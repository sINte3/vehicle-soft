# -*- coding: utf-8 -*-
"""dji_area/control_store.py -- решения администратора и журнал циклов площади.

DRONE-AREA-CONTROL-V2-MEGA. Единственный писатель двух таблиц миграции
`DRONE_AREA_CONTROL_V2_001`:

* `drone_area_decisions` -- append-only история решений администратора по
  записи площади DJI (блок B);
* `drone_area_cycle_runs` -- журнал прогонов цикла FLIGHTS -> MANIFEST ->
  SOURCES -> RECALC: по расписанию и по кнопке «Обновить данные DJI» (блок D).

Stdlib sqlite3, без Flask: модуль зовут и веб-маршруты (своим соединением к
той же базе, как приёмник источников зовёт `dji_area/store.py`), и
`tools/dji_area_daily.py`, и тесты без приложения.

[REASON]: отдельный модуль, а не раздел `dji_area/store.py`. Тот файл входит
в замороженный отпечаток кода (`tools/dji_area_holdout.py:FROZEN_FILES`), и
любая его правка ломает закреплённый отпечаток в двух ранбуках. Таблиц
`dji_*` этот модуль не пишет: `dji_area_calculations` только ЧИТАЕТСЯ, чтобы
запомнить, против какого автоматического расчёта принято решение.
"""

import json
import os
import re
import socket
from datetime import datetime, timedelta

from dji_area import accounting as acc
from dji_area import decisions as dec
from dji_area import store

DECISIONS_TABLE = 'drone_area_decisions'
RUNS_TABLE = 'drone_area_cycle_runs'

# ─── Журнал циклов: словарь ─────────────────────────────────────────────────

TRIGGER_MANUAL = 'MANUAL'
TRIGGER_SCHEDULED = 'SCHEDULED'
TRIGGERS = (TRIGGER_MANUAL, TRIGGER_SCHEDULED)

STATUS_QUEUED = 'QUEUED'
STATUS_RUNNING = 'RUNNING'
STATUS_SUCCESS = 'SUCCESS'
STATUS_WARNINGS = 'SUCCESS_WITH_WARNINGS'
STATUS_FAILED = 'FAILED'
STATUS_BUSY = 'BUSY'
STATUS_INTERRUPTED = 'INTERRUPTED'
STATUS_LAUNCH_FAILED = 'LAUNCH_FAILED'
STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_SUCCESS, STATUS_WARNINGS,
            STATUS_FAILED, STATUS_BUSY, STATUS_INTERRUPTED,
            STATUS_LAUNCH_FAILED)
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)
SUCCESS_STATUSES = (STATUS_SUCCESS, STATUS_WARNINGS)
ERROR_STATUSES = (STATUS_FAILED, STATUS_BUSY, STATUS_INTERRUPTED,
                  STATUS_LAUNCH_FAILED)

# [REASON]: очередь, которую никто не взял за это время, -- не «ожидает», а
# несостоявшийся запуск: задача планировщика не поднялась, процесс не
# стартовал. Пятнадцать минут -- с запасом на ожидание блокировки у
# одновременно работающего цикла по расписанию не рассчитаны: такой прогон
# держит блокировку, и очередь тогда считается живой (см. effective_status).
QUEUED_GRACE = timedelta(minutes=15)

STATUS_LABELS = {
    None: ('Не запускалось', 'Ишга туширилмаган'),
    STATUS_QUEUED: ('Ожидает запуска', 'Ишга тушишини кутмоқда'),
    STATUS_RUNNING: ('Выполняется', 'Бажарилмоқда'),
    STATUS_SUCCESS: ('Успешно', 'Муваффақиятли'),
    STATUS_WARNINGS: ('Успешно, с предупреждениями',
                      'Муваффақиятли, огоҳлантиришлар билан'),
    STATUS_FAILED: ('Ошибка', 'Хато'),
    STATUS_BUSY: ('Не запущено: шёл другой сбор',
                  'Ишга тушмади: бошқа йиғим кетаётган эди'),
    STATUS_INTERRUPTED: ('Прервано', 'Узилиб қолди'),
    STATUS_LAUNCH_FAILED: ('Запуск не состоялся', 'Ишга тушириш амалга '
                           'ошмади'),
}
STATUS_BADGES = {
    None: '',
    STATUS_QUEUED: 'vs-badge-info',
    STATUS_RUNNING: 'vs-badge-info',
    STATUS_SUCCESS: 'vs-badge-success',
    STATUS_WARNINGS: 'vs-badge-warning',
    STATUS_FAILED: 'vs-badge-danger',
    STATUS_BUSY: 'vs-badge-warning',
    STATUS_INTERRUPTED: 'vs-badge-danger',
    STATUS_LAUNCH_FAILED: 'vs-badge-danger',
}
TRIGGER_LABELS = {
    TRIGGER_MANUAL: ('вручную', 'қўлда'),
    TRIGGER_SCHEDULED: ('по расписанию', 'жадвал бўйича'),
}

