# -*- coding: utf-8 -*-
"""GPS-16 -- пометка «не наша техника» по списку объектов, утверждённому владельцем.

ЗАЧЕМ
Решение владельца 01.10: «техника с пометкой «ф/х» - это техника фермеров.
Они нам не нужны. В нашей программе они не должны быть»; легковые и
малотоннажные -- исключить. 14.10 он повторил это по четырём фермерским
объектам, которых в Wialon уже нет, и попросил инвентаризацию: «удалить не
нужные, связать которые не связаны».

01.10 решение исполнено для объектов из списка сорока, который владелец
видел: строки добавлены на экране и связаны GPS-10 02.10. Остальные
фермерские объекты в тот список не попали, строки у них нет, а объект без
строки план-факт считает (`gps/exclusion.py`). Пометить их прежним путём --
по одной строке на экране и связкой GPS-10 -- можно, пока объект есть в
Wialon; удалённый из Wialon объект GPS-10 уже не найдёт, хотя его сутки на
экране остаются. За 02-12.10 так посчиталось 803 га у 50 фермерских объектов
(прогон 14.10).

ЧТО ДЕЛАЕТ
Для каждого id из файла `--ids` (столбец `wialon_id`) создаёт ту же строку,
какую дают «+ Добавить» -> «Нет в системе» и связка GPS-10 вместе:
`vialon_name` -- имя объекта, как его последним записал сборщик из списка
Wialon (`collector_units`), `wialon_id` -- этот id, `skip = 1`,
`equipment_id` пуст. С этой строкой расчёт объект больше не считает, а экран
«Факт по технике» и сверка заявок agro-work прячут его вместе с прошлыми
сутками.

Имя, которое у сборщика носит ещё хоть один объект, пишется с номером:
«<имя> (wialon <id>)». По имени строку находят импорт моточасов и экран
«+ Добавить»; строка «не наша» под общим именем объявила бы чужой и второй
объект -- а под именем с номером она касается только своего id.

ЧЕГО НЕ ДЕЛАЕТ
  Ничего не удаляет: ни строк, ни посчитанных суток, ни точек. Сутки объекта
      остаются в базе как были; их прячет экран.
  Не трогает существующие строки. Объект, у которого строка с этим id уже
      есть, и объект, чьё имя уже занято строкой сопоставления, идут в CSV, а
      не в базу; в колонке `primechanie` -- что с ним делать.
  Не решает, чья техника. Список составляет человек; инструмент проверяет,
      что каждый id ему известен (имя у сборщика или посчитанные сутки), и
      неизвестный id -- скорее опечатка -- отменяет прогон целиком.
  Не ставит `equipment_id` и не меняет категорий. Моточасам пометка не
      вредит: у объекта без строки их и так не было -- импорт находит машину
      только по строке сопоставления.

ОТКАТ
Номера созданных строк -- в колонке `mapping_id` CSV записи.
Данные, одна строка: на экране сопоставления Wialon (/wialon/mapping) --
значок корзины у этой строки. Объект снова считается со следующей ночи, его
прошлые сутки снова видны на экране и в сверке. Галочки «нет в системе» у
существующей строки на экране нет: «Изменить» без машины экран не сохранит
(«Выберите технику или отметьте «Нет в системе»»). Если объект окажется
нашим и машина у него есть -- «Изменить», выбрать машину, «Сохранить»:
строка станет связью, `wialon_id` останется. Снять только id, не удаляя
строку, -- `gps_link_mappings.py --unset СТРОКА --apply`; но тогда автоплан
GPS-10 вернёт id, пока объект с тем же именем есть в Wialon.
Код: инструмент не меняет ни схемы, ни работы программы; откатывать нечего.

СУХОЙ ПРОГОН ПО УМОЛЧАНИЮ. Без `--apply` база открывается `mode=ro`, и
пишется только CSV плана. С `--apply` файл отчёта открывается на запись ДО
базы (открыт в Excel -- отказ с кодом 2, база не тронута); затем одна
транзакция `BEGIN IMMEDIATE`:
план пересобирается внутри неё (между сухим прогоном и записью человек мог
добавить строку на экране), вставляются только строки со статусом `pomechu`,
и до фиксации на том же соединении проверяется постусловие -- правило
`gps.exclusion.excluded_units` называет каждый помеченный объект «не наша».
Не сошлось -- откат целиком, код 1.

ТОЛЬКО STDLIB. Пишет через `sqlite3` напрямую, без Flask: `app = create_app()`
вызывает `db.create_all()` на импорте, и любой скрипт с `from app import app`
становится писателем схемы. В Wialon не ходит вовсе.

Коды выхода:
  0 -- план (или запись) без остатка;
  3 -- часть объектов оставлена человеку (названы в CSV и в консоли),
       остальное записано (или будет записано);
  1 -- постусловие не сошлось, откат, ничего не записано;
  2 -- неверный ввод, ничего не записано;
  4 -- база занята другим писателем дольше 30 с, ничего не записано --
       повторить позже;
  5 -- строки ЗАПИСАНЫ, но CSV отчёта записать не удалось (строка «written»
       в консоли -- правда).

Запуск (PowerShell, из C:\\gps-tools, по одной команде на строку):

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_mark_not_ours.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --ids docs\\gps_ne_nasha_2026-10-14.csv --out C:\\gps-tools\\check\\ne_nasha_plan.csv

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_mark_not_ours.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --ids docs\\gps_ne_nasha_2026-10-14.csv --out C:\\gps-tools\\check\\ne_nasha_apply.csv --apply

Вывод в консоль -- ASCII: имена объектов -- только в CSV (UTF-8 с BOM).

Запускать системным Python из папки `C:\\transport-report` не нужно: токен
Wialon инструменту не нужен.
"""

