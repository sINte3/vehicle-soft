# -*- coding: utf-8 -*-
"""tools/dji_area_retained_release_closeout.py -- закрытие выпуска правила
DJI-AREA-RETAINED-FOOTPRINT-001 на production одним инструментом.

DJI-AREA-RETAINED-RELEASE-CLOSEOUT-002. Блок R1 28.09.2026 остановился
правильно: сухой прогон сентября хотел переписать 362 строки вместо пяти
названных оракулом. 357 лишних -- дрейф ОТПЕЧАТКА без смены решения: у 356
позже появилась новая неизменяемая ревизия списка DJI с тем же RAW, у одной
сменился источник соседа по цепочке (базы). Решение, RAW, счёт -- прежние;
изменилось только то, на какие неизменяемые источники ссылается расчёт.
Разбор с номерами -- `docs/DJI_AREA_RETAINED_FOOTPRINT_RELEASE_RUNBOOK.md`;
в коде номеров вылетов нет, они приходят только из оракула.

Инструмент доказывает это ДАННЫМИ для каждой строки и только после этого
переписывает такие строки штатным append-only путём (`pipeline.recalculate`
с `--apply` по названным `flight_id`) -- после чего переход сентября снова
равен ровно пяти строкам оракула. Списка «357» здесь нет и быть не должно:
строка нормализуется, только если сама прошла все ворота.

ВОРОТА НОРМАЛИЗАЦИИ (каждая строка, иначе отказ и остановка всего выпуска):

1. текущая строка расчёта есть, сухой прогон хочет другой отпечаток;
2. строка, которую запишет пересчёт, совпадает с сохранённой во ВСЕХ
   колонках результата (`RESULT_COLUMNS` = все колонки `store.CALC_COLUMNS`,
   кроме отпечатка, ссылок на ревизии и сводку V4 и служебных дат): RAW,
   принятое, статусы, метод, уверенность, флаги аномалий, применение, экран
   и цепочка, пересечение, допуск к итогу, billable (пуст);
3. правило DJI-AREA-RETAINED-FOOTPRINT-001 на строке не срабатывает -- его
   строки идут переходом оракула, а не нормализацией;
4. ПРИЧИНА доказана контрфактом: тот же код с указателями ревизий, откатанными
   к тому, что было при сохранённом расчёте, воспроизводит сохранённый
   отпечаток бит в бит. Значит, кроме того, какие ревизии текущие, не
   изменилось ничего -- ни код, ни конфигурация, ни идентичность машины, ни
   состав соседей;
5. действующее решение администратора не меняет статус «расчёт изменился»
   (отпечаток входит в идентичность решения);
6. привязка к полю, которую перепишет то же применение, та же.

ПОДКОМАНДЫ

  inspect    только чтение: классификация периода оракула и план;
  normalize  копия базы -> нормализация доказанных строк -> проверки после;
  r1         до деплоя (код -- клон тега выпуска): копия, снимок RAW,
             нормализация, повтор на коде production (`--baseline-root`),
             оценщик правила, PRE-APPLY, сторож RAW;
  r2         после деплоя: вердикт r1, копия, нормализация дрейфа после r1,
             PRE-APPLY, применение ровно `expected_rewrites`, повтор по ним
             (`unchanged`), второй прогон периода, POST-APPLY, сторож RAW.

Шаги r1/r2 -- существующие инструменты (`dji_area_recalc`, оценщик
`dji_area_footprint_calibration`, `dji_area_control_acceptance`,
`dji_area_raw_guard`), вызванные в этом процессе с теми же аргументами, что
прежде стояли в блоках PowerShell. Копия базы -- штатный
`backup_transport_db.py` (online backup + `PRAGMA integrity_check`). Всё, что
пишет, делается под блокировкой цикла площади (`dji_area_cycle.lock`):
цикл по расписанию или backfill в это время не стартует.

Запуск (службы остановлены; корень -- клон тега выпуска для r1 и
production для r2):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_retained_release_closeout.py inspect --db C:\\transport-report\\instance\\transport.db --oracle docs\\DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json --out C:\\VehicleSoft_Retained_Footprint_Release\\inspect
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_retained_release_closeout.py r1 --db C:\\transport-report\\instance\\transport.db --oracle docs\\DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json --out C:\\VehicleSoft_Retained_Footprint_Release\\closeout_r1 --backup-dir C:\\transport-report\\backups\\dji-area --raw-snapshot C:\\VehicleSoft_Retained_Footprint_Release\\raw_before.json --baseline-root C:\\transport-report

Коды возврата: 0 PASS; 1 ошибка аргументов или данных; 2 база не найдена
(файл НЕ создаётся); 3 STOP -- ворота не пройдены, причины напечатаны и
записаны в `closeout_verdict.json`. Вывод в консоль только ASCII.

ОТКАТ. Кода: удалить файл, продукт его не импортирует. Данных: строки
нормализации append-only, прежняя строка закрыта `superseded_at` и в
результате не отличается от новой; код production до деплоя видит новые
строки `unchanged` (проверено шагом `--baseline-root`). Полный возврат базы --
копия из `closeout_verdict.json` (`backup.path`) при остановленных службах.
"""

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import traceback
from collections import defaultdict
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dji_area  # noqa: E402
from dji_area import accounting as acc  # noqa: E402
from dji_area import control_store as cs  # noqa: E402
from dji_area import decisions as dec  # noqa: E402
from dji_area import pipeline as pl  # noqa: E402
from dji_area import store  # noqa: E402
from drone_collector import runlock  # noqa: E402  (stdlib only)
from tools import dji_area_control_acceptance as acceptance  # noqa: E402
from tools import dji_area_footprint_calibration as evaluator  # noqa: E402
from tools import dji_area_holdout as holdout  # noqa: E402
from tools import dji_area_raw_guard as raw_guard  # noqa: E402
from tools import dji_area_recalc as recalc  # noqa: E402

TOOL_ID = 'DJI-AREA-RETAINED-RELEASE-CLOSEOUT-002'
LOCK_PURPOSE = 'dji-area-retained-release-closeout'
VERDICT_FILE = 'closeout_verdict.json'
BACKUP_SCRIPT = os.path.join(ROOT, 'backup_transport_db.py')

EXIT_PASS = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_STOP = 3

PHASE_INSPECT = 'inspect'
PHASE_NORMALIZE = 'normalize'
PHASE_R1 = 'r1'
PHASE_R2 = 'r2'

# ─── Классы строк периода ────────────────────────────────────────────────────

UNCHANGED = 'UNCHANGED'
HASH_ONLY = 'HASH_ONLY_SAFE_NORMALIZATION'
TRANSITION = 'EXPECTED_TRANSITION'
REFUSED = 'REFUSED'
CLASSES = (UNCHANGED, HASH_ONLY, TRANSITION, REFUSED)

# Причины отказа. Любой отказ останавливает выпуск: строка не переписывается,
# а PRE-APPLY с ней в дрейфе всё равно не прошёл бы.
R_NO_STORED = 'NO_STORED_CALCULATION'
R_RAW_CHANGED = 'RAW_CHANGED'
R_BILLABLE = 'BILLABLE_NOT_NULL'
R_RULE_FIRES = 'UNEXPECTED_RULE_FIRING'
R_SEMANTIC = 'SEMANTIC_CHANGE'
R_UNEXPLAINED = 'UNEXPLAINED_HASH_DRIFT'
R_DECISION = 'ADMIN_DECISION_WOULD_CHANGE'
R_FIELD = 'FIELD_ATTRIBUTION_WOULD_CHANGE'
R_FIELD_MISSING = 'FIELD_ATTRIBUTION_MISSING'
R_TRANSITION = 'TRANSITION_NOT_AS_EXPECTED'

# Чей источник сменился (доказывает контрафакт, называет сверка указателей).
CAUSE_OWN = 'OWN_SOURCE_REVISION'
CAUSE_NEIGHBOUR = 'NEIGHBOUR_SOURCE_REVISION'
CAUSE_BOTH = 'OWN_AND_NEIGHBOUR_SOURCE_REVISION'
CAUSE_OTHER = 'OTHER_SOURCE_REVISION'

# ─── Колонки строки расчёта ─────────────────────────────────────────────────

REVISION_KEYS = ('list_revision_id', 'card_revision_id', 'route_revision_id',
                 'v4_revision_id')
