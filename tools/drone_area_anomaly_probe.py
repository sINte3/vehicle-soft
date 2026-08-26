# -*- coding: utf-8 -*-
"""DRONE-COVERAGE-001, этап A2: аудит применимости данных DJI по всему парку.

    python tools/drone_area_anomaly_probe.py --db C:\\backups\\transport_2026-08-26.db ^
        --out "A2 аудит парка 2026-08-26.xlsx"

ТОЛЬКО ЧТЕНИЕ. База открывается через file:-URI с `mode=ro`, ни одной пишущей
команды в файле нет, отсутствующая база не создаётся (код возврата 2). Скрипт
не импортирует ни `app`, ни `models`, ни SQLAlchemy: `app = create_app()`
вызывает `db.create_all()` на импорте и превращает читателя в писателя.

**Запускать на КОПИИ, а не на рабочей базе.** Копия снимается штатным
`backup_production_db.bat`. Причина не в записи -- её здесь нет, -- а в том,
что чтение живой базы под нагрузкой даёт несогласованный срез: часть строк
прочитана до чужого коммита, часть после.

Что измеряется и зачем
----------------------
Три вопроса, от которых зависит, имеет ли смысл этап B (сбор маршрутов).

1. **Повторы площади.** 05.06.2026 у машины №8 нашлась запись `622715275`:
   40 секунд, 13 точек маршрута, 108 метров пути -- и площадь 0.594 га, в
   точности равная площади предыдущего вылета. Один случай ничего не говорит о
   парке. Здесь считается, сколько таких по всей базе и на сколько гектаров.

   **Совпадение площади само по себе не является ошибкой DJI.** Два соседних
   вылета по одному заданию вполне могут дать одну и ту же площадь. Поэтому
   строка помечается `ANOMALY_CANDIDATE`, а не «ошибка», и разбивается на
   признаки, которые случайное совпадение объяснить труднее: нулевая для
   работы длительность, слишком короткий интервал, серия из трёх и более.

2. **Отсутствующая ширина захвата.** Без `spray_width` геометрическое покрытие
   не считается вовсе -- решение владельца от 2026-08-25: `DATA_UNAVAILABLE`,
   подстановка запрещена (ни медиана, ни паспорт, ни соседнее значение). Доля
   таких вылетов -- это доля парка, до которой этап B не дотянется никогда.

3. **Качество исходных данных.** Всё, что помешает расчёту: битый `raw_json`,
   невозможные времена, отрицательные величины, расхождение колонки и JSON.

Скрипт НИЧЕГО не исправляет. Только измеряет и показывает.

Что он НЕ делает и почему
-------------------------
Не подставляет ширину. Не объявляет кандидата ошибкой. Не назначает порогов
«допустимого процента» -- порог это решение владельца, а не свойство данных.
Не выводит машину из ника: см. раздел «Идентификатор борта» ниже.

Вывод в консоль -- только ASCII (кодировка консоли Windows). Кириллица уходит
в xlsx.
"""

import argparse
import collections
import datetime
import hashlib
import json
import os
import sqlite3
import sys

# Смещение показа. Дроны опрыскивают ночью, поэтому день считается по UTC+5:
# в UTC вылеты одной смены разъезжаются по двум датам. То же значение, что
# DRONE_DISPLAY_UTC_OFFSET в drones.py; здесь оно повторено, а не
# импортировано, потому что импорт drones.py тянет за собой приложение.
UTC_OFFSET_MINUTES = 300

# Колонки `drone_flights`, без которых аудит невозможен. Сверено с DDL
# migrate_drones_foundation_001.py, а не с models.py: на production схему
# создавала миграция.
REQUIRED_COLUMNS = (
    'id', 'dji_flight_id', 'drone_unit_id', 'nickname_raw', 'serial_number',
    'started_at', 'finished_at', 'work_seconds', 'area_ha', 'spray_width',
    'raw_json',
)

# Ключ полной площади в payload DJI. В базе лежит `area_ha` = m2 / 10000, и
# деление уже потеряло знаки: 5940.000029700001 превращается в
# 0.5940000029700001. Для сравнения «равна ли площадь предыдущей» берётся
# сырое значение, а колонка служит запасным источником и предметом сверки.
RAW_AREA_KEY = 'new_work_area'
RAW_WIDTH_KEY = 'spray_width'

# Длительность, ниже которой вылет не может быть настоящей работой. Не порог
# «допустимого», а признак для разбора: 40-секундная запись 622715275 попала
# бы сюда. Названо константой, чтобы число было видно, а не спрятано в коде.
SHORT_FLIGHT_SECONDS = 60

# Доля самых коротких интервалов между вылетами, которая считается «аномально
# коротким интервалом».
#
# [REASON]: порог НЕ выдуман -- он вычисляется из самих данных как 5-й
# процентиль интервалов между соседними вылетами той же машины, и полученное
# значение печатается в отчёте. Фиксированное число секунд здесь было бы
# правилом, взятым с потолка: интервал зависит от того, как быстро заправляют
# бак, а это разное на разных площадках.
SHORT_GAP_PERCENTILE = 5

# Минимальная длина серии одинаковых площадей, которую стоит показать отдельно.
RUN_MIN_LENGTH = 3

# На сколько вылетов назад искать равную площадь.
#
# [REASON]: задание формулирует правило как «совпадает с площадью ПРЕДЫДУЩЕГО
# по времени вылета». Проверено на известном случае -- правило его не ловит.
# 05.06.2026 у машины №8 порядок такой: 622715273 = 5940.0000297,
# 622715274 = 3293.3333 (ручной режим), 622715275 = 5940.0000297. То есть
# 622715275 повторяет площадь вылета ЧЕРЕЗ ОДИН, а непосредственно перед ним
# стоит вылет с другой площадью. Окно в один шаг дало бы ноль кандидатов на
# том самом случае, ради которого аудит затеян.
#
# Поэтому сравнение идёт по окну, а расстояние (`lag`) записывается в каждую
# строку и разделяется в отчёте: lag = 1 и lag >= 2 -- разные по
# правдоподобию события, и смешивать их нельзя. Значение 3 покрывает
# известный случай с запасом; больше не берётся, потому что чем шире окно,
# тем выше вероятность случайного совпадения.
DEFAULT_LOOKBACK = 3