import argparse
import csv
import os
import re
import sqlite3
import sys

from collections import Counter
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_collector import config, storage                           # noqa: E402
from gps.exclusion import (EXCLUDED_NON_FIELD, EXCLUDED_NOT_OURS,   # noqa: E402
                           excluded_units)

DB_PATH = os.path.join(REPO_ROOT, 'instance', 'transport.db')

MARK = 'pomechu'              # строки нет -- будет создана
ALREADY = 'uzhe_ne_nasha'     # объект уже исключён как «не наша»
NON_FIELD = 'uzhe_ne_polevaya'  # уже исключён категорией машины
HAS_ROW = 'est_stroka'        # строка с этим id есть, объект не «не наша»
NAME_TAKEN = 'imya_zanyato'   # имя занято строкой без этого id
NO_NAME = 'net_imeni'         # сборщик имени не знает -- строку не создать

LEFT_TO_PERSON = (HAS_ROW, NAME_TAKEN, NO_NAME)
CSV_ORDER = (HAS_ROW, NAME_TAKEN, NO_NAME, MARK, ALREADY, NON_FIELD)
CSV_COLUMNS = ('status', 'wialon_id', 'osnovanie', 'wialon_name', 'row_name',
               'v_wialon', 'seen_at', 'days_computed', 'ha_total', 'first_day',
               'last_day', 'mapping_id', 'mapping_name', 'skip', 'equipment_id',
               'primechanie')

EXIT_BUSY = 4
BUSY_TIMEOUT_S = 30
EXIT_REPORT_FAILED = 5

# Что делать с объектом, оставленным человеку. Текст -- в CSV, не в консоль.
NEXT_STEP = {
    HAS_ROW: ('строка с этим id уже есть и объект считается: решать по '
              'строке на экране сопоставления (машина, категория) -- '
              'инструмент её не трогает'),
    NAME_TAKEN: ('имя занято строкой сопоставления (номер -- в mapping_id): '
                 'пришлите этот CSV сессии; экран сопоставления id объекту '
                 'не ставит'),
    NO_NAME: ('сборщик не знает имени объекта, а строке нужно имя: пришлите '
              'этот CSV сессии'),
}


class BadInput(Exception):
    pass


def normalize_name(text):
    """Имя строки так, как его хранит экран сопоставления.

    [REASON]: та же нормализация, что у `wialon_import._normalize_wialon_name`
    (пробелы по краям и повторные внутри): строку, созданную здесь, экран и
    импорт моточасов должны находить тем же именем, что и строку, добавленную
    руками. Импортировать нельзя -- модуль тянет Flask.
    """
    return re.sub(r'\s+', ' ', (text or '').strip())