STEP_FLIGHTS = 'FLIGHTS'
STEP_MANIFEST = 'MANIFEST'
STEP_SOURCES = 'SOURCES'
STEP_VERIFY = 'VERIFY'
STEP_RECALC = 'RECALC'
STEP_LABELS = {
    STEP_FLIGHTS: ('сбор вылетов DJI', 'DJI парвозларини йиғиш'),
    STEP_MANIFEST: ('список для проверки V4', 'V4 текшируви рўйхати'),
    STEP_SOURCES: ('получение доказательств V4', 'V4 далилларини олиш'),
    STEP_VERIFY: ('проверка полноты доказательств',
                  'далиллар тўлиқлигини текшириш'),
    STEP_RECALC: ('пересчёт площади', 'майдонни қайта ҳисоблаш'),
}

# Итог цикла (result_json.outcome) -- то же, что статус завершённой строки.
FAILURE_CANDIDATE_EVIDENCE = 'CANDIDATE_EVIDENCE_MISSING'
WARNING_CONTROL_EVIDENCE = 'CONTROL_EVIDENCE_MISSING'
WARNING_CANDIDATE_NO_V4 = 'CANDIDATE_NO_V4_AT_SOURCE'
WARNING_NO_V4_CHECK = 'NO_V4_CHECK_UNAVAILABLE'
FAILURE_LAUNCH = 'LAUNCH_FAILED'

