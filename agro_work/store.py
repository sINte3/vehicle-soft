# -*- coding: utf-8 -*-
"""Запись импорта agro-work в transport.db. Только stdlib `sqlite3`.

У таблиц `agro_work_*` один писатель -- этот модуль (импорт, связки, методы).
Экран сверки их только читает, отдельным соединением `mode=ro`.

НИЧЕГО НЕ УДАЛЯЕТ. Заявка, пропавшая из полной выгрузки, получает отметку
`gone_at` и остаётся; вернувшаяся -- отметку снимает, и оба события лежат в
журнале. Связка владельца снимается отметкой `unlinked_at`. Журнал и история
статусов защищены от UPDATE и DELETE триггерами миграции AGRO_WORK_001.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import records

TABLES = ('agro_work_import_runs', 'agro_work_work_types',
          'agro_work_transports', 'agro_work_transport_links',
          'agro_work_applications', 'agro_work_status_events',
          'agro_work_changes')

SOURCE_IMPORT = 'import'
SOURCE_LINKS = 'links'
SOURCE_METHODS = 'methods'

ENTITY_APPLICATION = 'application'
ENTITY_TRANSPORT = 'transport'
ENTITY_WORK_TYPE = 'work_type'
ENTITY_LINK = 'link'

RUN_RUNNING = 'running'
RUN_OK = 'ok'
RUN_INCOMPLETE = 'incomplete'
RUN_ERROR = 'error'
RUN_INTERRUPTED = 'interrupted'

# [REASON]: прогон со статусом `running` моложе этого срока -- живой, и второй
# рядом с ним не запускается: два писателя одних строк дали бы журнал, в
# котором одно изменение записано дважды. Старше -- процесс убит (окно
# консоли закрыли), и строка честно становится `interrupted`.
STALE_RUNNING_HOURS = 6

RUN_COUNTERS = ('api_count', 'pages', 'rows_seen', 'rows_new', 'rows_updated',
                'rows_unchanged', 'rows_rejected', 'rows_duplicate',
                'rows_gone', 'history_fetched', 'history_failed',
                'history_pending', 'requests', 'logins', 'refreshes')

STAMP = '%Y-%m-%d %H:%M:%S'


class AnotherRunActive(Exception):
    """Другой прогон импорта ещё идёт."""


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def stamp(moment):
    return moment.astimezone(timezone.utc).strftime(STAMP)


def connect(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    return con


# [REASON]: подсказка не называет папку. Те же инструменты работают и на копии
# (C:\VehicleSoft_AgroWork), и на боевой базе (C:\transport-report); прежний
# текст «cd C:\VehicleSoft_AgroWork» отправил бы человека с боевого сервера
# повторять команду на копии -- запись ушла бы не в ту базу.
READONLY_HINT = ('the database is read-only for this window - open PowerShell '
                 'as administrator, go to the same folder and repeat; '
                 'nothing was written')


def is_readonly(exc):
    """Отказ SQLite писать в базу, которую этому процессу можно только читать.

    [REASON]: копию базы создаёт окно PowerShell от имени администратора, и
    обычное окно того же пользователя открывает её только на чтение: SQLite
    молча опускается в чтение, пробный прогон проходит, а запись падает
    трассировкой «attempt to write a readonly database» (шаг 10, 29.09).
    Человеку нужны причина и действие, а не трассировка.
    """
    return (isinstance(exc, sqlite3.OperationalError)
            and 'readonly database' in str(exc))


def missing_tables(con):
    have = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return [table for table in TABLES if table not in have]


def _value(value):
    return None if value is None else str(value)


def journal(con, source, entity, entity_id, field, old, new, when, run_id=None):
    con.execute(
        'INSERT INTO agro_work_changes (run_id, source, entity, entity_id, '
        'field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (run_id, source, entity, str(entity_id), field, _value(old),
         _value(new), when))


# --- журнал прогона ------------------------------------------------------------

def start_run(con, version, now):
    """Новая строка журнала прогона; отказ, если другой прогон жив."""
    for row in con.execute("SELECT id, started_at FROM agro_work_import_runs "
                           "WHERE status = ?", (RUN_RUNNING,)).fetchall():
        started = datetime.strptime(row['started_at'], STAMP).replace(
            tzinfo=timezone.utc)
        if now - started < timedelta(hours=STALE_RUNNING_HOURS):
            raise AnotherRunActive(row['id'])
    con.execute("UPDATE agro_work_import_runs SET status = ?, finished_at = ?, "
                "message = 'the process ended without finishing the run' "
                "WHERE status = ?", (RUN_INTERRUPTED, stamp(now), RUN_RUNNING))
    cursor = con.execute(
        'INSERT INTO agro_work_import_runs (started_at, status, tool_version) '
        'VALUES (?, ?, ?)', (stamp(now), RUN_RUNNING, version))
    con.commit()
    return cursor.lastrowid


def finish_run(con, run_id, status, counters, message=None, detail=None):
    values = [counters.get(name) if name in ('api_count', 'history_pending')
              else int(counters.get(name) or 0) for name in RUN_COUNTERS]
    con.execute(
        'UPDATE agro_work_import_runs SET status = ?, finished_at = ?, %s, '
        'message = ?, detail_json = ? WHERE id = ?'
        % ', '.join('%s = ?' % name for name in RUN_COUNTERS),
        [status, stamp(utc_now())] + values
        + [message, json.dumps(detail, ensure_ascii=False, sort_keys=True)
           if detail is not None else None, run_id])
    con.commit()


# --- виды работ -----------------------------------------------------------------

def upsert_work_type(con, record, when, run_id):
    """Вид работы из справочника. Колонку `method` не трогает никогда."""
    row = con.execute('SELECT name, unit, unit_display FROM agro_work_work_types '
                      'WHERE id = ?', (record['id'],)).fetchone()
    if row is None:
        con.execute('INSERT INTO agro_work_work_types (id, name, unit, '
                    'unit_display, first_seen_at, last_seen_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (record['id'], record['name'], record['unit'],
                     record['unit_display'], when, when))
        return 'new'
    changes = [(field, row[field], record[field])
               for field in records.WORK_TYPE_FIELDS
               if row[field] != record[field]]
    con.execute('UPDATE agro_work_work_types SET name = ?, unit = ?, '
                'unit_display = ?, last_seen_at = ? WHERE id = ?',
                (record['name'], record['unit'], record['unit_display'], when,
                 record['id']))
    for field, old, new in changes:
        journal(con, SOURCE_IMPORT, ENTITY_WORK_TYPE, record['id'], field, old,
                new, when, run_id)
    return 'updated' if changes else 'unchanged'


def ensure_work_type(con, record, when):
    """Вид работы, известный только по заявке: вставить, если его нет.

    [REASON]: не обновлять. Вложенное `work_type_info` заявки беднее
    справочника, и обновление из него переписывало бы справочник каждым
    прогоном, записывая в журнал «правки», которых в agro-work не было.
    """
    con.execute('INSERT OR IGNORE INTO agro_work_work_types (id, name, unit, '
                'unit_display, first_seen_at, last_seen_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (record['id'], record['name'], record['unit'],
                 record['unit_display'], when, when))


# --- машины ---------------------------------------------------------------------

def upsert_transport(con, record, when, run_id):
    """Машина из реестра `/transports/`."""
    fields = records.TRANSPORT_FIELDS
    row = con.execute('SELECT %s FROM agro_work_transports WHERE id = ?'
                      % ', '.join(fields), (record['id'],)).fetchone()
    if row is None:
        con.execute('INSERT INTO agro_work_transports (id, %s, in_registry, '
                    'match_status, first_seen_at, last_seen_at) '
                    'VALUES (?, %s, 1, ?, ?, ?)'
                    % (', '.join(fields), ', '.join('?' * len(fields))),
                    [record['id']] + [record[f] for f in fields]
                    + ['none', when, when])
        return 'new'
    changes = [(field, row[field], record[field]) for field in fields
               if row[field] != record[field]]
    con.execute('UPDATE agro_work_transports SET %s, in_registry = 1, '
                'last_seen_at = ? WHERE id = ?'
                % ', '.join('%s = ?' % f for f in fields),
                [record[f] for f in fields] + [when, record['id']])
    for field, old, new in changes:
        journal(con, SOURCE_IMPORT, ENTITY_TRANSPORT, record['id'], field, old,
                new, when, run_id)
    return 'updated' if changes else 'unchanged'


def ensure_transport(con, record, when):
    """Машина, известная только по заявке (в реестре её нет): вставить.

    True, если строка вставлена сейчас.
    """
    fields = records.TRANSPORT_FIELDS
    cursor = con.execute('INSERT OR IGNORE INTO agro_work_transports (id, %s, '
                         'in_registry, match_status, first_seen_at, '
                         'last_seen_at) VALUES (?, %s, 0, ?, ?, ?)'
                         % (', '.join(fields), ', '.join('?' * len(fields))),
                         [record['id']] + [record[f] for f in fields]
                         + ['none', when, when])
    return cursor.rowcount == 1


def transport_rows(con):
    return [dict(row) for row in con.execute(
        'SELECT id, plate_number, plate_norm, equipment_id, match_status '
        'FROM agro_work_transports ORDER BY id')]


def equipment_rows(con):
    return [dict(row) for row in con.execute(
        'SELECT id, name, plate FROM equipment ORDER BY id')]


def active_links(con):
    return {row['agro_transport_id']: row['equipment_id'] for row in con.execute(
        'SELECT agro_transport_id, equipment_id FROM agro_work_transport_links '
        'WHERE unlinked_at IS NULL')}


def apply_resolution(con, resolution, when, run_id, source=SOURCE_IMPORT,
                     fresh=frozenset()):
    """Записать связь машин с нашей техникой. Число изменённых строк.

    `fresh` -- машины, вставленные этим же прогоном.
    """
    changed = 0
    for transport_id, item in sorted(resolution.items()):
        row = con.execute('SELECT equipment_id, match_status '
                          'FROM agro_work_transports WHERE id = ?',
                          (transport_id,)).fetchone()
        if row is None:
            continue
        if (row['equipment_id'], row['match_status']) == (
                item['equipment_id'], item['status']):
            continue
        con.execute('UPDATE agro_work_transports SET equipment_id = ?, '
                    'match_status = ? WHERE id = ?',
                    (item['equipment_id'], item['status'], transport_id))
        changed += 1
        if transport_id in fresh:
            # [REASON]: машина появилась в этом же прогоне, и её первая связь
            # -- знакомство, а не правка. Журнал первичной загрузки иначе
            # начинался бы с трёхсот строк «none -> auto». Набор передаётся
            # явно: сравнение меток времени путало бы два прогона одной
            # секунды.
            continue
        if row['equipment_id'] != item['equipment_id']:
            journal(con, source, ENTITY_TRANSPORT, transport_id,
                    'equipment_id', row['equipment_id'], item['equipment_id'],
                    when, run_id)
        if row['match_status'] != item['status']:
            journal(con, source, ENTITY_TRANSPORT, transport_id,
                    'match_status', row['match_status'], item['status'], when,
                    run_id)
    return changed


# --- заявки -----------------------------------------------------------------------

def upsert_application(con, record, run_id, when):
    """'new' | 'updated' | 'unchanged'. Каждое изменённое поле -- в журнал."""
    fields = records.APPLICATION_FIELDS
    row = con.execute('SELECT %s, gone_at FROM agro_work_applications '
                      'WHERE id = ?' % ', '.join(fields),
                      (record['id'],)).fetchone()
    if row is None:
        con.execute('INSERT INTO agro_work_applications (id, %s, '
                    'first_seen_run_id, last_seen_run_id) VALUES (?, %s, ?, ?)'
                    % (', '.join(fields), ', '.join('?' * len(fields))),
                    [record['id']] + [record[f] for f in fields]
                    + [run_id, run_id])
        return 'new'
    changes = [(field, row[field], record[field]) for field in fields
               if row[field] != record[field]]
    con.execute('UPDATE agro_work_applications SET %s, last_seen_run_id = ?, '
                'gone_at = NULL WHERE id = ?'
                % ', '.join('%s = ?' % f for f in fields),
                [record[f] for f in fields] + [run_id, record['id']])
    for field, old, new in changes:
        journal(con, SOURCE_IMPORT, ENTITY_APPLICATION, record['id'], field,
                old, new, when, run_id)
    if row['gone_at'] is not None:
        # Вернулась в выгрузку: отметка снята, и это тоже событие.
        journal(con, SOURCE_IMPORT, ENTITY_APPLICATION, record['id'], 'gone_at',
                row['gone_at'], None, when, run_id)
        return 'updated'
    return 'updated' if changes else 'unchanged'


def mark_gone(con, run_id, when):
    """Отметить заявки, которых нет в ПОЛНОЙ выгрузке этого прогона.

    Вызывается только когда выгрузка полна: неполная выгрузка -- не
    доказательство исчезновения (тот же принцип, что у обхода каталога DJI:
    «incomplete is not deletion»).
    """
    ids = [row[0] for row in con.execute(
        'SELECT id FROM agro_work_applications WHERE last_seen_run_id <> ? '
        'AND gone_at IS NULL ORDER BY id', (run_id,))]
    for app_id in ids:
        con.execute('UPDATE agro_work_applications SET gone_at = ? WHERE id = ?',
                    (when, app_id))
        journal(con, SOURCE_IMPORT, ENTITY_APPLICATION, app_id, 'gone_at', None,
                when, when, run_id)
    return len(ids)


# --- история статусов ---------------------------------------------------------------

_HISTORY_NEEDED = (
    "FROM agro_work_applications WHERE gone_at IS NULL "
    "AND status IN ('COMPLETED', 'CANCELLED') "
    "AND (history_updated_at IS NULL OR history_updated_at <> updated_at)")


def history_queue(con, limit):
    """Заявки, которым нужна история, новейшие первыми.

    [REASON]: история нужна ради дат закрытия и отмены, поэтому только
    закрытым заявкам. Открытая получит её, когда закроется: смена статуса
    сдвигает её `updated_at` (B0: у 149 из 150 выполненных `updated_at` --
    это момент закрытия), и она попадёт сюда как изменившаяся. Повторно
    история берётся только у заявок, чей `updated_at` сдвинулся с прошлой
    загрузки истории, -- это и есть «только для новых и изменившихся».

    [REASON]: новейшие первыми. Факт GPS собирается с 20.08 -- старые заявки
    сверять пока не с чем, а растянутая первичная загрузка должна первым делом
    дать то, что сверку оживит.
    """
    if limit <= 0:
        return []
    return [(row[0], row[1]) for row in con.execute(
        'SELECT id, updated_at ' + _HISTORY_NEEDED
        + ' ORDER BY created_day DESC, created_at DESC, id LIMIT ?', (limit,))]


def history_pending_count(con):
    return con.execute('SELECT COUNT(*) ' + _HISTORY_NEEDED).fetchone()[0]


DERIVED_FIELDS = ('initial_status', 'completed_at', 'completed_day',
                  'cancelled_at')


def store_history(con, app_id, listed_updated_at, events, when, run_id):
    """Слить события истории и пересчитать выведенные даты заявки."""
    for event in events:
        con.execute(
            'INSERT OR IGNORE INTO agro_work_status_events (application_id, '
            'action, old_status, new_status, changed_at, changed_fields, '
            'fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (app_id, event['action'], event['old_status'], event['new_status'],
             event['changed_at'], event['changed_fields'], when))
    stored = [dict(row) for row in con.execute(
        'SELECT action, old_status, new_status, changed_at FROM '
        'agro_work_status_events WHERE application_id = ?', (app_id,))]
    derived = dict(zip(DERIVED_FIELDS, records.derive_dates(stored)))
    before = con.execute('SELECT %s FROM agro_work_applications WHERE id = ?'
                         % ', '.join(DERIVED_FIELDS), (app_id,)).fetchone()
    con.execute('UPDATE agro_work_applications SET history_updated_at = ?, '
                'history_fetched_at = ?, %s WHERE id = ?'
                % ', '.join('%s = ?' % f for f in DERIVED_FIELDS),
                [listed_updated_at, when] + [derived[f] for f in DERIVED_FIELDS]
                + [app_id])
    # [REASON]: первая загрузка истории (NULL -> дата) -- не правка, а
    # знакомство, и в журнал не идёт: иначе первичная загрузка положила бы в
    # него четыре тысячи строк шума. Сменившаяся дата закрытия -- правка.
    for field in DERIVED_FIELDS:
        if before[field] is not None and before[field] != derived[field]:
            journal(con, SOURCE_IMPORT, ENTITY_APPLICATION, app_id, field,
                    before[field], derived[field], when, run_id)
    return derived


# --- выборки для CSV и сводки ---------------------------------------------------------

def unmatched_transports(con):
    """Машины без связи с нашей техникой, с числом их заявок."""
    return [dict(row) for row in con.execute(
        "SELECT t.id, t.plate_number, t.plate_norm, t.brand_name, t.model, "
        "t.category_name, t.company_name, t.in_registry, t.match_status, "
        "COUNT(a.id) AS applications, MAX(a.created_day) AS last_day "
        "FROM agro_work_transports t "
        "LEFT JOIN agro_work_applications a ON a.transport_id = t.id "
        "AND a.gone_at IS NULL "
        "WHERE t.match_status IN ('none', 'ambiguous') "
        "GROUP BY t.id ORDER BY applications DESC, t.plate_norm, t.id")]


def match_summary(con):
    out = {status: 0 for status in ('manual', 'auto', 'ambiguous', 'none')}
    for row in con.execute('SELECT match_status, COUNT(*) FROM '
                           'agro_work_transports GROUP BY match_status'):
        out[row[0]] = row[1]
    with_apps = con.execute(
        "SELECT COUNT(DISTINCT t.id) FROM agro_work_transports t "
        "JOIN agro_work_applications a ON a.transport_id = t.id "
        "WHERE t.match_status IN ('none', 'ambiguous') "
        "AND a.gone_at IS NULL").fetchone()[0]
    out['unmatched_with_applications'] = with_apps
    return out