KEY_BY_SOURCE = {'list': 'list_revision_id', 'card': 'card_revision_id',
                 'route': 'route_revision_id', 'v4': 'v4_revision_id'}
# Происхождение: на какие неизменяемые источники ссылается строка. Меняться
# при нормализации может только оно. `v4_summary_id` -- ссылка на кэш сводки
# той же ревизии V4 (сухой прогон без кэша её не знает).
PROVENANCE_COLUMNS = ('calculation_input_hash', 'v4_summary_id') + REVISION_KEYS
# Служебные даты строки: их ставит хранилище, а не расчёт.
BOOKKEEPING_COLUMNS = ('calculated_at', 'superseded_at', 'supersede_reason')
# [REASON]: всё остальное -- РЕЗУЛЬТАТ, и он обязан совпасть целиком, а не по
# списку «важных» полей. Список выводится из `store.CALC_COLUMNS`, поэтому
# колонка, добавленная в хранилище позже, попадает под сравнение сама, а не
# проходит молча мимо ворот.
RESULT_COLUMNS = tuple(c for c in store.CALC_COLUMNS
                       if c not in PROVENANCE_COLUMNS + BOOKKEEPING_COLUMNS)
BOOL_COLUMNS = ('application_without_area', 'structural_candidate',
                'scalar_source_check')
# Привязка к полю -- отдельная таблица, но пишет её то же применение.
FIELD_IDENTITY_COLUMNS = ('field_attribution_tier', 'field_attribution_method',
                          'field_confidence', 'field_land_uuid',
                          'field_name_at_snapshot', 'field_serial_number')

SHOW_AT_MOST = 20
BASELINE_CHUNK = 400


class CloseoutError(RuntimeError):
    """Остановка до записи: ворота, копия, блокировка."""


def utc_now_text():
    return datetime.now(timezone.utc).replace(
        tzinfo=None, microsecond=0).isoformat(sep=' ')


def say(text=''):
    """Консоль Windows -- только ASCII."""
    print(str(text).encode('ascii', 'replace').decode('ascii'))


def _ids(values, at_most=SHOW_AT_MOST):
    values = sorted(values)
    text = ', '.join(str(v) for v in values[:at_most])
    if len(values) > at_most:
        text += ' and %d more' % (len(values) - at_most)
    return text or '-'


def lf_sha256(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read().replace(b'\r\n', b'\n')).hexdigest()


def write_json(path, document):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with io.open(path, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1,
                  sort_keys=True, default=str)


def read_json(path):
    with io.open(path, encoding='utf-8') as handle:
        return json.load(handle)


# ─── Оракул ──────────────────────────────────────────────────────────────────

def load_oracle(path):
    """(документ, (from, to), переход). ValueError -- оракул негоден."""
    try:
        oracle = read_json(path)
        period = (date.fromisoformat(oracle['period'][0]),
                  date.fromisoformat(oracle['period'][1]))
        expected = oracle['expected']
    except (IOError, OSError, ValueError, KeyError, IndexError,
            TypeError) as exc:
        raise ValueError('cannot read the oracle: %s' % exc)
    transition = oracle.get('transition')
    if not isinstance(transition, dict) or not isinstance(expected, dict):
        raise ValueError('the oracle has no transition section -- this tool '
                         'closes out a model transition')
    return oracle, period, transition


def transition_sets(transition):
    return (set(acceptance.transition_ids(transition, 'expected_rewrites')),
            set(acceptance.transition_ids(transition, 'must_stay_review')),
            transition.get('rule_flag'))


# ─── Сравнение строк ─────────────────────────────────────────────────────────

def _norm(column, value):
    """Значение колонки так, как его хранит база.

    `insert_calculation` пишет три логические колонки как 1/0/NULL, а строка
    сухого прогона несёт True/False: без приведения «тот же результат»
    выглядел бы изменённым.
    """
    if column in BOOL_COLUMNS:
        if value is None:
            return None
        return 1 if value else 0
    return value


def result_diff(stored, new):
    """Колонки результата, в которых строки расходятся. Сравнение точное:
    RAW и принятое не пересчитываются никем и обязаны совпасть до бита."""
    return [c for c in RESULT_COLUMNS
            if _norm(c, stored.get(c)) != _norm(c, new.get(c))]


def _same_text(a, b):
    if a is None or b is None:
        return a is None and b is None
    return str(a) == str(b)


def _flags(row):
    try:
        return list(json.loads(row.get('anomaly_flags_json') or '[]'))
    except ValueError:
        return []


# ─── Настоящий конвейер, снятый изнутри ─────────────────────────────────────

@contextlib.contextmanager
def instrumented_pipeline(provenance=None):
    """Сухой прогон `dji_area.pipeline` со снятием ПОЛНОЙ строки расчёта.

    ``provenance`` -- None либо функция flight_id -> {ключ ревизии: id}: её
    указатели ставятся в загруженные записи вместо текущих (контрафакт).

    [REASON]: построчный вывод `--rows` несёт решение, но не всю строку -- нет
    ни мостиков цепочки, ни причин окна, ни источника RAW. Ворота обязаны
    сравнить ВСЁ, что запишет применение, поэтому строка снимается там, где
    конвейер её уже собрал (`_accumulate`), а не собирается второй раз здесь:
    вторая реализация расчёта разошлась бы с первой. Файлы конвейера не
    меняются -- отпечаток замороженного кода тот же; подмена живёт только
    внутри `with` и снимается в `finally`.
    """
    captured = {}
    calls = {'load_flights': 0, 'accumulate': 0}
    original_load, original_accumulate = pl.load_flights, pl._accumulate

    def load_flights(con, date_from, date_to, flight_ids=None):
        items = original_load(con, date_from, date_to, flight_ids)
        calls['load_flights'] += 1
        if provenance is not None:
            for item in items:
                item.update(provenance(item['flight_id']))
        return items

    def accumulate(summary, item, decision, field, calc_row):
        calls['accumulate'] += 1
        captured[int(calc_row['flight_id'])] = {'calc': dict(calc_row),
                                                'field': dict(field or {})}
        return original_accumulate(summary, item, decision, field, calc_row)

    pl.load_flights, pl._accumulate = load_flights, accumulate
    try:
        yield captured, calls
    finally:
        pl.load_flights, pl._accumulate = original_load, original_accumulate


def dry_run(db_path, period, flight_ids=None, provenance=None):
    """(сводка, {flight_id: {'calc': строка, 'field': привязка}})."""
    with instrumented_pipeline(provenance) as (captured, calls):
        summary = pl.recalculate(db_path, period[0], period[1], apply=False,
                                 read_only=True, flight_ids=flight_ids)
    # [REASON]: подмена, которая не сработала, дала бы «доказательство» из
    # пустого места. Каждая цель прогона обязана пройти через снятие.
    if calls['load_flights'] < 1 or \
            calls['accumulate'] != summary['flights_in_period'] or \
            len(captured) != summary['flights_in_period']:
        raise CloseoutError('pipeline instrumentation did not see every '
                            'target (%r, %d captured, %r in period)'
                            % (calls, len(captured),
                               summary['flights_in_period']))
    summary.pop('flights', None)
    return summary, captured


# ─── Чтение базы ─────────────────────────────────────────────────────────────

def connect_ro(db_path):
    return store.connect(db_path, read_only=True)


def has_table(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                       "name=?", (name,)).fetchone() is not None


def _chunks(ids, size=400):
    ids = sorted(set(int(i) for i in ids))
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


def stored_rows(con, period):
    """Текущие строки расчёта периода (по дню отчёта), как они лежат."""
    return {int(r['flight_id']): dict(r) for r in con.execute(
        'SELECT * FROM dji_area_calculations WHERE superseded_at IS NULL '
        'AND area_algorithm_version = ? AND report_start_date BETWEEN ? '
        'AND ?', (dji_area.AREA_ALGORITHM_VERSION, period[0].isoformat(),
                  period[1].isoformat()))}


def current_row(con, flight_id):
    row = store.current_calculation(con, flight_id)
    return dict(row) if row is not None else None