# Итог прогона словами: код итога (`result_json.failure` / `warnings`) ->
# (ru, uz). Узбекский -- кириллицей.
#
# [REASON]: на экран идут ЭТИ фразы, а не колонка `message`. Та -- ASCII по
# построению (её пишет исполнитель в журнал планировщика и прогоняет через
# `redact`), может нести путь файла блокировки, pid и имя хоста и остаётся
# технической строкой для администратора. Пользователь RU/UZ должен понять,
# что произошло и что делать, не читая английский журнал.
FAILURE_TEXTS = {
    FAILURE_CANDIDATE_EVIDENCE: (
        'Доказательства V4 не получены для кандидатов: %(candidates)d. '
        'Пересчёт выполнен по уже полученным; следующий прогон доберёт '
        'недостающее.',
        'Номзодлар учун V4 далиллари олинмади: %(candidates)d. Қайта ҳисоб '
        'олинганлари бўйича бажарилди; кейинги ишга тушириш етишмаганини '
        'йиғиб олади.'),
    'VERIFY_UNAVAILABLE': (
        'Не удалось проверить полноту доказательств: повторный список для '
        'проверки V4 не получен. Пересчёт выполнен.',
        'Далиллар тўлиқлигини текшириб бўлмади: V4 текшируви учун қайта '
        'рўйхат олинмади. Қайта ҳисоб бажарилди.'),
    'COLLECTOR_BUSY': (
        'Сборщик DJI был занят другим прогоном (например, ночным сбором '
        'вылетов). Повторите позже.',
        'DJI йиғувчиси бошқа ишга тушириш билан банд эди (масалан, тунги '
        'парвозлар йиғими). Кейинроқ такрорланг.'),
    'STEP_FAILED': (
        'Шаг «%(step)s» завершился ошибкой.',
        '«%(step)s» қадами хато билан тугади.'),
    'MANIFEST_TOO_LARGE': (
        'Список для проверки V4 больше допустимого; к DJI за доказательствами '
        'не обращались. Нужна проверка администратором.',
        'V4 текшируви рўйхати рухсат этилгандан катта; далиллар учун DJI га '
        'мурожаат қилинмади. Администратор текшируви керак.'),
    'NOT_IDEMPOTENT': (
        'Повторный пересчёт изменил записи; нужна проверка администратором.',
        'Такрорий қайта ҳисоб ёзувларни ўзгартирди; администратор текшируви '
        'керак.'),
    'CYCLE_BUSY': (
        'Шёл другой цикл площади; этот прогон не запускался. Повторите '
        'позже.',
        'Бошқа майдон цикли кетаётган эди; бу ишга тушириш бошланмади. '
        'Кейинроқ такрорланг.'),
    'UNEXPECTED_ERROR': (
        'Непредвиденная ошибка исполнителя; подробности — в журнале.',
        'Бажарувчининг кутилмаган хатоси; тафсилотлар — журналда.'),
    'NO_DATABASE': ('База данных не найдена.', 'Маълумотлар базаси топилмади.'),
    'USAGE': ('Ошибка параметров запуска исполнителя.',
              'Бажарувчини ишга тушириш параметрлари хато.'),
    FAILURE_LAUNCH: (
        'Задача планировщика или процесс исполнителя не стартовали.',
        'Жадвалдаги вазифа ёки бажарувчи жараён бошланмади.'),
}
WARNING_TEXTS = {
    WARNING_CONTROL_EVIDENCE: (
        'Доказательства V4 не получены только для контрольных вылетов: '
        '%(controls)d. На итоги площади это не влияет.',
        'V4 далиллари фақат назорат парвозлари учун олинмади: %(controls)d. '
        'Бу майдон якунларига таъсир қилмайди.'),
    WARNING_CANDIDATE_NO_V4: (
        'DJI не хранит V4 для кандидатов: %(no_v4)d. Это не сбой сбора, и '
        'повтор не поможет: такие записи остаются по площади DJI RAW как '
        '«недостаточно доказательств» и автоматически не обнуляются; решение '
        'по ним может принять администратор.',
        'DJI номзодлар учун V4 ни сақламайди: %(no_v4)d. Бу йиғим хатоси '
        'эмас ва такрорлаш ёрдам бермайди: бундай ёзувлар DJI RAW майдони '
        'бўйича «далиллар етарли эмас» ҳолатида қолади ва автоматик нолга '
        'туширилмайди; улар бўйича қарорни администратор қабул қилиши '
        'мумкин.'),
    WARNING_NO_V4_CHECK: (
        'Не удалось проверить, хранит ли DJI V4 для всех кандидатов: '
        'повторный список не получен. Сбор источников полный, пересчёт '
        'выполнен.',
        'DJI барча номзодлар учун V4 ни сақлашини текшириб бўлмади: қайта '
        'рўйхат олинмади. Манбалар йиғими тўлиқ, қайта ҳисоб бажарилди.'),
}
STATUS_TEXTS = {
    'INTERRUPTED': (
        'Процесс завершился, не закрыв прогон (перезапуск службы или сбой). '
        'Запустите обновление ещё раз — повтор безопасен.',
        'Жараён ишга туширишни ёпмай тугади (хизмат қайта ишга туширилган '
        'ёки носозлик). Янгилашни яна ишга туширинг — такрорлаш хавфсиз.'),
    'LAUNCH_FAILED': (
        'Прогон никто не взял из очереди: проверьте задачу планировщика и '
        'настройки службы.',
        'Ишга туширишни навбатдан ҳеч ким олмади: жадвалдаги вазифа ва '
        'хизмат созламаларини текширинг.'),
    'BUSY': (
        'Исполнитель не дождался окончания другого сбора. Повторите позже.',
        'Бажарувчи бошқа йиғим тугашини кутиб ўтирмади. Кейинроқ '
        'такрорланг.'),
}


class ControlStoreError(RuntimeError):
    pass


class DecisionRefused(ValueError):
    def __init__(self, code):
        ValueError.__init__(self, code)
        self.code = code


class RunActive(RuntimeError):
    """Уже есть ожидающий или выполняющийся прогон."""

    def __init__(self, run):
        RuntimeError.__init__(self, 'a cycle run is already active')
        self.run = run