def name_key(text):
    """Ключ занятости имени: нормализация плюс регистр, как у GPS-10/GPS-12.

    [REASON]: UNIQUE в базе различает регистр, а человек -- нет. Строка
    «Damas» рядом с «DAMAS» -- две строки об одной машине, и вторую без
    человека заводить нельзя.
    """
    return normalize_name(text).casefold()


def read_ids(path):
    """[(wialon_id, основание)] из CSV со столбцом wialon_id. Кривое -- отказ."""
    if not os.path.isfile(path):
        raise BadInput('ids file not found: %s' % path)
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if 'wialon_id' not in (reader.fieldnames or []):
            raise BadInput('%s has no column wialon_id'
                           % os.path.basename(path))
        out, seen = [], set()
        for line, row in enumerate(reader, 2):
            value = (row.get('wialon_id') or '').strip()
            if not value.isdigit() or int(value) <= 0:
                raise BadInput('%s line %d: wialon_id is not a positive number'
                               % (os.path.basename(path), line))
            unit_id = int(value)
            # [REASON]: повтор id -- признак ошибки в списке (склеили два
            # списка, вставили строку дважды), а не намерение. Список --
            # решение человека, и его не чинят молча.
            if unit_id in seen:
                raise BadInput('%s line %d: wialon_id %d is listed twice'
                               % (os.path.basename(path), line, unit_id))
            seen.add(unit_id)
            out.append((unit_id, (row.get('osnovanie') or '').strip()))
    if not out:
        raise BadInput('%s lists no objects' % os.path.basename(path))
    return out


def collector_units(folder):
    """unit_id -> (имя, когда сборщик видел объект в списке Wialon). Только чтение.

    [REASON]: `mode=ro`, как у экрана: нет файла или таблицы -- имён нет, а
    отсутствующий файл не создаётся. Сборщик строку никогда не удаляет
    (`storage.write_unit_names`), поэтому имя объекта, удалённого из Wialon,
    здесь есть -- ровно то, что нужно для фермерских объектов 14.10.
    """
    path = storage.state_path(folder)
    if not os.path.isfile(path):
        return {}
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=5)
    except sqlite3.Error:
        return {}
    try:
        rows = con.execute(
            'SELECT unit_id, name, seen_at FROM collector_units').fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return {int(unit): (name.strip(), seen_at or '') for unit, name, seen_at in rows
            if isinstance(name, str) and name.strip()}


def computed_history(con, units):
    """unit_id -> посчитанные сутки за всё время: число, гектары, первые, последние.

    [REASON]: только опубликованные сутки (причина пуста) -- те, что видны на
    экране и в сверке нарядов. Окна нет: объект, удалённый из Wialon месяц
    назад, тоже должен быть узнан по своим суткам.
    """
    out = {}
    if not units:
        return out
    marks = ','.join('?' * len(units))
    try:
        for unit_id, days, first, last in con.execute(
                'SELECT wialon_id, COUNT(*), MIN(work_date), MAX(work_date) '
                'FROM gps_daily_aggregates WHERE reason IS NULL '
                'AND wialon_id IN (%s) GROUP BY wialon_id' % marks, list(units)):
            out[int(unit_id)] = {'days': int(days), 'ha': 0.0,
                                 'first': str(first), 'last': str(last)}
        for unit_id, area in con.execute(
                'SELECT p.wialon_id, COALESCE(SUM(p.area_ha), 0) '
                'FROM gps_work_polygons p '
                'JOIN gps_daily_aggregates a ON a.work_date = p.work_date '
                'AND a.wialon_id = p.wialon_id AND a.reason IS NULL '
                'WHERE p.wialon_id IN (%s) GROUP BY p.wialon_id' % marks,
                list(units)):
            if int(unit_id) in out:
                out[int(unit_id)]['ha'] = float(area or 0.0)
    except sqlite3.OperationalError:
        # Нет таблиц расчёта (новая база, фикстура): объект узнаётся по
        # имени у сборщика или не узнаётся вовсе.
        return {}
    return out