def stored_field_rows(con, flight_ids):
    out = {}
    for chunk in _chunks(flight_ids):
        for r in con.execute(
                'SELECT * FROM dji_field_attributions WHERE superseded_at IS '
                'NULL AND field_resolver_version = ? AND flight_id IN (%s) '
                'ORDER BY id' % ','.join('?' * len(chunk)),
                [dji_area.FIELD_RESOLVER_VERSION] + chunk):
            out[int(r['flight_id'])] = dict(r)
    return out


def current_evidence(con, flight_ids):
    out = {}
    for chunk in _chunks(flight_ids):
        for r in con.execute(
                'SELECT flight_id, list_revision_id, card_revision_id FROM '
                'dji_flight_evidence WHERE flight_id IN (%s)'
                % ','.join('?' * len(chunk)), chunk):
            out[int(r['flight_id'])] = dict(r)
    return out


def active_decisions(con, flight_ids):
    """Действующие решения администратора ({} без таблиц V2)."""
    if not flight_ids or not has_table(con, cs.DECISIONS_TABLE):
        return {}
    return cs.active_decisions(con, flight_ids)


def billable_in_table(con):
    return int(con.execute('SELECT COUNT(*) FROM dji_area_calculations WHERE '
                           'billable_area_m2 IS NOT NULL').fetchone()[0])


def v2_presence(con):
    """Миграция DRONE_AREA_CONTROL_V2_001: наличие, не действие.

    На production она применена (инвентаризация 28.09.2026); здесь это только
    печатается -- ни применять, ни повторять её выпуск не должен."""
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')")}
    registered = None
    if 'schema_migrations' in names:
        registered = con.execute(
            'SELECT COUNT(*) FROM schema_migrations WHERE name = ?',
            ('DRONE_AREA_CONTROL_V2_001',)).fetchone()[0] > 0
    return {'tables': all(t in names for t in (cs.DECISIONS_TABLE,
                                               cs.RUNS_TABLE)),
            'triggers': all(t in names for t in cs.APPEND_ONLY_TRIGGERS),
            'registered': registered}


# ─── Указатели источников на момент сохранённого расчёта ────────────────────

def _moment(text):
    return pl._parse_dt(text) if text else None


class ProvenanceHistory(object):
    """На какие ревизии указывал вылет в момент прошлого расчёта.

    Порядок тот же, что у `store.latest_revisions`: последняя по
    (captured_at_utc, id) каждого типа -- но только из ревизий, ПРИНЯТЫХ к
    этому моменту (`received_at`). Приёмник кладёт ревизию и пересобирает
    указатели вылета в одной транзакции с одним `now`, поэтому это и есть
    состояние `dji_flight_evidence` на тот момент.

    Для вылетов, посчитанных тем же прогоном (та же `calculated_at`), указатели
    берутся из их собственной строки: прогон записал ровно то, что загрузил.
    """

    def __init__(self, con, moments):
        self.revisions = defaultdict(list)
        for r in con.execute(
                "SELECT id, flight_id, source_type, received_at FROM "
                "dji_source_revisions WHERE flight_id IS NOT NULL AND "
                "source_type IN ('list', 'card', 'route', 'v4') "
                "ORDER BY captured_at_utc, id"):
            self.revisions[int(r['flight_id'])].append(
                (r['id'], r['source_type'], _moment(r['received_at'])))
        self.same_run = {}
        texts = sorted(set(m for m in moments if m))
        for start in range(0, len(texts), 200):
            part = texts[start:start + 200]
            for r in con.execute(
                    'SELECT id, flight_id, calculated_at, list_revision_id, '
                    'card_revision_id, route_revision_id, v4_revision_id '
                    'FROM dji_area_calculations WHERE area_algorithm_version '
                    '= ? AND calculated_at IN (%s) ORDER BY id'
                    % ','.join('?' * len(part)),
                    [dji_area.AREA_ALGORITHM_VERSION] + part):
                self.same_run[(r['calculated_at'], int(r['flight_id']))] = {
                    key: r[key] for key in REVISION_KEYS}

    def as_of(self, flight_id, moment_text):
        flight_id = int(flight_id)
        row = self.same_run.get((moment_text, flight_id))
        if row is not None:
            return dict(row)
        moment = _moment(moment_text)
        out = dict.fromkeys(REVISION_KEYS)
        for rev_id, source_type, received in self.revisions.get(flight_id,
                                                                ()):
            if received is None or moment is None or received <= moment:
                out[KEY_BY_SOURCE[source_type]] = rev_id
        return out


def _selected(revisions):
    """Ревизия, которой вылет входит в отпечаток СОСЕДА
    (`pipeline._neighbour_revision`: карточка, иначе список)."""
    return revisions.get('card_revision_id') or revisions.get(
        'list_revision_id')


def neighbours_of(row, stored):
    """Соседи отпечатка: база, мостики и группа пересечения строки."""
    out = set()
    if row.get('candidate_base_flight_id'):
        out.add(int(row['candidate_base_flight_id']))
    try:
        out.update(int(b) for b in json.loads(
            row.get('bridge_flight_ids_json') or '[]'))
    except (ValueError, TypeError):
        pass
    group = row.get('overlap_group_id')
    if group:
        out.update(fid for fid, other in stored.items()
                   if other.get('overlap_group_id') == group)
    out.discard(int(row['flight_id']))
    return sorted(out)


# ─── Классификация периода ───────────────────────────────────────────────────

def _refuse(record, code, detail=None):
    record['class'] = REFUSED
    record['refusal'] = code
    if detail is not None:
        record['detail'] = detail
    return record