def utcnow():
    return datetime.utcnow().replace(microsecond=0)


def _iso(dt):
    return store.iso(dt)


def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def tables_present(con):
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return DECISIONS_TABLE in names and RUNS_TABLE in names


# Триггеры миграции DRONE_AREA_CONTROL_V2_001, запрещающие UPDATE/DELETE.
APPEND_ONLY_TRIGGERS = ('trg_drone_area_decisions_no_update',
                        'trg_drone_area_decisions_no_delete')


def append_only_guarded(con):
    """Стоят ли оба триггера append-only на истории решений.

    [REASON]: `db.create_all()` при старте приложения создаёт обе таблицы по
    моделям -- решения пишутся и без миграции, но без триггеров история
    защищена только тем, что единственный писатель делает INSERT. Экран
    говорит администратору об этом, а не делает вид, что миграция не нужна."""
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'")}
    return all(name in names for name in APPEND_ONLY_TRIGGERS)


def require_tables(con):
    if not tables_present(con):
        raise ControlStoreError(
            'tables %s/%s are missing - run '
            'migrate_drone_area_control_v2_001.py first'
            % (DECISIONS_TABLE, RUNS_TABLE))


def _rows(cursor):
    return [dict(row) for row in cursor.fetchall()]


def _chunks(ids, size=400):
    ids = sorted({int(i) for i in ids})
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


# ─── Решения администратора ─────────────────────────────────────────────────

def _decision_dict(row):
    out = dict(row)
    out['performed_at'] = _parse_dt(out.get('performed_at'))
    out['is_override'] = bool(out.get('is_override'))
    return out


def decision_chains(con, flight_ids):
    """{flight_id: [строки по chain_seq]} -- вся история, включая замещённые."""
    out = {}
    for chunk in _chunks(flight_ids):
        cursor = con.execute(
            'SELECT * FROM drone_area_decisions WHERE flight_id IN (%s) '
            'ORDER BY flight_id, chain_seq' % ','.join('?' * len(chunk)),
            chunk)
        for row in cursor.fetchall():
            item = _decision_dict(row)
            out.setdefault(int(item['flight_id']), []).append(item)
    return out


def active_from_chain(chain):
    """Действующее решение цепочки либо None (последняя строка -- REVOKE)."""
    if not chain:
        return None
    last = chain[-1]
    if last['decision_type'] == dec.REVOKE:
        return None
    return last


def active_decisions(con, flight_ids):
    """{flight_id: действующее решение} -- только вылеты, где оно есть."""
    out = {}
    for flight_id, chain in decision_chains(con, flight_ids).items():
        active = active_from_chain(chain)
        if active is not None:
            out[flight_id] = active
    return out


def history(con, flight_ids):
    """Плоская история для листа книги: каждая строка с признаком «действует»."""
    out = []
    for flight_id, chain in sorted(decision_chains(con, flight_ids).items()):
        active = active_from_chain(chain)
        for item in chain:
            entry = dict(item)
            entry['is_current'] = active is not None and \
                item['id'] == active['id']
            out.append(entry)
    return out


def _auto_figures(calc):
    """(класс, причина, RAW, авто-принято, авто-исключено) -- как в отчёте."""
    auto = acc.classify(calc)
    cls = auto['accounting_class']
    raw = auto['raw_area_m2']
    if cls == acc.PHANTOM_PROVEN:
        accepted = auto['accounted_area_m2']
        excluded = auto['confirmed_overstatement_m2']
    else:
        accepted = raw
        excluded = 0.0 if raw is not None else None
    return cls, auto['reason'], raw, accepted, excluded


def current_calculation(con, flight_id):
    row = store.current_calculation(con, flight_id)
    return dict(row) if row is not None else None