def mapping_rows(con):
    return [{'id': row[0], 'vialon_name': row[1] or '', 'wialon_id': row[2],
             'skip': bool(row[3]), 'equipment_id': row[4]}
            for row in con.execute(
                'SELECT id, vialon_name, wialon_id, skip, equipment_id '
                'FROM vialon_mappings ORDER BY id')]


def schema_problem(con):
    """'' или что не так. Ничего не создаёт."""
    present = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='vialon_mappings'").fetchone()
    if not present:
        return 'table vialon_mappings is missing'
    columns = {row[1] for row in con.execute('PRAGMA table_info(vialon_mappings)')}
    for column in ('wialon_id', 'skip'):
        if column not in columns:
            return ('column vialon_mappings.%s is missing - run '
                    'migrate_core_foundation_001.py first' % column)
    return ''


def plan(con, wanted, names):
    """Строки плана по каждому id списка. Ничего не пишет.

    `wanted` -- [(wialon_id, основание)], `names` -- `collector_units()`.
    Бросает BadInput, если id не известен ни сборщику, ни расчёту, ни
    сопоставлению.
    """
    rows = mapping_rows(con)
    by_id, by_key = {}, {}
    for row in rows:
        if row['wialon_id'] is not None:
            by_id.setdefault(int(row['wialon_id']), []).append(row)
        by_key.setdefault(name_key(row['vialon_name']), []).append(row)
    excluded = excluded_units(con)
    history = computed_history(con, [unit for unit, _ in wanted])
    latest_seen = max((seen for _, seen in names.values()), default='')
    holders = {}
    for unit, (name, _) in names.items():
        holders.setdefault(name_key(name), set()).add(unit)

    unknown = [unit for unit, _ in wanted
               if unit not in names and unit not in history
               and unit not in by_id]
    if unknown:
        raise BadInput('object(s) unknown to the collector, the computation '
                       'and the mapping - a typo in the list? %s'
                       % ', '.join(str(unit) for unit in unknown))

    items = []
    for unit_id, basis in wanted:
        name, seen = names.get(unit_id, ('', ''))
        stored = normalize_name(name)
        row_name, note = stored, ''
        done = history.get(unit_id, {})
        own = by_id.get(unit_id, [])
        if own:
            # [REASON]: объект, который уже не считается, решения не ждёт:
            # цель списка достигнута. Исключённый категорией (легковая с
            # карточкой) остаётся с категорией -- галочка «нет в системе»
            # обнулила бы его связь с машиной (`wialon_import`), а она нужна
            # под отчёт «пробег + геозоны». Всё прочее со строкой решает
            # человек.
            why = excluded.get(unit_id)
            status = (ALREADY if why == EXCLUDED_NOT_OURS else
                      NON_FIELD if why == EXCLUDED_NON_FIELD else HAS_ROW)
            others = own
        elif not stored:
            status, others = NO_NAME, []
        else:
            shared = sorted(holders.get(name_key(stored), set()) - {unit_id})
            if shared:
                # [REASON]: имя носит ещё один объект Wialon. Импорт моточасов
                # и «+ Добавить» на экране ищут строку по имени: строка «не
                # наша» под общим именем объявила бы чужим и второй объект, а
                # его добавление на экране легло бы на эту строку с нашим
                # id. Под именем с номером строка касается только своего id
                # (так же поступают два объекта одного имени в одном списке).
                row_name = '%s (wialon %d)' % (stored, unit_id)
                note = ('имя общее с объектами %s -- строка под именем с '
                        'номером' % ', '.join(str(unit) for unit in shared))
            others = list(by_key.get(name_key(row_name), []))
            if others:
                # [REASON]: имя занято строкой сопоставления. Завести вторую
                # значит либо упасть на UNIQUE, либо, при другом регистре,
                # получить две строки об одной машине. Чья строка -- решает
                # человек.
                status = NAME_TAKEN
            else:
                status = MARK
                by_key[name_key(row_name)] = [{'id': '', 'vialon_name': row_name,
                                               'skip': True,
                                               'equipment_id': None}]
        if status in NEXT_STEP:
            note = NEXT_STEP[status] if not note else '%s; %s' % (
                note, NEXT_STEP[status])
        items.append({
            'status': status,
            'wialon_id': unit_id,
            'osnovanie': basis,
            'wialon_name': stored,
            'row_name': row_name if status == MARK else '',
            # [REASON]: сборщик пишет всем объектам списка одну метку времени
            # за прогон. Объект, чья метка старше последней, в последнем
            # списке Wialon уже не был.
            'v_wialon': ('' if not seen else
                         'da' if seen == latest_seen else 'net'),
            'seen_at': seen,
            'days_computed': done.get('days', 0),
            'ha_total': round(done.get('ha', 0.0), 2),
            'first_day': done.get('first', ''),
            'last_day': done.get('last', ''),
            'mapping_id': ' '.join(str(row['id']) for row in others),
            'mapping_name': ' | '.join(row['vialon_name'] for row in others),
            'skip': ' '.join('1' if row['skip'] else '0' for row in others),
            'equipment_id': ' '.join(str(row['equipment_id'] or '')
                                     for row in others).strip(),
            'primechanie': note})
    return items