def classify(db_path, oracle, period, transition, progress=say):
    """Каждая цель периода -- в один из классов; план нормализации.

    Только чтение: все прогоны -- `read_only=True`.
    """
    expected, stay, flag = transition_sets(transition)
    progress('  dry run of the period %s .. %s (current code) ...'
             % (period[0], period[1]))
    summary, captured = dry_run(db_path, period)
    con = connect_ro(db_path)
    try:
        stored = stored_rows(con, period)
        billable_table = billable_in_table(con)
    finally:
        con.close()

    records, candidates = {}, []
    for fid in sorted(captured):
        new = captured[fid]['calc']
        old = stored.get(fid)
        record = {'flight_id': fid, 'report_day': new.get('report_start_date'),
                  'class': None,
                  'new_hash': new.get('calculation_input_hash'),
                  'raw_area_m2': new.get('raw_area_m2')}
        records[fid] = record
        if old is None:
            _refuse(record, R_NO_STORED)
            continue
        before = acc.classify(old)
        record['stored_hash'] = old['calculation_input_hash']
        record['stored_calculated_at'] = old['calculated_at']
        record['stored_class'] = before['accounting_class']
        record['stored_reason'] = before['reason']
        if new['calculation_input_hash'] == old['calculation_input_hash']:
            record['class'] = UNCHANGED
            continue
        fires = flag in _flags(new)
        after = acc.classify(new)
        record['new_class'] = after['accounting_class']
        record['new_reason'] = after['reason']
        if fid in expected:
            wrong = []
            if not fires:
                wrong.append('the rule does not fire')
            if record['stored_reason'] != acc.R_APPLICATION_WITH_FLAT_COUNTER:
                wrong.append('before the rule it was %s, not a flat counter '
                             'waiting for a human' % record['stored_reason'])
            if _norm('raw_area_m2', old.get('raw_area_m2')) != _norm(
                    'raw_area_m2', new.get('raw_area_m2')):
                wrong.append('RAW would change')
            if old.get('billable_area_m2') is not None \
                    or new.get('billable_area_m2') is not None:
                wrong.append('billable is not empty')
            if wrong:
                _refuse(record, R_TRANSITION, '; '.join(wrong))
            else:
                record['class'] = TRANSITION
            continue
        diff = result_diff(old, new)
        if 'raw_area_m2' in diff:
            _refuse(record, R_RAW_CHANGED, {'stored': old.get('raw_area_m2'),
                                            'new': new.get('raw_area_m2')})
        elif old.get('billable_area_m2') is not None \
                or new.get('billable_area_m2') is not None:
            _refuse(record, R_BILLABLE)
        elif fires:
            _refuse(record, R_RULE_FIRES)
        elif diff:
            _refuse(record, R_SEMANTIC, diff)
        else:
            candidates.append(fid)

    # ── Контрфакт: причина -- ТОЛЬКО происхождение ──────────────────────
    groups = defaultdict(list)
    for fid in candidates:
        groups[stored[fid]['calculated_at']].append(fid)
    con = connect_ro(db_path)
    try:
        history = ProvenanceHistory(con, list(groups)) if groups else None
        proven = []
        for moment in sorted(groups, key=lambda m: m or ''):
            ids = sorted(groups[moment])
            progress('  counterfactual for %d record(s) calculated at %s ...'
                     % (len(ids), moment))
            _cf_summary, cf = dry_run(
                db_path, period, flight_ids=ids,
                provenance=lambda f, m=moment: history.as_of(f, m))
            for fid in ids:
                cf_hash = cf[fid]['calc']['calculation_input_hash']
                records[fid]['counterfactual_hash'] = cf_hash
                if cf_hash != stored[fid]['calculation_input_hash']:
                    _refuse(records[fid], R_UNEXPLAINED)
                else:
                    proven.append(fid)

        # ── Чей источник: сверка указателей (для отчёта) ─────────────────
        neighbour_ids = set()
        for fid in proven:
            neighbour_ids.update(neighbours_of(stored[fid], stored))
        evidence_now = current_evidence(con, neighbour_ids)
        for fid in proven:
            old, new = stored[fid], captured[fid]['calc']
            moment = old['calculated_at']
            own = [{'source': key[:-len('_revision_id')],
                    'at_calculation': old.get(key), 'current': new.get(key)}
                   for key in REVISION_KEYS if old.get(key) != new.get(key)]
            changed = []
            for nid in neighbours_of(old, stored):
                then = _selected(history.as_of(nid, moment))
                now = _selected(evidence_now.get(nid) or {})
                if then != now:
                    changed.append({'flight_id': nid, 'at_calculation': then,
                                    'current': now})
            if own and changed:
                cause = CAUSE_BOTH
            elif own:
                cause = CAUSE_OWN
            elif changed:
                cause = CAUSE_NEIGHBOUR
            else:
                cause = CAUSE_OTHER
            records[fid].update({'class': HASH_ONLY, 'cause': cause,
                                 'own_changes': own,
                                 'neighbour_changes': changed})

        # ── Решения администратора и привязка к полю ─────────────────────
        transitions = [f for f, r in records.items()
                       if r['class'] == TRANSITION]
        decisions = active_decisions(con, proven + transitions)
        fields = stored_field_rows(con, proven)
        v2 = v2_presence(con)
    finally:
        con.close()

    notes = []
    for fid in proven:
        record = records[fid]
        decision = decisions.get(fid)
        if decision is not None:
            live_before = not dec.decision_is_stale(decision, stored[fid])
            live_after = not dec.decision_is_stale(decision,
                                                   captured[fid]['calc'])
            record['admin_decision'] = {
                'id': decision.get('id'),
                'decision_type': decision.get('decision_type'),
                'live_before': live_before, 'live_after': live_after}
            # [REASON]: решение привязано к (версия, отпечаток) строки. Живое
            # решение по нормализованной строке стало бы «расчёт изменился»,
            # а «принять автоматический результат» перестало бы действовать
            # вовсе -- видимое изменение итога, которого нормализация делать
            # не вправе. Уже устаревшее остаётся устаревшим: итог тот же.
            if live_before != live_after:
                _refuse(record, R_DECISION, record['admin_decision'])
                continue
            notes.append('decision %s on %d is already stale and stays so'
                         % (decision.get('decision_type'), fid))
        stored_field = fields.get(fid)
        if stored_field is None:
            _refuse(record, R_FIELD_MISSING)
            continue
        would = captured[fid]['field']
        moved = [c for c in FIELD_IDENTITY_COLUMNS
                 if not _same_text(stored_field.get(c), would.get(c))]
        if moved:
            _refuse(record, R_FIELD, moved)
    for fid in transitions:
        decision = decisions.get(fid)
        if decision is not None and not dec.decision_is_stale(decision,
                                                                stored[fid]):
            records[fid]['admin_decision'] = {
                'id': decision.get('id'),
                'decision_type': decision.get('decision_type'),
                'live_before': True, 'live_after': False}
            notes.append(
                'transition %d carries a live decision %s: after the apply it '
                'is marked "calculation changed"%s'
                % (fid, decision.get('decision_type'),
                   ' and ACCEPT_AUTO_RESULT stops applying'
                   if decision.get('decision_type') == dec.ACCEPT_AUTO_RESULT
                   else ''))

    # ── Период целиком ───────────────────────────────────────────────────
    by_class = defaultdict(list)
    for fid, record in records.items():
        by_class[record['class']].append(fid)
    problems = []
    records_expected = (oracle.get('expected') or {}).get('records')
    if summary['flights_in_period'] != records_expected:
        problems.append('period: the dry run holds %d flights, the oracle '
                        'names %r -- the oracle no longer describes the period'
                        % (summary['flights_in_period'], records_expected))
    uncovered = sorted(set(stored) - set(captured))
    if uncovered:
        problems.append('period: %d stored row(s) are not targets of the dry '
                        'run: %s' % (len(uncovered), _ids(uncovered)))
    for code in sorted({r.get('refusal') for r in records.values()
                        if r['class'] == REFUSED}):
        ids = [f for f, r in records.items() if r.get('refusal') == code]
        problems.append('refused %s: %d record(s): %s'
                        % (code, len(ids), _ids(ids)))
    missing = sorted(expected - set(by_class[TRANSITION]))
    if missing:
        problems.append('transition: %d expected rewrite(s) are not a clean '
                        'transition: %s' % (len(missing), _ids(missing)))
    for fid in sorted(stay):
        line = captured.get(fid)
        if line is None:
            problems.append('transition: %d must stay REVIEW but is not in '
                            'the period' % fid)
            continue
        out = acc.classify(line['calc'])
        if out['accounting_class'] != acc.REVIEW \
                or flag in _flags(line['calc']):
            problems.append('transition: %d must stay REVIEW, the dry run '
                            'makes it %s / %s' % (fid, out['accounting_class'],
                                                  out['reason']))
        else:
            notes.append('must stay REVIEW %d: %s' % (fid, out['reason']))
    if billable_table:
        problems.append('billable: %d row(s) of dji_area_calculations carry '
                        'billable_area_m2' % billable_table)

    causes = defaultdict(int)
    for fid in by_class[HASH_ONLY]:
        causes[records[fid]['cause']] += 1
    counts = {cls: len(by_class[cls]) for cls in CLASSES}
    return {
        'period': [period[0].isoformat(), period[1].isoformat()],
        'flights_in_period': summary['flights_in_period'],
        'oracle_records': records_expected,
        'dry_run_calc_writes': summary.get('calc_writes'),
        'counts': counts,
        'causes': dict(causes),
        'hash_only': sorted(by_class[HASH_ONLY]),
        'transitions': sorted(by_class[TRANSITION]),
        'refused': sorted(by_class[REFUSED]),
        'counterfactual_runs': len(groups),
        'projected_pre_apply': {
            'unchanged': counts[UNCHANGED] + counts[HASH_ONLY],
            'would_write': counts[TRANSITION] + counts[REFUSED]},
        'predicted_hash': {str(f): records[f]['new_hash']
                           for f in by_class[HASH_ONLY] + by_class[TRANSITION]},
        'v2_migration': v2,
        'billable_non_null_table': billable_table,
        'records': {str(f): r for f, r in sorted(records.items())
                    if r['class'] != UNCHANGED},
        'problems': problems,
        'notes': notes,
    }


CSV_COLUMNS = ('flight_id', 'report_day', 'class', 'refusal', 'cause',
               'raw_area_m2', 'stored_class', 'stored_reason',
               'stored_calculated_at', 'stored_hash', 'new_hash',
               'counterfactual_hash', 'own_changes', 'neighbour_changes',
               'admin_decision', 'detail')


def write_classification(out_dir, name, result):
    write_json(os.path.join(out_dir, name + '.json'), result)
    path = os.path.join(out_dir, name + '.csv')
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS,
                                extrasaction='ignore')
        writer.writeheader()
        for _key, record in sorted(result['records'].items(),
                                   key=lambda kv: int(kv[0])):
            row = dict(record)
            for key in ('own_changes', 'neighbour_changes', 'admin_decision',
                        'detail'):
                if row.get(key) is not None:
                    row[key] = json.dumps(row[key], sort_keys=True)
            writer.writerow(row)