# Русское название проблемы -> ASCII-код для консоли.
#
# [REASON]: консоль Windows кириллицу в этом проекте не печатает (правило
# устава), а «issue #1: 1 rows» не говорит ничего. Код даёт понятную строку
# в консоли, полное название остаётся в xlsx.
QUALITY_CODES = {
    'raw_json не разобрался': 'RAW_JSON_UNPARSABLE',
    'нет dji_flight_id': 'NO_FLIGHT_ID',
    'повторяющийся dji_flight_id': 'DUPLICATE_FLIGHT_ID',
    'нет или не разобрано время начала': 'NO_START_TIME',
    'время начала в будущем': 'START_IN_FUTURE',
    'время начала раньше 2024 года': 'START_TOO_EARLY',
    'конец раньше начала': 'END_BEFORE_START',
    'отрицательная длительность': 'NEGATIVE_DURATION',
    'нулевая длительность': 'ZERO_DURATION',
    'отрицательная площадь': 'NEGATIVE_AREA',
    'вылет не привязан к машине': 'NO_MACHINE',
    'площадь в колонке и в raw_json расходятся': 'AREA_COLUMN_VS_JSON',
    'ширина есть в raw_json, но колонка пуста': 'WIDTH_COLUMN_EMPTY',
    'ширина в колонке и в raw_json расходятся': 'WIDTH_COLUMN_VS_JSON',
    'вылет начался раньше конца предыдущего': 'OVERLAPPING_FLIGHTS',
}

EXIT_OK = 0
EXIT_PRECONDITION = 1
EXIT_NO_DB = 2


class ProbeError(Exception):
    """Аудит невозможен: нет базы, нет таблицы, нет обязательной колонки."""


# ─── Подключение ─────────────────────────────────────────────────────────────

def connect_read_only(db_path, immutable=False):
    """Соединение только на чтение. Отсутствующий файл -- ошибка, не пустая база.

    [REASON]: `sqlite3.connect(path)` СОЗДАЁТ файл, если его нет. Читатель,
    который молча создал пустую базу и отчитался «0 аномалий», хуже, чем
    падение: ноль выглядит как хорошая новость.

    `immutable=1` разрешён только для статической копии: он говорит SQLite,
    что файл никто не меняет, и отключает проверку журнала. На живой базе это
    портит чтение, поэтому по умолчанию выключен.
    """
    if not os.path.exists(db_path):
        raise ProbeError('database not found: %s' % db_path)
    quoted = db_path.replace('?', '%3f').replace('#', '%23')
    uri = 'file:%s?mode=ro' % quoted
    if immutable:
        uri += '&immutable=1'
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


# ─── Схема ───────────────────────────────────────────────────────────────────

def describe_schema(con):
    """Фактические колонки `drone_flights`, а не ожидаемые.

    Возвращает (список колонок, список отсутствующих обязательных).
    """
    rows = list(con.execute('PRAGMA table_info(drone_flights)'))
    if not rows:
        raise ProbeError(
            'table drone_flights not found -- is this a Vehicle Soft database?')
    present = [row['name'] for row in rows]
    missing = [name for name in REQUIRED_COLUMNS if name not in present]
    return present, missing


def machine_identity(con):
    """Чем в этой базе опознаётся борт. Измерение, а не пересказ модели.

    Задание требует установить, какой идентификатор действительно обозначает
    один и тот же планер, и не принимать отображаемое имя за технический ключ.
    Ответ по фактической схеме: **на строке вылета такого идентификатора
    нет**.

    * `serial_number` в payload DJI -- идентификатор ВЫЛЕТА, не машины. Здесь
      это перепроверяется на живых данных: доля различных значений к числу
      строк должна быть около единицы.
    * `nickname_raw` -- отображаемое имя с пульта. Оператор его меняет.
    * `drone_unit_id` -- РЕЗУЛЬТАТ разбора ника через `drone_nicknames`,
      единственная связь вылета с машиной. Наследует все слабости ника.
    * `hardware_id` (Body Code) -- настоящий идентификатор планера, но он
      лежит на `drone_units`, а в списке вылетов его нет вовсе. В
      `raw_json` его тоже нет: приёмник кладёт строку списка целиком, а
      список этого поля не содержит.
    """
    facts = {}
    total = con.execute('SELECT count(*) FROM drone_flights').fetchone()[0]
    facts['flights_total'] = total
    facts['distinct_serial_number'] = con.execute(
        'SELECT count(DISTINCT serial_number) FROM drone_flights '
        'WHERE serial_number IS NOT NULL').fetchone()[0]
    facts['serial_number_null'] = con.execute(
        'SELECT count(*) FROM drone_flights '
        'WHERE serial_number IS NULL').fetchone()[0]
    facts['distinct_nickname_raw'] = con.execute(
        'SELECT count(DISTINCT nickname_raw) FROM drone_flights '
        'WHERE nickname_raw IS NOT NULL').fetchone()[0]
    facts['distinct_drone_unit_id'] = con.execute(
        'SELECT count(DISTINCT drone_unit_id) FROM drone_flights '
        'WHERE drone_unit_id IS NOT NULL').fetchone()[0]
    facts['drone_unit_id_null'] = con.execute(
        'SELECT count(*) FROM drone_flights '
        'WHERE drone_unit_id IS NULL').fetchone()[0]

    # Один ник, указывающий на разные машины, -- признак того, что группировка
    # по нику дала бы неверный результат. Проверяется, а не предполагается.
    facts['nicknames_pointing_at_many_units'] = con.execute(
        'SELECT count(*) FROM ('
        '  SELECT nickname_raw FROM drone_flights '
        '  WHERE nickname_raw IS NOT NULL AND drone_unit_id IS NOT NULL '
        '  GROUP BY nickname_raw HAVING count(DISTINCT drone_unit_id) > 1)'
    ).fetchone()[0]

    facts['hardware_id_on_flight_row'] = False
    facts['hardware_id_in_raw_json'] = None  # заполняется при обходе строк

    try:
        facts['units_total'] = con.execute(
            'SELECT count(*) FROM drone_units').fetchone()[0]
        facts['units_with_hardware_id'] = con.execute(
            "SELECT count(*) FROM drone_units WHERE hardware_id IS NOT NULL "
            "AND hardware_id <> ''").fetchone()[0]
    except sqlite3.Error:
        facts['units_total'] = None
        facts['units_with_hardware_id'] = None
    return facts


# ─── Чтение строк ────────────────────────────────────────────────────────────