def write_plan(path, items):
    order = {status: index for index, status in enumerate(CSV_ORDER)}
    ordered = sorted(items, key=lambda item: (order.get(item['status'], 99),
                                              -item['ha_total'],
                                              item['wialon_id']))
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for item in ordered:
            writer.writerow([item[column] for column in CSV_COLUMNS])


def out_writable(path):
    """'' или почему в файл отчёта писать нельзя. Файл не портит и не оставляет.

    [REASON]: режим 'a' проверяет право записи (файл, открытый в Excel, на
    Windows отвечает PermissionError), не обрезая прежний отчёт; созданный
    проверкой пустой файл тут же убирается -- отказ не оставляет следов.
    """
    existed = os.path.exists(path)
    try:
        with open(path, 'a', encoding='utf-8'):
            pass
    except OSError as problem:
        return problem.__class__.__name__
    if not existed:
        os.remove(path)
    return ''


def insert_marks(con, items):
    """Строки «не наша» для плана. Вызывается внутри открытой транзакции.

    [REASON]: `created_by` пуст -- запись сделана инструментом, а не
    пользователем экрана; время -- UTC, как пишет модель (`datetime.utcnow`)
    и связка GPS-10.
    """
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    written = 0
    for item in items:
        if item['status'] != MARK:
            continue
        cursor = con.execute(
            'INSERT INTO vialon_mappings (vialon_name, wialon_id, '
            'equipment_id, skip, created_by, created_at, updated_at) '
            'VALUES (?, ?, NULL, 1, NULL, ?, ?)',
            (item['row_name'], item['wialon_id'], stamp, stamp))
        # Номер новой строки -- в отчёт: по нему владелец найдёт её на
        # экране, если решение придётся отменить.
        item['mapping_id'] = str(cursor.lastrowid)
        item['mapping_name'] = item['row_name']
        item['skip'] = '1'
        written += 1
    return written


def postcondition(con, items):
    """'' или что не сошлось. Проверяется правилом расчёта, а не своим запросом.

    [REASON]: смысл записи -- чтобы расчёт и экран перестали считать объект.
    Проверка своим запросом («строка со skip=1 есть») подтвердила бы только
    то, что INSERT выполнился; правило `excluded_units` подтверждает то, ради
    чего его делали, -- и поймает, например, вторую строку того же id без
    галочки, которая оставила бы объект в расчёте.
    """
    excluded = excluded_units(con)
    wrong = [item['wialon_id'] for item in items
             if item['status'] in (MARK, ALREADY)
             and excluded.get(item['wialon_id']) != EXCLUDED_NOT_OURS]
    if wrong:
        return ('not excluded as not ours after the write: %s'
                % ', '.join(str(unit) for unit in wrong))
    return ''