def record_decision(con, flight_id, action, comment, confirmed,
                    override_confirmed, expected_active_id, user_id,
                    user_name, now=None, expected_calc_id=None):
    """Записать решение (или отмену) одной строкой. Возвращает новую строку.

    Идентичность автоматического расчёта берётся ЗДЕСЬ, из текущей строки
    `dji_area_calculations` под действующей версией алгоритма, а не из
    формы: браузер не может подсунуть чужой отпечаток.

    ``expected_calc_id`` -- id строки расчёта, которую видел администратор.

    [REASON]: сверка обоих «что видел человек» -- и действующего решения, и
    автоматического расчёта. Без второй «принять автоматический результат»,
    нажатое по корректировке 0.1 га, записалось бы против пересчёта,
    случившегося, пока форма была открыта, -- и приняло бы уже весь RAW.
    Id строки расчёта устойчив: при том же входе строка реактивируется с
    тем же id, при новом входе появляется новая.

    Отказ -- `DecisionRefused(code)` с кодом из `dji_area.decisions.ERRORS`;
    база при отказе не меняется.
    """
    now = now or utcnow()
    flight_id = int(flight_id)
    store.begin_immediate(con)
    try:
        calc = current_calculation(con, flight_id)
        if calc is None:
            raise DecisionRefused(dec.E_NOT_DECIDABLE)
        if expected_calc_id is not None \
                and int(expected_calc_id) != int(calc['id']):
            raise DecisionRefused(dec.E_STALE_CALC)
        cls, reason, raw, auto_accepted, auto_excluded = _auto_figures(calc)
        chain = decision_chains(con, [flight_id]).get(flight_id, [])
        active = active_from_chain(chain)
        refusal = dec.validate(action, cls, raw, active, comment, confirmed,
                               override_confirmed, expected_active_id,
                               active_stale=dec.decision_is_stale(active,
                                                                  calc))
        if refusal:
            raise DecisionRefused(refusal)
        if action == dec.REVOKE:
            state, eff_accepted, eff_excluded, _applied = dec.effective(
                cls, raw, auto_accepted, auto_excluded, None)
        else:
            state, eff_accepted, eff_excluded, _applied = dec.effective(
                cls, raw, auto_accepted, auto_excluded,
                {'decision_type': action})
        last = chain[-1] if chain else None
        values = {
            'flight_id': flight_id,
            'chain_seq': (last['chain_seq'] + 1) if last else 1,
            'supersedes_decision_id': last['id'] if last else None,
            'decision_type': action,
            'calculation_id': calc.get('id'),
            'area_algorithm_version': calc.get('area_algorithm_version'),
            'calculation_input_hash': calc.get('calculation_input_hash'),
            'auto_class': cls,
            'auto_reason': reason,
            'raw_area_m2': raw,
            'auto_accepted_m2': auto_accepted,
            'auto_excluded_m2': auto_excluded,
            'effective_accepted_m2': eff_accepted,
            'effective_excluded_m2': eff_excluded,
            'is_override': 1 if dec.is_override(cls) else 0,
            'comment': (comment or '').strip(),
            'performed_by_user_id': user_id,
            'performed_by_name': (user_name or '')[:150] or None,
            'performed_at': _iso(now),
            'decisions_version': dec.DECISIONS_VERSION,
        }
        cols = list(values)
        con.execute('INSERT INTO drone_area_decisions (%s) VALUES (%s)'
                    % (', '.join(cols), ', '.join('?' for _ in cols)),
                    [values[c] for c in cols])
        new_id = con.execute('SELECT last_insert_rowid()').fetchone()[0]
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise
    values['id'] = new_id
    values['state'] = state
    values['performed_at'] = now
    values['is_override'] = bool(values['is_override'])
    return values


# ─── Журнал циклов ──────────────────────────────────────────────────────────

def _run_dict(row):
    if row is None:
        return None
    out = dict(row)
    for key in ('requested_at', 'started_at', 'finished_at', 'heartbeat_at'):
        out[key] = _parse_dt(out.get(key))
    try:
        out['result'] = json.loads(out.get('result_json') or '{}')
    except ValueError:
        out['result'] = {}
    return out


def get_run(con, run_id):
    return _run_dict(con.execute(
        'SELECT * FROM drone_area_cycle_runs WHERE id=?',
        (int(run_id),)).fetchone())


def active_runs(con):
    return [_run_dict(r) for r in con.execute(
        "SELECT * FROM drone_area_cycle_runs WHERE status IN ('QUEUED', "
        "'RUNNING') ORDER BY id").fetchall()]


def latest_runs(con, limit=10):
    return [_run_dict(r) for r in con.execute(
        'SELECT * FROM drone_area_cycle_runs ORDER BY id DESC LIMIT ?',
        (int(limit),)).fetchall()]