class FlightRow(object):
    """Одна строка вылета в форме, пригодной для арифметики."""

    __slots__ = ('row_id', 'dji_flight_id', 'unit_id', 'nickname', 'serial',
                 'started_at', 'finished_at', 'work_seconds', 'area_ha_col',
                 'width_col', 'raw_area_m2', 'raw_width', 'width_key_present',
                 'raw_ok', 'raw_error', 'raw_has_hardware_id', 'local_month',
                 'local_date')

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))

    @property
    def area_m2(self):
        """Площадь в м2 из наиболее точного доступного источника.

        Сначала `raw_json.new_work_area` (полная точность), затем колонка
        `area_ha` x 10000. Расхождение между ними считается отдельно.
        """
        if self.raw_area_m2 is not None:
            return self.raw_area_m2
        if self.area_ha_col is not None:
            return self.area_ha_col * 10000.0
        return None

    @property
    def duration_seconds(self):
        """Длительность: из `work_seconds`, иначе из разности времён."""
        if self.work_seconds is not None:
            return self.work_seconds
        if self.started_at is not None and self.finished_at is not None:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    @property
    def width_value(self):
        """Ширина из наиболее надёжного доступного источника, или None.

        [REASON]: раздельные `width_state` и `width_value` были дефектом,
        найденным собственным тестом: при неразобранном `raw_json` состояние
        определялось по КОЛОНКЕ, а значение бралось из JSON, где его нет, и
        отчёт падал на `round(None)`. Один источник на оба ответа исключает
        расхождение по построению.
        """
        if self.raw_ok and self.width_key_present:
            return self.raw_width
        if not self.raw_ok:
            return self.width_col
        return None

    @property
    def width_state(self):
        """Одно из семи состояний ширины захвата.

        `MISSING_KEY`  -- ключа spray_width нет в payload вовсе;
        `JSON_NULL`    -- ключ есть, значение null;
        `COLUMN_NULL`  -- raw_json не разобрался И колонка пуста;
        `MINUS_ONE`    -- ровно -1, наблюдаемый маркер «не записано»;
        `ZERO`         -- ровно 0, столь же непригоден для радиуса буфера;
        `NEGATIVE`     -- иное отрицательное;
        `PRESENT`      -- пригодное положительное значение.

        [REASON]: семь состояний, а не «есть/нет», потому что они означают
        разное для диагностики. `MISSING_KEY` -- смена контракта DJI;
        `JSON_NULL` -- DJI поле знает, но для этого вылета не записал;
        `COLUMN_NULL` -- разбирать нечего и запасного источника нет.
        """
        if self.raw_ok and not self.width_key_present:
            return 'MISSING_KEY'
        value = self.width_value
        if value is None:
            return 'JSON_NULL' if self.raw_ok else 'COLUMN_NULL'
        if value == -1:
            return 'MINUS_ONE'
        if value == 0:
            return 'ZERO'
        if value < 0:
            return 'NEGATIVE'
        return 'PRESENT'

    @property
    def width_usable(self):
        return self.width_state == 'PRESENT'