def report(items, out=print):
    counts = Counter(item['status'] for item in items)
    out('objects in the list : %d' % len(items))
    for status in CSV_ORDER:
        if counts.get(status):
            ids = sorted(item['wialon_id'] for item in items
                         if item['status'] == status)
            out('  %-14s %3d : %s' % (status, counts[status],
                                      ' '.join(str(unit) for unit in ids)))
    to_mark = [item for item in items if item['status'] == MARK]
    out('hectares to hide    : %.2f on %d computed day(s)'
        % (sum(item['ha_total'] for item in to_mark),
           sum(item['days_computed'] for item in to_mark)))
    return counts


def _refuse(message):
    sys.stderr.write('ERROR: %s\n' % message)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--dir', default=None,
                        help='folder with the collector files (default: instance/)')
    parser.add_argument('--ids', required=True,
                        help='CSV with a wialon_id column (and optional osnovanie)')
    parser.add_argument('--out', default='gps_ne_nasha_plan.csv',
                        help='CSV with the plan (or what was written)')
    parser.add_argument('--apply', action='store_true',
                        help='write the rows; without it nothing is written')
    args = parser.parse_args(argv)

    # [REASON]: проверка до любого открытия: `sqlite3.connect` на
    # отсутствующий путь создал бы пустую базу, и пустая база вместо боевой
    # -- молчаливая потеря справочника.
    if not os.path.isfile(args.db):
        return _refuse('database not found at %s - refusing to run' % args.db)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(out_dir):
        return _refuse('folder for --out does not exist: %s' % out_dir)
    try:
        wanted = read_ids(args.ids)
    except BadInput as problem:
        return _refuse(str(problem))
    folder = args.dir or config.points_dir()
    names = collector_units(folder)
    print('collector names read : %d' % len(names))

    if not args.apply:
        con = sqlite3.connect('file:%s?mode=ro' % args.db, uri=True, timeout=30)
        try:
            problem = schema_problem(con)
            if problem:
                return _refuse(problem)
            items = plan(con, wanted, names)
        except BadInput as problem:
            return _refuse(str(problem))
        finally:
            con.close()
        write_plan(args.out, items)
        counts = report(items)
        print('')
        print('DRY RUN - nothing was written to the database (opened mode=ro)')
        print('rows that --apply would create: %d' % counts.get(MARK, 0))
        print('plan: %s' % args.out)
        return 3 if any(counts.get(status) for status in LEFT_TO_PERSON) else 0

    # [REASON]: файл отчёта проверяется до базы. Открыт в Excel (частый
    # случай: та же команда, что у сухого прогона, плюс --apply) -- отказ
    # с кодом 2, пока в базе ничего не тронуто; иначе строки записались бы,
    # а владелец увидел бы трассировку и решил, что запись не прошла.
    why = out_writable(args.out)
    if why:
        return _refuse('cannot write --out %s (%s) - nothing was written'
                       % (args.out, why))
    return _apply(args, wanted, names)


def _apply(args, wanted, names):
    con = sqlite3.connect(args.db, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    try:
        problem = schema_problem(con)
        if problem:
            return _refuse(problem)
        try:
            con.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as problem:
            # Служба или ночной расчёт держат запись дольше таймаута.
            print('FAILED: database is busy (%s) - nothing was written, '
                  'run the same command again later' % problem)
            return EXIT_BUSY
        try:
            items = plan(con, wanted, names)
            written = insert_marks(con, items)
            failed = postcondition(con, items)
            if failed:
                con.execute('ROLLBACK')
                try:
                    write_plan(args.out, items)
                except OSError:
                    pass
                report(items)
                print('')
                print('FAILED: %s' % failed)
                print('rolled back - nothing was written')
                return 1
            con.execute('COMMIT')
        except BadInput as problem:
            con.execute('ROLLBACK')
            return _refuse(str(problem))
        except Exception:
            con.execute('ROLLBACK')
            raise
    finally:
        con.close()
    counts = report(items)
    print('')
    print('written: %d row(s) "not ours", one transaction' % written)
    try:
        write_plan(args.out, items)
    except OSError as problem:
        print('report CSV could not be written (%s) - the rows ARE written'
              % problem.__class__.__name__)
        return EXIT_REPORT_FAILED
    print('report : %s' % args.out)
    return 3 if any(counts.get(status) for status in LEFT_TO_PERSON) else 0


if __name__ == '__main__':
    sys.exit(main())