def last_success(con):
    return _run_dict(con.execute(
        "SELECT * FROM drone_area_cycle_runs WHERE status IN ('SUCCESS', "
        "'SUCCESS_WITH_WARNINGS') ORDER BY finished_at DESC, id DESC "
        "LIMIT 1").fetchone())


def effective_status(run, lock_held, now=None):
    """Статус для показа. Ничего не пишет.

    [REASON]: GET-страница не имеет права писать в базу (урок
    AUDIT-GET-SIDE-EFFECT), поэтому «процесс умер, не дописав строку»
    вычисляется при чтении, а записывается только там, где запись уместна:
    при следующем запуске (POST) и в начале прогона-исполнителя.
    RUNNING при свободной блокировке -- мёртвый процесс: исполнитель берёт
    блокировку ДО перевода строки в RUNNING и отпускает ПОСЛЕ её закрытия.
    """
    if run is None:
        return None
    status = run.get('status')
    now = now or utcnow()
    if status == STATUS_RUNNING and not lock_held:
        return STATUS_INTERRUPTED
    if status == STATUS_QUEUED and not lock_held:
        requested = run.get('requested_at')
        if requested is not None and now - requested > QUEUED_GRACE:
            return STATUS_LAUNCH_FAILED
    return status


def reconcile(con, lock_held, now=None, keep_run_id=None):
    """Закрыть мёртвые строки: RUNNING без блокировки, QUEUED сверх срока.

    Вызывается только из пишущих путей. ``keep_run_id`` -- строка, которую
    исполнитель как раз ведёт (её не трогать, даже если блокировку взял он).
    ``lock_held`` -- bool либо вызываемое без аргументов (проба блокировки).
    Возвращает id закрытых строк.

    [REASON]: пробу блокировки веб-путь передаёт ВЫЗЫВАЕМЫМ, и она
    выполняется уже ПОД транзакцией писателя. Исполнитель переводит строку в
    RUNNING, держа блокировку файла, и отпускает её только после того, как
    закрыл строку своей транзакцией. Под нашей транзакцией закрыть строку он
    не может; значит, RUNNING при свободной блокировке здесь -- мёртвый
    процесс, а не прогон, взявший блокировку за миг до нашей пробы.
    """
    now = now or utcnow()
    closed = []
    store.begin_immediate(con)
    try:
        if callable(lock_held):
            lock_held = bool(lock_held())
        for run in active_runs(con):
            if run['id'] == keep_run_id:
                continue
            status = effective_status(run, lock_held, now)
            if status == STATUS_INTERRUPTED or (
                    status == STATUS_LAUNCH_FAILED
                    and run['status'] == STATUS_QUEUED):
                message = ('The process ended without closing the run '
                           '(service restart or crash).'
                           if status == STATUS_INTERRUPTED else
                           'The queued run was never picked up.')
                con.execute(
                    'UPDATE drone_area_cycle_runs SET status=?, '
                    'active_slot=NULL, finished_at=?, message=? WHERE id=? '
                    "AND status IN ('QUEUED', 'RUNNING')",
                    (status, _iso(now), message, run['id']))
                closed.append(run['id'])
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise
    return closed


def enqueue_manual(con, user_id, user_name, now=None):
    """Поставить ручной прогон в очередь. `RunActive`, если что-то уже идёт.

    [REASON]: проверка и вставка -- в одной немедленной транзакции, а
    уникальный частичный индекс по `active_slot` страхует от двух
    одновременных запросов на уровне базы: второй INSERT падает, и ответом
    ему служит уже существующий прогон.
    """
    import sqlite3

    now = now or utcnow()
    store.begin_immediate(con)
    try:
        active = active_runs(con)
        if active:
            con.execute('ROLLBACK')
            raise RunActive(active[0])
        try:
            con.execute(
                'INSERT INTO drone_area_cycle_runs (trigger_kind, status, '
                'active_slot, requested_by_user_id, requested_by_name, '
                'requested_at, host) VALUES (?, ?, 1, ?, ?, ?, ?)',
                (TRIGGER_MANUAL, STATUS_QUEUED, user_id,
                 (user_name or '')[:150] or None, _iso(now),
                 socket.gethostname()[:100]))
        except sqlite3.IntegrityError:
            con.execute('ROLLBACK')
            raise RunActive((active_runs(con) or [None])[0])
        run_id = con.execute('SELECT last_insert_rowid()').fetchone()[0]
        con.execute('COMMIT')
    except RunActive:
        raise
    except Exception:
        con.execute('ROLLBACK')
        raise
    return get_run(con, run_id)