def parse_datetime(value):
    """SQLite DATETIME -> naive datetime, None когда разобрать нельзя."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('T', ' ')
    if text.endswith('Z'):
        text = text[:-1]
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
                '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def to_local(moment):
    if moment is None:
        return None
    return moment + datetime.timedelta(minutes=UTC_OFFSET_MINUTES)


def load_rows(con):
    """Все вылеты в память. Ошибка разбора одной строки не прекращает обход.

    [REASON]: 31 тысяча строк -- это десятки мегабайт с `raw_json`, что для
    разового аудита приемлемо и много проще, чем оконные функции в SQL, числа
    которых нечем проверить тестом. Арифметика здесь на Python и покрыта
    тестами; SQL остаётся простым SELECT.
    """
    rows = []
    for record in con.execute(
            'SELECT id, dji_flight_id, drone_unit_id, nickname_raw, '
            '       serial_number, started_at, finished_at, work_seconds, '
            '       area_ha, spray_width, raw_json '
            'FROM drone_flights ORDER BY started_at, id'):
        raw_ok = True
        raw_error = None
        raw_area = None
        raw_width = None
        width_key_present = False
        raw_has_hw = False
        try:
            payload = json.loads(record['raw_json'])
            if not isinstance(payload, dict):
                raise ValueError('raw_json is not an object')
            if RAW_AREA_KEY in payload:
                value = payload[RAW_AREA_KEY]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    raw_area = float(value)
            width_key_present = RAW_WIDTH_KEY in payload
            if width_key_present:
                value = payload[RAW_WIDTH_KEY]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    raw_width = float(value)
            raw_has_hw = 'hardware_id' in payload
        except Exception as exc:          # noqa: BLE001 -- измеряем, не чиним
            raw_ok = False
            raw_error = '%s: %s' % (type(exc).__name__, exc)

        started = parse_datetime(record['started_at'])
        finished = parse_datetime(record['finished_at'])
        local_started = to_local(started)
        rows.append(FlightRow(
            row_id=record['id'],
            dji_flight_id=record['dji_flight_id'],
            unit_id=record['drone_unit_id'],
            nickname=record['nickname_raw'],
            serial=record['serial_number'],
            started_at=started,
            finished_at=finished,
            work_seconds=record['work_seconds'],
            area_ha_col=record['area_ha'],
            width_col=record['spray_width'],
            raw_area_m2=raw_area,
            raw_width=raw_width,
            width_key_present=width_key_present,
            raw_ok=raw_ok,
            raw_error=raw_error,
            raw_has_hardware_id=raw_has_hw,
            local_month=(local_started.strftime('%Y-%m')
                         if local_started else None),
            local_date=(local_started.date().isoformat()
                        if local_started else None),
        ))
    return rows


# ─── 4.1 Повторы площади ─────────────────────────────────────────────────────

def percentile(values, pct):
    """Процентиль по ближайшему рангу. Пустой вход -> None."""
    if not values:
        return None
    ordered = sorted(values)
    index = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[max(0, min(index, len(ordered) - 1))]


def group_key(row):
    """Ключ группировки «один борт».

    `drone_unit_id`, а не ник: ник это отображаемое имя. Вылеты без машины
    группируются отдельным ключом и НЕ смешиваются в одну кучу с чужими --
    ('unresolved', ник) сохраняет различие между спеллингами.
    """
    if row.unit_id is not None:
        return ('unit', row.unit_id)
    return ('unresolved', row.nickname or '')


def find_repeats(rows, lookback=DEFAULT_LOOKBACK):
    """Кандидаты «площадь равна площади одного из недавних вылетов той же машины».

    Возвращает (список кандидатов, статистика интервалов).

    Кандидат -- это `ANOMALY_CANDIDATE`, а не ошибка: два вылета по одному
    заданию могут дать одну площадь законно. Признаки, сужающие круг,
    считаются отдельно и не складываются в один вердикт.

    `lag` -- на сколько вылетов назад нашлось совпадение. Ближайшее
    совпадение выигрывает: если площадь равна и предыдущему, и позапрошлому,
    записывается lag = 1.
    """
    by_machine = collections.defaultdict(list)
    for row in rows:
        if row.started_at is None:
            continue
        by_machine[group_key(row)].append(row)

    gaps = []
    negative_gap_rows = []
    for machine_rows in by_machine.values():
        machine_rows.sort(key=lambda item: (item.started_at, item.row_id))
        for index in range(1, len(machine_rows)):
            previous, current = machine_rows[index - 1], machine_rows[index]
            end = previous.finished_at or previous.started_at
            if end is None or current.started_at is None:
                continue
            gap = (current.started_at - end).total_seconds()
            if gap < 0:
                # Вылет начался раньше, чем закончился предыдущий. В
                # процентиль такие не идут -- иначе горстка накладок утянет
                # порог ниже нуля и признак «короткий интервал» перестанет
                # означать что-либо. Считаются отдельно, как дефект данных.
                negative_gap_rows.append(current)
                continue
            gaps.append(gap)

    short_gap_threshold = percentile(gaps, SHORT_GAP_PERCENTILE)

    candidates = []
    for key, machine_rows in by_machine.items():
        run_length = 1
        for index in range(1, len(machine_rows)):
            current = machine_rows[index]
            area_now = current.area_m2
            immediate = machine_rows[index - 1].area_m2

            # Серия считается только по непосредственным соседям: три подряд
            # одинаковых -- это серия, а совпадение через один -- нет.
            if (area_now is not None and immediate is not None
                    and area_now == immediate):
                run_length += 1
            else:
                run_length = 1

            if area_now is None or area_now == 0:
                # Нулевая площадь совпадает у соседей сплошь и рядом; это не
                # тот случай, ради которого затеян разбор.
                continue

            match = None
            for lag in range(1, min(lookback, index) + 1):
                candidate_previous = machine_rows[index - lag]
                if candidate_previous.area_m2 == area_now:
                    match = (lag, candidate_previous)
                    break
            if match is None:
                continue
            lag, previous = match

            end = previous.finished_at or previous.started_at
            gap = None
            if end is not None and current.started_at is not None:
                gap = (current.started_at - end).total_seconds()
            duration = current.duration_seconds
            candidates.append({
                'machine': key,
                'dji_flight_id': current.dji_flight_id,
                'previous_dji_flight_id': previous.dji_flight_id,
                'lag': lag,
                'local_date': current.local_date,
                'local_month': current.local_month,
                'nickname': current.nickname,
                'unit_id': current.unit_id,
                'area_m2': area_now,
                'area_ha': area_now / 10000.0,
                'duration_seconds': duration,
                'gap_seconds': gap,
                'run_length': run_length,
                'short_flight': (duration is not None
                                 and duration < SHORT_FLIGHT_SECONDS),
                'short_gap': (gap is not None and gap >= 0
                              and short_gap_threshold is not None
                              and gap <= short_gap_threshold),
            })
    return candidates, {
        'gap_sample_size': len(gaps),
        'short_gap_threshold_seconds': short_gap_threshold,
        'short_gap_percentile': SHORT_GAP_PERCENTILE,
        'lookback': lookback,
        'negative_gap_rows': negative_gap_rows,
    }


# ─── 4.2 Ширина ──────────────────────────────────────────────────────────────

def width_report(rows):
    states = collections.Counter()
    area_by_state = collections.Counter()
    by_month = collections.defaultdict(
        lambda: {'flights': 0, 'usable': 0, 'area_ha': 0.0,
                 'unusable_area_ha': 0.0})
    by_machine = collections.defaultdict(
        lambda: {'flights': 0, 'usable': 0, 'area_ha': 0.0,
                 'unusable_area_ha': 0.0, 'nickname': None})
    distinct_widths = collections.Counter()

    for row in rows:
        state = row.width_state
        states[state] += 1
        area = row.area_m2 or 0.0
        area_by_state[state] += area
        if row.width_usable:
            distinct_widths[round(row.width_value, 4)] += 1

        month = by_month[row.local_month or '(нет даты)']
        month['flights'] += 1
        month['area_ha'] += area / 10000.0
        if row.width_usable:
            month['usable'] += 1
        else:
            month['unusable_area_ha'] += area / 10000.0

        key = group_key(row)
        machine = by_machine[key]
        machine['flights'] += 1
        machine['area_ha'] += area / 10000.0
        machine['nickname'] = machine['nickname'] or row.nickname
        if row.width_usable:
            machine['usable'] += 1
        else:
            machine['unusable_area_ha'] += area / 10000.0

    months_with_any_width = sorted(
        month for month, data in by_month.items()
        if data['usable'] > 0 and month != '(нет даты)')
    months_without_any_width = sorted(
        month for month, data in by_month.items()
        if data['usable'] == 0 and data['flights'] > 0
        and month != '(нет даты)')

    return {
        'states': dict(states),
        'area_ha_by_state': {name: value / 10000.0
                             for name, value in area_by_state.items()},
        'by_month': dict(by_month),
        'by_machine': dict(by_machine),
        'distinct_widths': dict(distinct_widths),
        'first_month_with_width': (months_with_any_width[0]
                                   if months_with_any_width else None),
        'last_month_with_width': (months_with_any_width[-1]
                                  if months_with_any_width else None),
        'months_without_any_width': months_without_any_width,
    }


# ─── 4.3 Качество данных ─────────────────────────────────────────────────────

def quality_report(rows, con):
    issues = collections.Counter()
    samples = collections.defaultdict(list)

    def note(kind, row, detail=''):
        issues[kind] += 1
        if len(samples[kind]) < 20:
            samples[kind].append({
                'dji_flight_id': row.dji_flight_id,
                'row_id': row.row_id,
                'local_date': row.local_date,
                'nickname': row.nickname,
                'detail': detail,
            })

    now_limit = datetime.datetime.utcnow() + datetime.timedelta(days=2)
    earliest = datetime.datetime(2024, 1, 1)

    for row in rows:
        if not row.raw_ok:
            note('raw_json не разобрался', row, row.raw_error or '')
        if row.dji_flight_id is None:
            note('нет dji_flight_id', row)
        if row.started_at is None:
            note('нет или не разобрано время начала', row)
        else:
            if row.started_at > now_limit:
                note('время начала в будущем', row, str(row.started_at))
            if row.started_at < earliest:
                note('время начала раньше 2024 года', row, str(row.started_at))
        if (row.started_at is not None and row.finished_at is not None
                and row.finished_at < row.started_at):
            note('конец раньше начала', row)
        duration = row.duration_seconds
        if duration is not None and duration < 0:
            note('отрицательная длительность', row, str(duration))
        elif duration == 0:
            note('нулевая длительность', row)
        area = row.area_m2
        if area is not None and area < 0:
            note('отрицательная площадь', row, str(area))
        if row.unit_id is None:
            note('вылет не привязан к машине', row, row.nickname or '')
        # Расхождение колонки и JSON. Колонка -- это m2/10000, поэтому
        # сравнение ведётся в гектарах с допуском на двоичное представление.
        if row.raw_area_m2 is not None and row.area_ha_col is not None:
            expected = row.raw_area_m2 / 10000.0
            if abs(expected - row.area_ha_col) > 1e-9:
                note('площадь в колонке и в raw_json расходятся', row,
                     'колонка %.10f, json %.10f' % (row.area_ha_col, expected))
        # Ширина в колонке против ширины в JSON.
        if row.raw_ok and row.width_key_present and row.raw_width is not None:
            if row.width_col is None:
                note('ширина есть в raw_json, но колонка пуста', row,
                     str(row.raw_width))
            elif abs(row.width_col - row.raw_width) > 1e-9:
                note('ширина в колонке и в raw_json расходятся', row,
                     'колонка %s, json %s' % (row.width_col, row.raw_width))

    duplicates = con.execute(
        'SELECT count(*) FROM (SELECT dji_flight_id FROM drone_flights '
        'GROUP BY dji_flight_id HAVING count(*) > 1)').fetchone()[0]
    if duplicates:
        issues['повторяющийся dji_flight_id'] = duplicates

    return {'issues': dict(issues), 'samples': dict(samples)}


# ─── Сводка ──────────────────────────────────────────────────────────────────

def analyse(con, lookback=DEFAULT_LOOKBACK):
    """Вся арифметика аудита, без openpyxl -- числа проверяются тестами."""
    present, missing = describe_schema(con)
    if missing:
        raise ProbeError('drone_flights is missing required column(s): %s'
                         % ', '.join(missing))

    identity = machine_identity(con)
    rows = load_rows(con)
    candidates, gap_stats = find_repeats(rows, lookback=lookback)
    widths = width_report(rows)
    quality = quality_report(rows, con)

    # Накладка времён -- дефект данных, а не признак повтора площади.
    if gap_stats['negative_gap_rows']:
        quality['issues']['вылет начался раньше конца предыдущего'] = len(
            gap_stats['negative_gap_rows'])
        quality['samples']['вылет начался раньше конца предыдущего'] = [
            {'dji_flight_id': row.dji_flight_id, 'row_id': row.row_id,
             'local_date': row.local_date, 'nickname': row.nickname,
             'detail': ''}
            for row in gap_stats['negative_gap_rows'][:20]]
    gap_stats = {name: value for name, value in gap_stats.items()
                 if name != 'negative_gap_rows'}

    identity['hardware_id_in_raw_json'] = sum(
        1 for row in rows if row.raw_has_hardware_id)

    total_area_ha = sum((row.area_m2 or 0.0) for row in rows) / 10000.0
    candidate_area_ha = sum(item['area_ha'] for item in candidates)

    by_month = collections.defaultdict(
        lambda: {'flights': 0, 'area_ha': 0.0, 'repeat_flights': 0,
                 'repeat_area_ha': 0.0})
    for row in rows:
        month = by_month[row.local_month or '(нет даты)']
        month['flights'] += 1
        month['area_ha'] += (row.area_m2 or 0.0) / 10000.0
    for item in candidates:
        month = by_month[item['local_month'] or '(нет даты)']
        month['repeat_flights'] += 1
        month['repeat_area_ha'] += item['area_ha']

    by_machine = collections.defaultdict(
        lambda: {'flights': 0, 'area_ha': 0.0, 'repeat_flights': 0,
                 'repeat_area_ha': 0.0, 'nickname': None})
    for row in rows:
        machine = by_machine[group_key(row)]
        machine['flights'] += 1
        machine['area_ha'] += (row.area_m2 or 0.0) / 10000.0
        machine['nickname'] = machine['nickname'] or row.nickname
    for item in candidates:
        machine = by_machine[item['machine']]
        machine['repeat_flights'] += 1
        machine['repeat_area_ha'] += item['area_ha']

    usable = widths['states'].get('PRESENT', 0)
    usable_area_ha = widths['area_ha_by_state'].get('PRESENT', 0.0)

    return {
        'schema_columns': present,
        'identity': identity,
        'flights_total': len(rows),
        'total_area_ha': total_area_ha,
        'repeats': {
            'candidates': candidates,
            'count': len(candidates),
            'area_ha': candidate_area_ha,
            'share_of_flights': (len(candidates) / len(rows)) if rows else 0.0,
            'share_of_area': (candidate_area_ha / total_area_ha)
                             if total_area_ha else 0.0,
            'short_flight_count': sum(1 for item in candidates
                                      if item['short_flight']),
            'short_gap_count': sum(1 for item in candidates
                                   if item['short_gap']),
            'runs_3plus_count': sum(1 for item in candidates
                                    if item['run_length'] >= RUN_MIN_LENGTH),
            'lag1_count': sum(1 for item in candidates if item['lag'] == 1),
            'lag2plus_count': sum(1 for item in candidates if item['lag'] >= 2),
            'lag1_area_ha': sum(item['area_ha'] for item in candidates
                                if item['lag'] == 1),
            'lag2plus_area_ha': sum(item['area_ha'] for item in candidates
                                    if item['lag'] >= 2),
            'gap_stats': gap_stats,
        },
        'widths': widths,
        'width_usable_flights': usable,
        'width_usable_share': (usable / len(rows)) if rows else 0.0,
        'width_usable_area_ha': usable_area_ha,
        'width_usable_area_share': (usable_area_ha / total_area_ha)
                                   if total_area_ha else 0.0,
        'quality': quality,
        'by_month': dict(by_month),
        'by_machine': dict(by_machine),
    }


def check_known_case(result, dji_flight_id=622715275):
    """Найден ли известный случай из DISCOVERY §6.2.

    [REASON]: задание требует не продолжать молча, если он не найден.
    Отсутствие означает одно из трёх, и все три меняют доверие к аудиту:
    в базе нет этого вылета, правило сравнения работает не так, как в разборе,
    или площадь в базе отличается от площади в снимке. Возвращается словарь,
    и вызывающий обязан на него посмотреть.
    """
    found = [item for item in result['repeats']['candidates']
             if item['dji_flight_id'] == dji_flight_id]
    return {
        'dji_flight_id': dji_flight_id,
        'found_as_candidate': bool(found),
        'detail': found[0] if found else None,
    }


# ─── Вывод ───────────────────────────────────────────────────────────────────

def machine_label(key, nickname):
    kind, value = key
    if kind == 'unit':
        return 'машина id=%s (%s)' % (value, nickname or '?')
    return 'без машины: %s' % (value or '(пустой ник)')


def print_console_summary(result, known, db_path, digest_before):
    """ASCII only -- консоль Windows."""
    print('=' * 72)
    print('A2 fleet audit  --  READ ONLY')
    print('database : %s' % db_path)
    print('sha256   : %s' % digest_before)
    print('=' * 72)
    print('flights total          : %d' % result['flights_total'])
    print('area total, ha         : %.2f' % result['total_area_ha'])
    print('')
    print('-- width of swath ------------------------------------------------')
    for state in ('PRESENT', 'MINUS_ONE', 'JSON_NULL', 'MISSING_KEY', 'ZERO',
                  'NEGATIVE', 'COLUMN_NULL'):
        count = result['widths']['states'].get(state, 0)
        if count:
            area = result['widths']['area_ha_by_state'].get(state, 0.0)
            print('  %-12s %8d flights  %12.2f ha' % (state, count, area))
    print('  usable for geometry  : %d flights (%.1f%%), %.2f ha (%.1f%%)'
          % (result['width_usable_flights'],
             100.0 * result['width_usable_share'],
             result['width_usable_area_ha'],
             100.0 * result['width_usable_area_share']))
    print('')
    print('-- repeated area (ANOMALY_CANDIDATE, not an error) ---------------')
    repeats = result['repeats']
    print('  candidates           : %d (%.2f%% of flights)'
          % (repeats['count'], 100.0 * repeats['share_of_flights']))
    print('  area of candidates   : %.2f ha (%.2f%% of all hectares)'
          % (repeats['area_ha'], 100.0 * repeats['share_of_area']))
    print('  of them shorter than %ds : %d'
          % (SHORT_FLIGHT_SECONDS, repeats['short_flight_count']))
    threshold = repeats['gap_stats']['short_gap_threshold_seconds']
    print('  of them after a short gap (<= %s s, p%d of %d gaps) : %d'
          % ('n/a' if threshold is None else '%.0f' % threshold,
             SHORT_GAP_PERCENTILE, repeats['gap_stats']['gap_sample_size'],
             repeats['short_gap_count']))
    print('  of them in a run of %d+ : %d'
          % (RUN_MIN_LENGTH, repeats['runs_3plus_count']))
    print('  match with the immediately previous flight (lag 1) : %d, %.2f ha'
          % (repeats['lag1_count'], repeats['lag1_area_ha']))
    print('  match further back (lag 2..%d)                     : %d, %.2f ha'
          % (repeats['gap_stats']['lookback'], repeats['lag2plus_count'],
             repeats['lag2plus_area_ha']))
    print('')
    print('  known case %d : %s'
          % (known['dji_flight_id'],
             'FOUND' if known['found_as_candidate'] else 'NOT FOUND'))
    if not known['found_as_candidate']:
        print('  !! the known case is absent. Do not read the numbers above')
        print('     as final until the reason is established: the flight may')
        print('     be missing from this database, or the comparison rule may')
        print('     differ from the one used in DISCOVERY 6.2.')
    print('')
    print('-- data quality --------------------------------------------------')
    if not result['quality']['issues']:
        print('  no issues found')
    for kind, count in sorted(result['quality']['issues'].items(),
                              key=lambda pair: -pair[1]):
        print('  %-24s %6d rows' % (QUALITY_CODES.get(kind, 'OTHER'), count))
    print('')
    print('-- machine identity ----------------------------------------------')
    identity = result['identity']
    print('  distinct serial_number / flights : %d / %d  '
          '(serial is a FLIGHT id, not a machine id)'
          % (identity['distinct_serial_number'], identity['flights_total']))
    print('  flights with no machine resolved : %d'
          % identity['drone_unit_id_null'])
    print('  nicknames pointing at >1 machine : %d'
          % identity['nicknames_pointing_at_many_units'])
    print('  hardware_id present in raw_json  : %d rows'
          % (identity['hardware_id_in_raw_json'] or 0))
    print('=' * 72)


def write_xlsx(result, known, path, db_path, digest_before, digest_after):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical='top')
    book = Workbook()

    def sheet(title, headers, widths):
        ws = book.create_sheet(title)
        ws.append(headers)
        for cell in ws[1]:
            cell.font = bold
        for index, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(index)].width = width
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = 'A1:%s1' % get_column_letter(len(headers))
        return ws

    # --- Сводка --------------------------------------------------------------
    ws = book.active
    ws.title = 'Сводка'
    ws.column_dimensions['A'].width = 58
    ws.column_dimensions['B'].width = 26
    ws.column_dimensions['C'].width = 60
    repeats = result['repeats']
    threshold = repeats['gap_stats']['short_gap_threshold_seconds']
    lines = [
        ('DRONE-COVERAGE-001, этап A2 — аудит парка', '', ''),
        ('Режим', 'только чтение', 'file:...?mode=ro, пишущих команд нет'),
        ('База (копия)', os.path.basename(db_path), db_path),
        ('SHA-256 до анализа', digest_before, ''),
        ('SHA-256 после анализа', digest_after,
         'совпадение доказывает, что файл не изменён'),
        ('', '', ''),
        ('Вылетов всего', result['flights_total'], ''),
        ('Гектаров всего (по данным DJI)', round(result['total_area_ha'], 2),
         'сумма new_work_area / 10000'),
        ('', '', ''),
        ('ШИРИНА ЗАХВАТА', '', ''),
        ('Вылетов с пригодной шириной', result['width_usable_flights'],
         'только для них возможен геометрический расчёт'),
        ('Доля вылетов с шириной',
         round(100.0 * result['width_usable_share'], 2),
         'процентов'),
        ('Гектаров с пригодной шириной',
         round(result['width_usable_area_ha'], 2), ''),
        ('Доля гектаров с шириной',
         round(100.0 * result['width_usable_area_share'], 2), 'процентов'),
        ('Гектаров DATA_UNAVAILABLE',
         round(result['total_area_ha'] - result['width_usable_area_ha'], 2),
         'подстановка ширины запрещена решением владельца 2026-08-25'),
        ('', '', ''),
        ('ПОВТОРЫ ПЛОЩАДИ (ANOMALY_CANDIDATE)', '',
         'совпадение само по себе не ошибка DJI'),
        ('Кандидатов', repeats['count'], ''),
        ('Доля от вылетов', round(100.0 * repeats['share_of_flights'], 3),
         'процентов'),
        ('Гектаров у кандидатов', round(repeats['area_ha'], 2), ''),
        ('Доля от гектаров', round(100.0 * repeats['share_of_area'], 3),
         'процентов'),
        ('Из них короче %d с' % SHORT_FLIGHT_SECONDS,
         repeats['short_flight_count'], ''),
        ('Из них после короткого интервала', repeats['short_gap_count'],
         ('порог %.0f с — %d-й процентиль интервалов, вычислен по этим же '
          'данным' % (threshold, SHORT_GAP_PERCENTILE))
         if threshold is not None else 'интервалов не набралось'),
        ('Из них в серии из %d и более' % RUN_MIN_LENGTH,
         repeats['runs_3plus_count'], ''),
        ('Совпадение с непосредственно предыдущим (lag 1)',
         repeats['lag1_count'],
         'гектаров: %.2f' % repeats['lag1_area_ha']),
        ('Совпадение через один и дальше (lag 2..%d)'
         % repeats['gap_stats']['lookback'], repeats['lag2plus_count'],
         'гектаров: %.2f. Известный случай 622715275 именно такой'
         % repeats['lag2plus_area_ha']),
        ('', '', ''),
        ('Известный случай %d' % known['dji_flight_id'],
         'НАЙДЕН' if known['found_as_candidate'] else 'НЕ НАЙДЕН',
         'DISCOVERY §6.2; если не найден — числа выше нельзя считать '
         'окончательными, пока причина не установлена'),
    ]
    for row in lines:
        ws.append(list(row))
    for cell in ws['A']:
        cell.font = bold
    for cell in ws['C']:
        cell.alignment = wrap

    # --- По месяцам ----------------------------------------------------------
    ws = sheet('По месяцам',
               ['Месяц (UTC+5)', 'Вылетов', 'Гектаров', 'С шириной',
                'Доля с шириной, %', 'Гектаров без ширины',
                'Кандидатов повтора', 'Гектаров у кандидатов'],
               [16, 12, 14, 12, 20, 22, 20, 22])
    months = sorted(set(list(result['by_month'].keys())
                        + list(result['widths']['by_month'].keys())))
    for month in months:
        base = result['by_month'].get(
            month, {'flights': 0, 'area_ha': 0.0, 'repeat_flights': 0,
                    'repeat_area_ha': 0.0})
        width = result['widths']['by_month'].get(
            month, {'flights': 0, 'usable': 0, 'unusable_area_ha': 0.0})
        share = (100.0 * width['usable'] / width['flights']
                 if width['flights'] else 0.0)
        ws.append([month, base['flights'], round(base['area_ha'], 2),
                   width['usable'], round(share, 1),
                   round(width['unusable_area_ha'], 2),
                   base['repeat_flights'], round(base['repeat_area_ha'], 2)])

    # --- По дронам -----------------------------------------------------------
    ws = sheet('По дронам',
               ['Машина', 'Ник (первый встреченный)', 'Вылетов', 'Гектаров',
                'С шириной', 'Доля с шириной, %', 'Гектаров без ширины',
                'Кандидатов повтора', 'Гектаров у кандидатов'],
               [26, 26, 12, 14, 12, 20, 22, 20, 22])
    for key in sorted(result['by_machine'], key=lambda item: (item[0], str(item[1]))):
        base = result['by_machine'][key]
        width = result['widths']['by_machine'].get(
            key, {'flights': 0, 'usable': 0, 'unusable_area_ha': 0.0})
        share = (100.0 * width['usable'] / width['flights']
                 if width['flights'] else 0.0)
        ws.append([machine_label(key, base['nickname']), base['nickname'] or '',
                   base['flights'], round(base['area_ha'], 2),
                   width['usable'], round(share, 1),
                   round(width['unusable_area_ha'], 2),
                   base['repeat_flights'], round(base['repeat_area_ha'], 2)])

    # --- Повторы площади -----------------------------------------------------
    ws = sheet('Повторы площади',
               ['Дата (UTC+5)', 'Машина', 'Ник', 'dji_flight_id',
                'Совпало с dji_flight_id', 'Вылетов назад (lag)',
                'Площадь, га', 'Длительность, с',
                'Интервал, с', 'Длина серии',
                'Короткий вылет', 'Короткий интервал'],
               [14, 24, 20, 18, 24, 20, 14, 18, 18, 14, 16, 18])
    for item in sorted(result['repeats']['candidates'],
                       key=lambda entry: (entry['local_date'] or '',
                                          entry['dji_flight_id'] or 0)):
        ws.append([
            item['local_date'] or '',
            machine_label(item['machine'], item['nickname']),
            item['nickname'] or '',
            item['dji_flight_id'], item['previous_dji_flight_id'],
            item['lag'],
            round(item['area_ha'], 4),
            '' if item['duration_seconds'] is None else round(item['duration_seconds']),
            '' if item['gap_seconds'] is None else round(item['gap_seconds']),
            item['run_length'],
            'да' if item['short_flight'] else '',
            'да' if item['short_gap'] else '',
        ])

    # --- Нет ширины ----------------------------------------------------------
    ws = sheet('Нет ширины',
               ['Состояние', 'Что это значит', 'Вылетов', 'Гектаров DJI'],
               [16, 62, 12, 16])
    meanings = {
        'PRESENT': 'ширина есть и пригодна — геометрию посчитать можно',
        'MINUS_ONE': 'DJI прислал -1 — наблюдаемый маркер «не записано»',
        'JSON_NULL': 'ключ есть, значение null — DJI поле знает, но не записал',
        'MISSING_KEY': 'ключа spray_width в payload нет вовсе — смена контракта DJI',
        'ZERO': 'ровно 0 — как радиус буфера непригоден так же, как -1',
        'NEGATIVE': 'иное отрицательное значение',
        'COLUMN_NULL': 'колонка пуста при неразобранном raw_json',
    }
    for state, count in sorted(result['widths']['states'].items(),
                               key=lambda pair: -pair[1]):
        ws.append([state, meanings.get(state, ''), count,
                   round(result['widths']['area_ha_by_state'].get(state, 0.0), 2)])
    ws.append([])
    ws.append(['Различные значения ширины среди пригодных', '', '', ''])
    ws.append(['Ширина, м', 'Вылетов', '', ''])
    for value, count in sorted(result['widths']['distinct_widths'].items()):
        ws.append([value, count, '', ''])
    ws.append([])
    ws.append(['Первый месяц, где ширина встречается',
               result['widths']['first_month_with_width'] or '—', '', ''])
    ws.append(['Последний месяц, где ширина встречается',
               result['widths']['last_month_with_width'] or '—', '', ''])
    ws.append(['Месяцы, где ширины нет ни у одного вылета',
               ', '.join(result['widths']['months_without_any_width']) or '—',
               '', ''])

    # --- Качество данных -----------------------------------------------------
    ws = sheet('Качество данных',
               ['Проблема', 'Код', 'Строк', 'Примеры dji_flight_id'],
               [56, 24, 12, 60])
    if not result['quality']['issues']:
        ws.append(['проблем не найдено', '', 0, ''])
    for kind, count in sorted(result['quality']['issues'].items(),
                              key=lambda pair: -pair[1]):
        examples = ', '.join(
            str(sample['dji_flight_id'])
            for sample in result['quality']['samples'].get(kind, [])[:10])
        ws.append([kind, QUALITY_CODES.get(kind, 'OTHER'), count, examples])
    for cell in ws['D']:
        cell.alignment = wrap

    # --- Методика ------------------------------------------------------------
    ws = book.create_sheet('Методика')
    ws.column_dimensions['A'].width = 110
    method = [
        'DRONE-COVERAGE-001, этап A2. Методика и её границы.',
        '',
        'РЕЖИМ. База открыта через file:-URI с mode=ro. Пишущих SQL-команд в '
        'скрипте нет. SHA-256 файла до и после анализа приведён на листе '
        '«Сводка» — совпадение и есть доказательство неизменности.',
        '',
        'ИСТОЧНИК ПЛОЩАДИ. Берётся new_work_area из raw_json (полная '
        'точность). Колонка area_ha равна m2/10000 и знаки уже потеряла: '
        '5940.000029700001 превращается в 0.5940000029700001. Когда raw_json '
        'не разобрался, используется колонка, а расхождение между источниками '
        'считается отдельно на листе «Качество данных».',
        '',
        'ГРУППИРОВКА ПО БОРТУ. На строке вылета технического идентификатора '
        'планера НЕТ. serial_number в payload DJI — это идентификатор ВЫЛЕТА '
        '(проверяется на листе «Сводка»: число различных значений близко к '
        'числу строк). nickname_raw — отображаемое имя с пульта, оператор его '
        'меняет. Единственная связь вылета с машиной — drone_unit_id, а он '
        'получен разбором ника через drone_nicknames и наследует все слабости '
        'ника. hardware_id (Body Code) — настоящий идентификатор планера, но '
        'он лежит на drone_units, а в списке вылетов его нет вовсе. Поэтому '
        'группировка ведётся по drone_unit_id, вылеты без машины вынесены '
        'отдельными строками и НЕ ссыпаны в одну кучу.',
        '',
        'ПОВТОР ПЛОЩАДИ. Кандидат — вылет, у которого площадь ТОЧНО равна '
        'площади одного из %d предыдущих вылетов той же машины, и она не '
        'ноль. Это ANOMALY_CANDIDATE, а не ошибка DJI: два вылета по одному '
        'заданию могут дать одну площадь законно. Признаки, сужающие круг, '
        'считаются отдельно и НЕ складываются в один вердикт.' % DEFAULT_LOOKBACK,
        '',
        'ПОЧЕМУ ОКНО, А НЕ ОДИН ШАГ. Задание формулировало правило как '
        '«совпадает с предыдущим по времени вылетом». На известном случае оно '
        'не срабатывает: 05.06.2026 у машины №8 между двумя вылетами по '
        '5940.0000297 м2 стоит вылет 622715274 на 3293.3333 м2 в ручном '
        'режиме, то есть 622715275 повторяет площадь вылета ЧЕРЕЗ ОДИН. Окно '
        'в один шаг дало бы ноль кандидатов на том самом случае, ради '
        'которого аудит затеян. Расстояние записано в колонке «Вылетов '
        'назад» и разделено в сводке: lag 1 и lag 2 и дальше — разные по '
        'правдоподобию события.',
        '',
        'КОРОТКИЙ ИНТЕРВАЛ. Порог не выдуман: это %d-й процентиль интервалов '
        'между соседними вылетами той же машины, вычисленный по этим же '
        'данным. Значение напечатано на листе «Сводка».' % SHORT_GAP_PERCENTILE,
        '',
        'КОРОТКИЙ ВЫЛЕТ. Длительность меньше %d секунд. Это признак для '
        'разбора, а не порог «допустимого».' % SHORT_FLIGHT_SECONDS,
        '',
        'ШИРИНА ЗАХВАТА. Шесть состояний, потому что они означают разное: '
        'MISSING_KEY — смена контракта DJI; JSON_NULL — DJI поле знает, но '
        'для этого вылета не записал; MINUS_ONE — наблюдаемый маркер «не '
        'записано»; ZERO и NEGATIVE — непригодны как радиус буфера; '
        'COLUMN_NULL — колонка пуста при неразобранном JSON.',
        '',
        'ЧЕГО ЗДЕСЬ НЕТ. Подстановки ширины — ни медианной, ни паспортной, ни '
        'соседней (решение владельца 2026-08-25: DATA_UNAVAILABLE). '
        'Назначенных порогов «допустимого процента» — порог это решение '
        'владельца, а не свойство данных. Исправлений: скрипт только измеряет.',
        '',
        'ОШИБКА В ОДНОЙ СТРОКЕ не прекращает анализ: она попадает в лист '
        '«Качество данных» и считается там.',
    ]
    for line in method:
        ws.append([line])
    for cell in ws['A']:
        cell.alignment = wrap

    book.save(path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='DRONE-COVERAGE-001 stage A2: read-only fleet audit of '
                    'DJI flight data. Never writes to the database.')
    parser.add_argument('--db', required=True,
                        help='path to a COPY of transport.db (never the live '
                             'file)')
    parser.add_argument('--out', default=None,
                        help='path of the xlsx report to write')
    parser.add_argument('--lookback', type=int, default=DEFAULT_LOOKBACK,
                        help='how many flights back to look for an equal '
                             'area (default %d). The known case 622715275 '
                             'repeats the area of the flight TWO back, so 1 '
                             'would miss it.' % DEFAULT_LOOKBACK)
    parser.add_argument('--immutable', action='store_true',
                        help='add immutable=1 to the URI. Correct ONLY for a '
                             'static backup copy, never for a live database')
    args = parser.parse_args(argv)

    try:
        digest_before = sha256_of(args.db)
    except OSError as exc:
        print('ERROR: cannot read %s (%s)' % (args.db, exc))
        return EXIT_NO_DB

    try:
        con = connect_read_only(args.db, immutable=args.immutable)
    except ProbeError as exc:
        print('ERROR: %s' % exc)
        return EXIT_NO_DB

    try:
        result = analyse(con, lookback=args.lookback)
    except ProbeError as exc:
        print('ERROR: %s' % exc)
        return EXIT_PRECONDITION
    finally:
        con.close()

    known = check_known_case(result)
    digest_after = sha256_of(args.db)
    print_console_summary(result, known, args.db, digest_before)

    if digest_before != digest_after:
        print('FATAL: the database file changed during the run. The report is')
        print('       not trustworthy and no xlsx was written.')
        return EXIT_PRECONDITION
    print('sha256 after : %s  (unchanged)' % digest_after)

    if args.out:
        written = write_xlsx(result, known, args.out, args.db,
                             digest_before, digest_after)
        print('report written: %s' % written)
    else:
        print('no --out given, xlsx not written')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