def print_classification(result, title):
    counts = result['counts']
    say('%s %s .. %s' % (title, result['period'][0], result['period'][1]))
    say('  flights in period        : %d (oracle %r)'
        % (result['flights_in_period'], result['oracle_records']))
    say('  dry run calc_writes      : %s'
        % json.dumps(result['dry_run_calc_writes'], sort_keys=True))
    say('  UNCHANGED                : %d' % counts[UNCHANGED])
    say('  HASH_ONLY normalization  : %d  %s'
        % (counts[HASH_ONLY], json.dumps(result['causes'], sort_keys=True)))
    say('  EXPECTED TRANSITION      : %d  [%s]'
        % (counts[TRANSITION], _ids(result['transitions'])))
    say('  REFUSED                  : %d  [%s]'
        % (counts[REFUSED], _ids(result['refused'])))
    say('  counterfactual runs      : %d' % result['counterfactual_runs'])
    projected = result['projected_pre_apply']
    say('  after normalization the dry run wants: unchanged %d, would_write %d'
        % (projected['unchanged'], projected['would_write']))
    v2 = result['v2_migration']
    say('  DRONE_AREA_CONTROL_V2_001: tables %s, triggers %s, registered %s '
        '(presence only, never applied here)'
        % (v2['tables'], v2['triggers'], v2['registered']))
    shown = 0
    for key, record in sorted(result['records'].items(),
                              key=lambda kv: int(kv[0])):
        if record['class'] != REFUSED or shown >= SHOW_AT_MOST:
            continue
        shown += 1
        say('    refused %s %s %s' % (key, record.get('refusal'),
                                      json.dumps(record.get('detail'),
                                                 sort_keys=True)))
    for note in result['notes']:
        say('  %s' % note)
    for problem in result['problems']:
        say('  STOP: %s' % problem)


# ─── Копия базы, снимок, блокировка ─────────────────────────────────────────

def db_signature(db_path, immutable=False):
    """Отпечаток состояния, по которому копия сверяется с живой базой.

    [REASON]: копия -- файл, который никто не пишет, и открывается она с
    `immutable=1`: онлайн-копия WAL-базы остаётся в режиме WAL, а открыть её
    `mode=ro` без файла `-shm` рядом SQLite вправе отказаться.
    """
    if immutable:
        con = sqlite3.connect('file:%s?mode=ro&immutable=1' % os.path.abspath(
            db_path).replace('\\', '/').replace('?', '%3f').replace(
                '#', '%23'), uri=True, timeout=30)
    else:
        con = connect_ro(db_path)
    try:
        out = {}
        for table in ('dji_area_calculations', 'dji_source_revisions',
                      'dji_flight_evidence', 'dji_field_attributions',
                      'drone_flights'):
            count, top = con.execute('SELECT COUNT(*), MAX(rowid) FROM %s'
                                     % table).fetchone()
            out[table] = [count, top]
        out['dji_area_calculations_current'] = con.execute(
            'SELECT COUNT(*) FROM dji_area_calculations WHERE superseded_at '
            'IS NULL').fetchone()[0]
        return out
    finally:
        con.close()