def claim_queued(con, pid=None, host=None, now=None):
    """Исполнитель берёт самый старый ручной прогон из очереди: -> RUNNING."""
    now = now or utcnow()
    store.begin_immediate(con)
    try:
        row = con.execute(
            "SELECT id FROM drone_area_cycle_runs WHERE status='QUEUED' "
            'ORDER BY id LIMIT 1').fetchone()
        if row is None:
            con.execute('COMMIT')
            return None
        con.execute(
            "UPDATE drone_area_cycle_runs SET status='RUNNING', started_at=?, "
            "heartbeat_at=?, pid=?, host=? WHERE id=? AND status='QUEUED'",
            (_iso(now), _iso(now), pid or os.getpid(),
             (host or socket.gethostname())[:100], row[0]))
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise
    return get_run(con, row[0])


def start_scheduled(con, window_from=None, window_to=None, pid=None,
                    host=None, now=None):
    """Прогон по расписанию: сразу RUNNING, без `active_slot`.

    [REASON]: слот держит только ручной прогон. Прогон по расписанию
    сериализуется блокировкой цикла, а слот ему не нужен: иначе ручной
    запрос, поставленный за секунду до расписания, сорвал бы расписание.
    """
    now = now or utcnow()
    store.begin_immediate(con)
    try:
        con.execute(
            'INSERT INTO drone_area_cycle_runs (trigger_kind, status, '
            'requested_at, started_at, heartbeat_at, window_from, window_to, '
            'pid, host) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (TRIGGER_SCHEDULED, STATUS_RUNNING, _iso(now), _iso(now),
             _iso(now), window_from.isoformat() if window_from else None,
             window_to.isoformat() if window_to else None,
             pid or os.getpid(), (host or socket.gethostname())[:100]))
        run_id = con.execute('SELECT last_insert_rowid()').fetchone()[0]
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise
    return get_run(con, run_id)


def _update(con, run_id, **fields):
    store.begin_immediate(con)
    try:
        cols = sorted(fields)
        con.execute('UPDATE drone_area_cycle_runs SET %s WHERE id=?'
                    % ', '.join('%s=?' % c for c in cols),
                    [fields[c] for c in cols] + [int(run_id)])
        con.execute('COMMIT')
    except Exception:
        con.execute('ROLLBACK')
        raise


def set_window(con, run_id, window_from, window_to):
    _update(con, run_id,
            window_from=window_from.isoformat() if window_from else None,
            window_to=window_to.isoformat() if window_to else None)


def set_step(con, run_id, step, now=None):
    now = now or utcnow()
    _update(con, run_id, current_step=step, heartbeat_at=_iso(now))


def finish(con, run_id, status, exit_code=None, failed_step=None,
           message=None, result=None, now=None):
    """Закрыть прогон. Слот освобождается в той же записи."""
    if status not in STATUSES or status in ACTIVE_STATUSES:
        raise ControlStoreError('not a final status: %s' % status)
    now = now or utcnow()
    _update(con, run_id, status=status, active_slot=None,
            finished_at=_iso(now), heartbeat_at=_iso(now),
            exit_code=exit_code, failed_step=failed_step,
            message=redact(message) if message else None,
            result_json=json.dumps(result, sort_keys=True,
                                   ensure_ascii=True) if result else None)


# ─── Секреты не уходят ни в журнал, ни на экран ──────────────────────────────

_SECRET_NAME = re.compile(r'(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|COOKIE)',
                          re.IGNORECASE)
_SECRET_MIN_LEN = 6
MESSAGE_MAX_CHARS = 2000


def secret_values(environ=None):
    """Значения переменных окружения с «секретными» именами."""
    environ = os.environ if environ is None else environ
    out = []
    for name, value in environ.items():
        if _SECRET_NAME.search(name or '') and value \
                and len(value) >= _SECRET_MIN_LEN:
            out.append(value)
    return sorted(set(out), key=len, reverse=True)


def redact(text, secrets=None):
    """Текст без значений секретов и без хвостов вида token=..., ASCII.

    [REASON]: сообщение прогона показывается на экране и лежит в базе.
    Шаги цикла по построению секретов не печатают, но сообщение об ошибке
    приходит и из исключений -- их текст мы не контролируем. Поэтому
    фильтр стоит на выходе, а не на доверии.
    """
    if text is None:
        return None
    out = str(text)
    for value in (secret_values() if secrets is None else secrets):
        if value:
            out = out.replace(value, '***')
    out = re.sub(r'(?i)(token|password|secret|cookie)(["\']?\s*[:=]\s*)'
                 r'["\']?[^\s"\'&,;]+', r'\1\2***', out)
    out = out.encode('ascii', 'replace').decode('ascii')
    return out[:MESSAGE_MAX_CHARS]


def run_explanation(status, result, failed_step_label, lang):
    """Итог прогона одной-двумя фразами на языке пользователя; '' -- если
    сказать нечего сверх статуса (успех без предупреждений, идущий прогон)."""
    pick = dec.pick
    result = result or {}
    misses = result.get('evidence_misses') or {}
    no_v4 = len(result.get('candidates_no_v4_at_source') or []) or int(
        (result.get('manifest') or {}).get('no_v4_at_source') or 0)
    counts = {'candidates': len(misses.get('candidates') or []),
              'controls': len(misses.get('controls') or []),
              'no_v4': no_v4,
              'step': failed_step_label or '—'}
    parts = []
    if status in STATUS_TEXTS and status not in (STATUS_FAILED,):
        parts.append(pick(STATUS_TEXTS[status], lang))
    failure = result.get('failure')
    if failure and status not in (STATUS_INTERRUPTED,):
        pair = FAILURE_TEXTS.get(failure) or FAILURE_TEXTS['UNEXPECTED_ERROR']
        text = pick(pair, lang) % counts
        if text not in parts:
            parts.append(text)
    for warning in result.get('warnings') or ():
        pair = WARNING_TEXTS.get(warning)
        if pair:
            parts.append(pick(pair, lang) % counts)
    if not parts and status == STATUS_FAILED:
        parts.append(pick(FAILURE_TEXTS['UNEXPECTED_ERROR'], lang))
    return ' '.join(parts)


def run_view(run, lang, lock_held, now=None):
    """Строка журнала для экрана: подписи, время UTC+5, итог словами."""
    if run is None:
        return None
    pick = dec.pick
    status = effective_status(run, lock_held, now)
    local = lambda dt: dt + timedelta(hours=5) if dt else None  # noqa: E731
    result = run.get('result') or {}
    failed_step_label = pick(STEP_LABELS[run['failed_step']], lang) \
        if run.get('failed_step') in STEP_LABELS else ''
    return {
        'id': run['id'],
        'trigger': run.get('trigger_kind'),
        'trigger_label': pick(TRIGGER_LABELS.get(
            run.get('trigger_kind'), ('', '')), lang),
        'status': status,
        'status_label': pick(STATUS_LABELS.get(status,
                                               STATUS_LABELS[None]), lang),
        'status_badge': STATUS_BADGES.get(status, ''),
        'is_active': status in ACTIVE_STATUSES,
        'requested_by': run.get('requested_by_name'),
        'requested_local': local(run.get('requested_at')),
        'started_local': local(run.get('started_at')),
        'finished_local': local(run.get('finished_at')),
        'step': run.get('current_step'),
        'step_label': pick(STEP_LABELS[run['current_step']], lang)
        if run.get('current_step') in STEP_LABELS else '',
        'failed_step_label': pick(STEP_LABELS[run['failed_step']], lang)
        if run.get('failed_step') in STEP_LABELS else '',
        'window_from': run.get('window_from'),
        'window_to': run.get('window_to'),
        'exit_code': run.get('exit_code'),
        # Техническая ASCII-строка журнала -- только администратору.
        'message': run.get('message'),
        'explanation': run_explanation(status, result, failed_step_label,
                                       lang),
        'result': result,
    }