def make_backup(db_path, backup_dir, label, log_dir):
    """Копия базы штатным `backup_transport_db.py` в свой новый каталог.

    [REASON]: копию делает инструмент, а не блок PowerShell перед ним: без
    копии, прошедшей `PRAGMA integrity_check` и совпавшей с живой базой по
    числу и верхушке строк, запись не начинается -- это проверяется кодом и
    тестом, а не порядком строк в тексте, который вставляют руками.
    """
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base = os.path.join(os.path.abspath(backup_dir),
                        'retained_closeout_%s_%s' % (label, stamp))
    dest, number = base, 1
    # Каталог каждой копии -- новый: чужую копию не перезаписать никогда.
    while os.path.exists(dest):
        number += 1
        dest = '%s_%d' % (base, number)
    try:
        os.makedirs(dest)
    except OSError as exc:
        raise CloseoutError('cannot create the backup directory %s: %s'
                            % (dest, exc))
    proc = subprocess.run(
        [sys.executable, BACKUP_SCRIPT, '--source', os.path.abspath(db_path),
         '--dest-dir', dest, '--suffix', 'retained_closeout_%s' % label],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    with io.open(os.path.join(log_dir, 'backup_%s.log' % label), 'w',
                 encoding='utf-8') as handle:
        handle.write(proc.stdout or '')
        handle.write(proc.stderr or '')
    files = [n for n in os.listdir(dest) if n.endswith('.db')]
    if proc.returncode != 0 or len(files) != 1:
        raise CloseoutError('backup_transport_db.py exit %s, %d file(s) in %s'
                            % (proc.returncode, len(files), dest))
    path = os.path.join(dest, files[0])
    size = os.path.getsize(path)
    if size <= 0 or 'Integrity check : ok' not in (proc.stdout or ''):
        raise CloseoutError('the backup %s did not pass its integrity check'
                            % path)
    live, copy = db_signature(db_path), db_signature(path, immutable=True)
    if live != copy:
        raise CloseoutError('the backup does not match the live database: '
                            '%s vs %s' % (copy, live))
    return {'path': path, 'size_bytes': size, 'integrity_check': 'ok',
            'signature': live}


@contextlib.contextmanager
def cycle_lock(db_path):
    """Блокировка цикла площади на всё время записи."""
    lock = runlock.RunLock(runlock.cycle_lock_path(db_path),
                           purpose=LOCK_PURPOSE)
    if not lock.acquire(wait_s=0):
        info = runlock.owner(lock.path) or {}
        raise CloseoutError('the DJI area cycle lock %s is held (%s) -- a cycle '
                            'or backfill is running; nothing was written'
                            % (lock.path, json.dumps(info, sort_keys=True)))
    try:
        yield lock
    finally:
        lock.release()


# ─── Нормализация ────────────────────────────────────────────────────────────

def period_current_ids(con, period):
    return {int(r['flight_id']): int(r['id']) for r in con.execute(
        'SELECT flight_id, id FROM dji_area_calculations WHERE superseded_at '
        'IS NULL AND area_algorithm_version = ? AND report_start_date '
        'BETWEEN ? AND ?', (dji_area.AREA_ALGORITHM_VERSION,
                            period[0].isoformat(), period[1].isoformat()))}


def decision_rows(con):
    if not has_table(con, cs.DECISIONS_TABLE):
        return None
    return con.execute('SELECT COUNT(*) FROM %s' % cs.DECISIONS_TABLE
                       ).fetchone()[0]


def baseline_engine_check(baseline_root, db_path, period, flight_ids,
                          work_dir):
    """Код, который СЕЙЧАС стоит на production, видит нормализованные строки
    `unchanged`.

    [REASON]: до деплоя production работает на прежнем коде. Если бы он
    считал отпечаток этих строк иначе, первый же пересчёт сентября прежним
    кодом переписал бы их назад, а откат выпуска после нормализации оставил
    бы базу в дрейфе. Спрашивается ЕГО собственный `tools/dji_area_recalc.py`
    отдельным процессом -- тот, что на диске production, без подмен.
    """
    script = os.path.join(os.path.abspath(baseline_root), 'tools',
                          'dji_area_recalc.py')
    problems = []
    if not os.path.exists(script):
        return {'script': script}, ['baseline engine: %s not found' % script]
    os.makedirs(work_dir, exist_ok=True)
    flights, writes, runs = 0, defaultdict(int), []
    for number, chunk in enumerate(_chunks(flight_ids, BASELINE_CHUNK)):
        out = os.path.join(work_dir, 'baseline_%03d.json' % number)
        command = [sys.executable, script, '--db', os.path.abspath(db_path),
                   '--from', period[0].isoformat(), '--to',
                   period[1].isoformat(), '--dry-run', '--quiet', '--json',
                   out]
        for fid in chunk:
            command += ['--flight-id', str(fid)]
        proc = subprocess.run(command, cwd=os.path.abspath(baseline_root),
                              capture_output=True, text=True,
                              encoding='utf-8', errors='replace')
        with io.open(os.path.join(work_dir, 'baseline_%03d.log' % number),
                     'w', encoding='utf-8') as handle:
            handle.write(proc.stdout or '')
            handle.write(proc.stderr or '')
        runs.append({'flights': len(chunk), 'exit_code': proc.returncode})
        if proc.returncode != 0 or not os.path.exists(out):
            problems.append('baseline engine: run %d exit %s'
                            % (number, proc.returncode))
            continue
        document = read_json(out)
        flights += int(document.get('flights_in_period') or 0)
        for key, count in (document.get('calc_writes') or {}).items():
            writes[key] += int(count or 0)
    if not problems:
        other = {k: v for k, v in writes.items() if k != 'unchanged' and v}
        if flights != len(flight_ids) or writes.get('unchanged') != len(
                flight_ids) or other:
            problems.append('baseline engine: %d flight(s), calc_writes %s -- '
                            'the production code does not see the %d '
                            'normalized row(s) as unchanged'
                            % (flights, json.dumps(dict(writes),
                                                   sort_keys=True),
                               len(flight_ids)))
    return {'script': script, 'flights_in_period': flights,
            'calc_writes': dict(writes), 'runs': runs}, problems


def normalize_phase(db_path, oracle, period, transition, out_dir,
                    baseline_root=None, backup=None, backup_dir=None):
    """Нормализация доказанных строк. Вызывается под блокировкой цикла.

    Возвращает словарь результата; `problems` непусты -- STOP. Копия базы
    либо уже сделана (`backup`), либо делается здесь перед первой записью.
    """
    os.makedirs(out_dir, exist_ok=True)
    result = {'planned': 0, 'problems': [], 'backup': backup}
    say('')
    say('== NORMALIZE: classification before any write')
    before = classify(db_path, oracle, period, transition)
    write_classification(out_dir, 'classification_before', before)
    print_classification(before, 'CLASSIFICATION')
    result['before'] = {k: before[k] for k in (
        'counts', 'causes', 'hash_only', 'transitions', 'refused',
        'projected_pre_apply', 'dry_run_calc_writes', 'flights_in_period',
        'counterfactual_runs', 'v2_migration')}
    if before['problems']:
        result['problems'] = list(before['problems'])
        say('  NOTHING WAS WRITTEN: the classification refuses the period')
        return result
    targets = before['hash_only']
    result['planned'] = len(targets)
    predicted = before['predicted_hash']
    if targets:
        if backup is None:
            say('  backup before the first write ...')
            backup = make_backup(db_path, backup_dir, 'normalize', out_dir)
            result['backup'] = backup
        con = connect_ro(db_path)
        try:
            rows_before = stored_rows(con, period)
            ids_before = period_current_ids(con, period)
            raw_before = raw_guard.read_state(con)
            decisions_before = decision_rows(con)
            fields_before = stored_field_rows(con, targets)
        finally:
            con.close()
        say('  apply: %d proven record(s), one transaction, append-only'
            % len(targets))
        applied = pl.recalculate(db_path, period[0], period[1], apply=True,
                                 flight_ids=targets,
                                 batch_size=len(targets) + 1)
        applied.pop('flights', None)
        write_json(os.path.join(out_dir, 'normalize_apply.json'), applied)
        result['calc_writes'] = applied.get('calc_writes')
        result['field_writes'] = applied.get('field_writes')
        say('  apply calc_writes %s, field_writes %s'
            % (json.dumps(applied.get('calc_writes'), sort_keys=True),
               json.dumps(applied.get('field_writes'), sort_keys=True)))
        result['problems'] += written_problems(
            db_path, period, targets, predicted, rows_before, ids_before,
            raw_before, decisions_before, fields_before, applied)
    if targets:
        say('')
        say('== NORMALIZE: steady state after')
        after = classify(db_path, oracle, period, transition)
        write_classification(out_dir, 'classification_after', after)
        print_classification(after, 'CLASSIFICATION AFTER')
    else:
        # Ничего не записано: состояние после -- то же, что до, и второй
        # полный прогон ничего бы не добавил.
        say('  nothing to normalize: no fingerprint-only drift in the period')
        after = before
    result['after'] = {k: after[k] for k in (
        'counts', 'transitions', 'refused', 'projected_pre_apply',
        'dry_run_calc_writes', 'flights_in_period')}
    result['problems'] += steady_problems(before, after)
    if targets and baseline_root:
        say('  baseline engine: %s on the %d normalized record(s) ...'
            % (baseline_root, len(targets)))
        check, found = baseline_engine_check(
            baseline_root, db_path, period, targets,
            os.path.join(out_dir, 'baseline_engine'))
        result['baseline_engine'] = check
        result['problems'] += found
        say('  baseline engine calc_writes %s'
            % json.dumps(check.get('calc_writes'), sort_keys=True))
    return result


def written_problems(db_path, period, targets, predicted, rows_before,
                     ids_before, raw_before, decisions_before, fields_before,
                     applied):
    """Проверки ПОСЛЕ записи -- по тому, что реально лежит в базе."""
    problems = []
    writes = applied.get('calc_writes') or {}
    other = {k: v for k, v in writes.items()
             if k not in ('new', 'reactivated') and v}
    if other or writes.get('new', 0) + writes.get('reactivated', 0) \
            != len(targets):
        problems.append('apply: calc_writes %s for %d planned record(s)'
                        % (json.dumps(writes, sort_keys=True), len(targets)))
    con = connect_ro(db_path)
    try:
        for fid in targets:
            now = current_row(con, fid)
            if now is None:
                problems.append('written: %d has no current row' % fid)
                continue
            if now['calculation_input_hash'] != predicted[str(fid)]:
                problems.append('written: %d carries hash %s, the proof named '
                                '%s' % (fid, now['calculation_input_hash'],
                                        predicted[str(fid)]))
            diff = result_diff(rows_before[fid], now)
            if diff:
                problems.append('written: %d differs from the row it replaced '
                                'in %s' % (fid, ', '.join(diff)))
            old = con.execute('SELECT superseded_at, supersede_reason FROM '
                              'dji_area_calculations WHERE id = ?',
                              (rows_before[fid]['id'],)).fetchone()
            if now['id'] != rows_before[fid]['id'] and (
                    old is None or old['superseded_at'] is None):
                problems.append('written: the previous row of %d is not '
                                'superseded' % fid)
        ids_after = period_current_ids(con, period)
        touched = sorted(fid for fid in set(ids_before) | set(ids_after)
                         if fid not in targets
                         and ids_before.get(fid) != ids_after.get(fid))
        if touched:
            problems.append('written: %d row(s) outside the plan changed: %s'
                            % (len(touched), _ids(touched)))
        raw_after = raw_guard.read_state(con)
        if raw_after[0] != raw_before[0]:
            problems.append('RAW: drone_flights.area_ha changed')
        if raw_after[1]:
            problems.append('billable: %d row(s) carry billable_area_m2'
                            % raw_after[1])
        if decision_rows(con) != decisions_before:
            problems.append('decisions: the decision history changed')
        fields_after = stored_field_rows(con, targets)
        for fid in targets:
            then, now = fields_before.get(fid) or {}, fields_after.get(fid) or {}
            moved = [c for c in FIELD_IDENTITY_COLUMNS
                     if not _same_text(then.get(c), now.get(c))]
            if moved:
                problems.append('written: field attribution of %d moved in %s'
                                % (fid, ', '.join(moved)))
    finally:
        con.close()
    return problems


def steady_problems(before, after):
    """После нормализации: дрейфа нет, переход -- тот же и тем же отпечатком."""
    problems = []
    counts = after['counts']
    if counts[HASH_ONLY] or counts[REFUSED]:
        problems.append('steady state: after the normalization %d hash-only '
                        'and %d refused record(s) remain'
                        % (counts[HASH_ONLY], counts[REFUSED]))
    if after['transitions'] != before['transitions']:
        problems.append('steady state: the transition moved from [%s] to [%s]'
                        % (_ids(before['transitions']),
                           _ids(after['transitions'])))
    for fid in after['transitions']:
        key = str(fid)
        if after['predicted_hash'].get(key) != before['predicted_hash'].get(
                key):
            problems.append('steady state: the rewrite of %s changed its '
                            'fingerprint during the normalization' % key)
    expected_unchanged = after['flights_in_period'] - len(after['transitions'])
    if counts[UNCHANGED] != expected_unchanged:
        problems.append('steady state: unchanged %d of %d, expected %d'
                        % (counts[UNCHANGED], after['flights_in_period'],
                           expected_unchanged))
    return problems + list(after['problems'])


# ─── Шаги существующих инструментов ─────────────────────────────────────────

def run_step(verdict, name, main_fn, argv):
    """Инструмент проекта в этом процессе, с кодом возврата в вердикт."""
    say('')
    say('== STEP %s' % name)
    try:
        code = main_fn(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
    verdict['steps'].append({'step': name, 'exit_code': code,
                             'argv': list(argv)})
    say('  %s exit code: %s' % (name, code))
    return code


def _period_args(db_path, period):
    return ['--db', db_path, '--from', period[0].isoformat(), '--to',
            period[1].isoformat()]


def _id_args(ids):
    out = []
    for fid in sorted(ids):
        out += ['--flight-id', str(fid)]
    return out


def pre_apply(verdict, db_path, oracle_path, period, out_dir, name):
    pre_json = os.path.join(out_dir, 'pre.json')
    pre_rows = os.path.join(out_dir, 'pre_rows.json')
    if run_step(verdict, 'recalc dry-run (%s)' % name, recalc.main,
                _period_args(db_path, period)
                + ['--dry-run', '--quiet', '--json', pre_json, '--rows',
                   pre_rows]) != 0:
        return 'the recalc dry run failed'
    if run_step(verdict, 'acceptance PRE-APPLY', acceptance.main,
                ['--db', db_path, '--oracle', oracle_path, '--phase',
                 'pre-apply', '--recalc-summary', pre_json, '--recalc-rows',
                 pre_rows, '--out', os.path.join(out_dir, name)]) != 0:
        return 'PRE-APPLY acceptance did not pass'
    summary = read_json(pre_json)
    verdict['pre_apply_calc_writes'] = summary.get('calc_writes')
    return None


# ─── Фазы ────────────────────────────────────────────────────────────────────

def new_verdict(phase, db_path, oracle_path, oracle, period):
    return {'tool': TOOL_ID, 'phase': phase, 'verdict': None,
            'started_at_utc': utc_now_text(),
            'database': os.path.abspath(db_path),
            'oracle': oracle.get('oracle'),
            'oracle_path': os.path.abspath(oracle_path),
            'oracle_sha256': lf_sha256(oracle_path),
            'period': [period[0].isoformat(), period[1].isoformat()],
            'algorithm': dji_area.AREA_ALGORITHM_VERSION,
            'rule_version': dji_area.RETAINED_FOOTPRINT_RULE_VERSION,
            'code_fingerprint': holdout.code_fingerprint(),
            'steps': [], 'problems': [], 'notes': []}


def finish(verdict, out_dir):
    verdict['verdict'] = 'PASS' if not verdict['problems'] else 'STOP'
    verdict['finished_at_utc'] = utc_now_text()
    write_json(os.path.join(out_dir, VERDICT_FILE), verdict)
    say('')
    for problem in verdict['problems']:
        say('  STOP: %s' % problem)
    say('CLOSEOUT %s VERDICT: %s' % (verdict['phase'].upper(),
                                     verdict['verdict']))
    say('  verdict file: %s' % os.path.join(out_dir, VERDICT_FILE))
    return EXIT_PASS if verdict['verdict'] == 'PASS' else EXIT_STOP


def _normalization_summary(result):
    return {k: result.get(k) for k in (
        'planned', 'calc_writes', 'field_writes', 'before', 'after',
        'baseline_engine', 'problems')}


def cmd_inspect(args, oracle, period, transition):
    verdict = new_verdict(PHASE_INSPECT, args.db_path, args.oracle_path,
                          oracle, period)
    sha_before = acceptance.file_sha256(args.db_path)
    result = classify(args.db_path, oracle, period, transition)
    sha_after = acceptance.file_sha256(args.db_path)
    write_classification(args.out_dir, 'classification', result)
    print_classification(result, 'DJI AREA RETAINED CLOSEOUT INSPECT')
    verdict['classification'] = {k: result[k] for k in (
        'counts', 'causes', 'hash_only', 'transitions', 'refused',
        'projected_pre_apply', 'dry_run_calc_writes', 'flights_in_period',
        'counterfactual_runs', 'v2_migration')}
    verdict['problems'] += result['problems']
    verdict['notes'] += result['notes']
    verdict['database_sha256'] = {'before': sha_before, 'after': sha_after}
    if sha_before != sha_after:
        verdict['problems'].append('read-only: the database changed while it '
                                   'was read -- stop the services and repeat')
    say('  database sha256 unchanged: %s'
        % ('yes' if sha_before == sha_after else 'NO'))
    return finish(verdict, args.out_dir)


def cmd_normalize(args, oracle, period, transition):
    verdict = new_verdict(PHASE_NORMALIZE, args.db_path, args.oracle_path,
                          oracle, period)
    try:
        with cycle_lock(args.db_path):
            result = normalize_phase(
                args.db_path, oracle, period, transition,
                os.path.join(args.out_dir, 'normalize'),
                baseline_root=args.baseline_root,
                backup_dir=args.backup_dir)
        verdict['backup'] = result.get('backup')
        verdict['normalization'] = _normalization_summary(result)
        verdict['problems'] += result['problems']
    except CloseoutError as exc:
        verdict['problems'].append(str(exc))
    except Exception as exc:  # noqa: BLE001 -- вердикт пишется всегда
        verdict['problems'].append('error: %s: %s' % (type(exc).__name__, exc))
        verdict['traceback'] = traceback.format_exc()
    return finish(verdict, args.out_dir)


def cmd_r1(args, oracle, period, transition):
    """До деплоя: нормализация и PRE-APPLY кодом выпуска."""
    verdict = new_verdict(PHASE_R1, args.db_path, args.oracle_path, oracle,
                          period)
    out = args.out_dir
    try:
        with cycle_lock(args.db_path):
            say('== BACKUP before any write')
            verdict['backup'] = make_backup(args.db_path, args.backup_dir,
                                            'r1', out)
            say('  %s  %d bytes, integrity ok, matches the live database'
                % (verdict['backup']['path'], verdict['backup']['size_bytes']))
            created = not os.path.exists(args.raw_snapshot)
            if created and run_step(verdict, 'RAW snapshot', raw_guard.main,
                                    ['--db', args.db_path, '--save',
                                     args.raw_snapshot]) != 0:
                raise CloseoutError('the RAW snapshot was not written')
            verdict['raw_snapshot'] = {'path': os.path.abspath(
                args.raw_snapshot), 'created_now': created}
            result = normalize_phase(
                args.db_path, oracle, period, transition,
                os.path.join(out, 'normalize'),
                baseline_root=args.baseline_root, backup=verdict['backup'])
            verdict['normalization'] = _normalization_summary(result)
            if result['problems']:
                verdict['problems'] += result['problems']
                raise CloseoutError('the normalization did not pass')
            if run_step(verdict, 'retained-footprint evaluator',
                        evaluator.main,
                        ['--db', args.db_path, '--out',
                         os.path.join(out, 'evaluation'),
                         '--evaluate-rule']) != 0:
                raise CloseoutError('the retained-footprint evaluator did not '
                                    'pass')
            failed = pre_apply(verdict, args.db_path, args.oracle_path, period,
                               out, 'acceptance')
            if failed:
                raise CloseoutError(failed)
            if run_step(verdict, 'RAW guard', raw_guard.main,
                        ['--db', args.db_path, '--compare',
                         args.raw_snapshot]) != 0:
                raise CloseoutError('the RAW guard found RAW or billable '
                                    'touched')
    except CloseoutError as exc:
        verdict['problems'].append(str(exc))
    except Exception as exc:  # noqa: BLE001 -- вердикт пишется всегда
        verdict['problems'].append('error: %s: %s' % (type(exc).__name__, exc))
        verdict['traceback'] = traceback.format_exc()
    return finish(verdict, out)


def cmd_r2(args, oracle, period, transition):
    """После деплоя: дрейф после r1, PRE-APPLY, ровно переход, POST-APPLY."""
    verdict = new_verdict(PHASE_R2, args.db_path, args.oracle_path, oracle,
                          period)
    out = args.out_dir
    expected = acceptance.transition_ids(transition, 'expected_rewrites')
    try:
        r1 = read_json(args.r1_verdict)
    except (IOError, OSError, ValueError) as exc:
        verdict['problems'].append('cannot read the r1 verdict: %s' % exc)
        return finish(verdict, out)
    # [REASON]: применение идёт только поверх ПРОЙДЕННОГО r1 того же оракула
    # и той же модели: иначе r2 применил бы переход, который никто не
    # принимал до деплоя.
    for key, want in (('verdict', 'PASS'), ('phase', PHASE_R1),
                      ('oracle_sha256', verdict['oracle_sha256']),
                      ('code_fingerprint', verdict['code_fingerprint'])):
        if r1.get(key) != want:
            verdict['problems'].append('r1 verdict: %s is %r, expected %r'
                                       % (key, r1.get(key), want))
    if not os.path.exists(args.raw_snapshot):
        verdict['problems'].append('the RAW snapshot %s of r1 is missing'
                                   % args.raw_snapshot)
    if not expected:
        verdict['problems'].append('the oracle names no expected rewrite')
    if verdict['problems']:
        return finish(verdict, out)
    try:
        with cycle_lock(args.db_path):
            say('== BACKUP before any write')
            verdict['backup'] = make_backup(args.db_path, args.backup_dir,
                                            'r2', out)
            say('  %s  %d bytes, integrity ok, matches the live database'
                % (verdict['backup']['path'], verdict['backup']['size_bytes']))
            result = normalize_phase(
                args.db_path, oracle, period, transition,
                os.path.join(out, 'normalize'), backup=verdict['backup'])
            verdict['normalization'] = _normalization_summary(result)
            if result['problems']:
                verdict['problems'] += result['problems']
                raise CloseoutError('the normalization did not pass')
            failed = pre_apply(verdict, args.db_path, args.oracle_path, period,
                               out, 'pre_apply')
            if failed:
                raise CloseoutError(failed + ' -- NOTHING OF THE TRANSITION '
                                    'WAS WRITTEN')
            apply_json = os.path.join(out, 'apply.json')
            if run_step(verdict, 'apply the transition', recalc.main,
                        _period_args(args.db_path, period)
                        + ['--apply', '--quiet', '--json', apply_json]
                        + _id_args(expected)) != 0:
                raise CloseoutError('the apply failed -- the backup holds the '
                                    'database before it')
            verdict['apply_calc_writes'] = read_json(apply_json).get(
                'calc_writes')
            targeted_json = os.path.join(out, 'targeted.json')
            if run_step(verdict, 'targeted dry-run of the rewrites',
                        recalc.main,
                        _period_args(args.db_path, period)
                        + ['--dry-run', '--quiet', '--json', targeted_json]
                        + _id_args(expected)) != 0:
                raise CloseoutError('the targeted dry run failed')
            targeted = read_json(targeted_json)
            verdict['targeted_calc_writes'] = targeted.get('calc_writes')
            if targeted.get('flights_in_period') != len(expected) or \
                    targeted.get('calc_writes') != {'unchanged': len(expected)}:
                raise CloseoutError(
                    'the targeted dry run is %s over %r flight(s), expected '
                    'unchanged %d' % (json.dumps(targeted.get('calc_writes'),
                                                 sort_keys=True),
                                      targeted.get('flights_in_period'),
                                      len(expected)))
            second_json = os.path.join(out, 'second.json')
            if run_step(verdict, 'second dry-run of the period', recalc.main,
                        _period_args(args.db_path, period)
                        + ['--dry-run', '--quiet', '--json',
                           second_json]) != 0:
                raise CloseoutError('the second dry run failed')
            verdict['second_calc_writes'] = read_json(second_json).get(
                'calc_writes')
            if run_step(verdict, 'acceptance POST-APPLY', acceptance.main,
                        ['--db', args.db_path, '--oracle', args.oracle_path,
                         '--phase', 'post-apply', '--recalc-summary',
                         second_json, '--apply-summary', apply_json, '--out',
                         os.path.join(out, 'post_apply')]) != 0:
                raise CloseoutError('POST-APPLY acceptance did not pass -- '
                                    'the backup holds the database before '
                                    'the apply')
            if run_step(verdict, 'RAW guard', raw_guard.main,
                        ['--db', args.db_path, '--compare',
                         args.raw_snapshot]) != 0:
                raise CloseoutError('the RAW guard found RAW or billable '
                                    'touched')
    except CloseoutError as exc:
        verdict['problems'].append(str(exc))
    except Exception as exc:  # noqa: BLE001 -- вердикт пишется всегда
        verdict['problems'].append('error: %s: %s' % (type(exc).__name__, exc))
        verdict['traceback'] = traceback.format_exc()
    return finish(verdict, out)


# ─── Командная строка ────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_area_retained_release_closeout.py',
        description='Close out the DJI-AREA-RETAINED-FOOTPRINT-001 release: '
                    'prove and normalize fingerprint-only drift, then run '
                    'R1 (before the deploy) or R2 (after it).')
    sub = parser.add_subparsers(dest='command')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--db', dest='db_path', required=True, metavar='PATH')
    common.add_argument('--oracle', dest='oracle_path', required=True,
                        metavar='PATH')
    common.add_argument('--out', dest='out_dir', required=True, metavar='DIR',
                        help='a new or empty directory for every artifact')
    sub.add_parser(PHASE_INSPECT, parents=[common],
                   help='read-only: classify the period and print the plan')
    normalize = sub.add_parser(PHASE_NORMALIZE, parents=[common],
                               help='backup, then normalize the proven rows')
    normalize.add_argument('--backup-dir', dest='backup_dir', required=True,
                           metavar='DIR')
    normalize.add_argument('--baseline-root', dest='baseline_root',
                           metavar='DIR', help='checkout of the code production '
                           'runs now: its own recalc must see the normalized '
                           'rows unchanged')
    r1 = sub.add_parser(PHASE_R1, parents=[common],
                        help='before the deploy: normalize + PRE-APPLY')
    r1.add_argument('--backup-dir', dest='backup_dir', required=True,
                    metavar='DIR')
    r1.add_argument('--raw-snapshot', dest='raw_snapshot', required=True,
                    metavar='FILE', help='saved here if absent, compared at '
                    'the end')
    r1.add_argument('--baseline-root', dest='baseline_root', metavar='DIR')
    r2 = sub.add_parser(PHASE_R2, parents=[common],
                        help='after the deploy: apply exactly the transition')
    r2.add_argument('--backup-dir', dest='backup_dir', required=True,
                    metavar='DIR')
    r2.add_argument('--raw-snapshot', dest='raw_snapshot', required=True,
                    metavar='FILE')
    r2.add_argument('--r1-verdict', dest='r1_verdict', required=True,
                    metavar='FILE')
    return parser


COMMANDS = {PHASE_INSPECT: cmd_inspect, PHASE_NORMALIZE: cmd_normalize,
            PHASE_R1: cmd_r1, PHASE_R2: cmd_r2}


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_PASS
    if args.command not in COMMANDS:
        parser.print_help()
        return EXIT_USAGE
    if not os.path.exists(args.db_path):
        # [REASON]: sqlite3.connect создал бы пустой файл, и прогон «по нулю
        # строк» прошёл бы любые ворота.
        say('ERROR: database not found at %s - refusing to run.'
            % args.db_path)
        return EXIT_NO_DATABASE
    try:
        oracle, period, transition = load_oracle(args.oracle_path)
    except ValueError as exc:
        say('ERROR: %s' % exc)
        return EXIT_USAGE
    running = acceptance.rule_problems(oracle)
    if running:
        for problem in running:
            say('ERROR: %s' % problem)
        return EXIT_USAGE
    if os.path.isdir(args.out_dir) and os.listdir(args.out_dir):
        say('ERROR: %s is not empty -- every run gets its own directory'
            % args.out_dir)
        return EXIT_USAGE
    os.makedirs(args.out_dir, exist_ok=True)
    try:
        con = connect_ro(args.db_path)
        try:
            store.require_tables(con)
        finally:
            con.close()
    except (store.StoreError, sqlite3.Error) as exc:
        say('ERROR: %s' % exc)
        return EXIT_USAGE
    say('%s  %s  (algorithm %s, rule %s)'
        % (TOOL_ID, args.command, dji_area.AREA_ALGORITHM_VERSION,
           dji_area.RETAINED_FOOTPRINT_RULE_VERSION))
    return COMMANDS[args.command](args, oracle, period, transition)


if __name__ == '__main__':
    sys.exit(main())
