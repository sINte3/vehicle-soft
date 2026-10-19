# -*- coding: utf-8 -*-
"""Where the points live: monthly SQLite files and the watermark. Stdlib only.

DECISION G3 -- POINTS ARE NOT IN transport.db
About a million points a day, thirty million a month. Kept in the application's
database they would dominate its size, its backups and its VACUUM time, and the
90-day retention would be a DELETE of tens of millions of rows followed by a
rebuild of the file the service is reading from. Kept in monthly files,
retention is `del gps_points_202605.db*` and costs nothing.

The `*` is not decoration: the files run in WAL mode, so each one is three
(`-wal` and `-shm` beside it) and deleting only the first leaves the other two.

WHY THE WATERMARK IS IN A FILE OF ITS OWN
It must outlive the points it describes. A monthly file is deleted by
retention; if the watermarks lived inside one, deleting May would tell the
collector it had never collected anything, and the next run would re-fetch from
the beginning for every object.
"""

import collections
import os
import sqlite3
from datetime import datetime, timedelta

from . import config

POINTS_PREFIX = "gps_points_"
STATE_FILE = "gps_collector_state.db"

CREATE_POINTS = """
    CREATE TABLE IF NOT EXISTS points (
        unit_id INTEGER NOT NULL,
        t       INTEGER NOT NULL,
        lon     REAL    NOT NULL,
        lat     REAL    NOT NULL,
        speed   REAL    NOT NULL,
        course  INTEGER,
        sats    INTEGER,
        ign     REAL,
        PRIMARY KEY (unit_id, t)
    ) WITHOUT ROWID
"""

# [REASON]: GPS-24, метод «время». `ign` -- сырое значение параметра датчика
# зажигания в сообщении (wialon.ignition_value); NULL -- у объекта нет датчика
# зажигания, сообщение параметра не несло, или точка легла до GPS-24. Файлы
# месяцев, созданные раньше, колонки не имеют, а CREATE TABLE IF NOT EXISTS
# колонку никогда не добавит -- поэтому open_points проверяет её через PRAGMA
# и добавляет ALTER: в SQLite нет ADD COLUMN IF NOT EXISTS. Колонка
# допускает NULL, и ALTER не переписывает файл: месяц на тридцать миллионов
# точек получает её мгновенно. Откат кода её не убирает и не должен: старый
# код пишет точки по именам колонок, `ign` остаётся NULL.
IGNITION_COLUMN = "ign"

CREATE_STATE = """
    CREATE TABLE IF NOT EXISTS collector_watermarks (
        unit_id    INTEGER PRIMARY KEY,
        last_t     INTEGER NOT NULL,
        updated_at TEXT    NOT NULL
    )
"""

# [REASON]: имена объектов Wialon. Список парка с именами коллектор получает
# каждым прогоном (`list_units`), но до 09.10.2026 только печатал их -- и
# экран «Факт по технике» показывал объект без строки сопоставления голым
# четырёхзначным номером: владелец не мог понять, что это за машина. Имя
# лежит в файле отметок, а не в помесячном файле точек, по той же причине,
# что watermark: ретенция удаляет месяц, а сутки, посчитанные по нему,
# остаются на экране и должны остаться названными.
CREATE_UNITS = """
    CREATE TABLE IF NOT EXISTS collector_units (
        unit_id INTEGER PRIMARY KEY,
        name    TEXT    NOT NULL,
        seen_at TEXT    NOT NULL
    )
"""


# [REASON]: GPS-25, приёмка метода «время». Снимок счётчика моточасов Wialon
# каждым прогоном: `cneh` (часы) на момент последнего сообщения объекта
# `last_t`. Разность двух снимков -- моточасы, которые Wialon насчитал сам по
# тому же датчику зажигания, против них проверяется наш расчёт. Лежит в
# файле отметок, а не в файле месяца: ретенция удаляет месяц, а проверка
# окна на стыке месяцев читает оба снимка. Строки только добавляются; ключ
# (unit_id, last_t) -- объект без новых сообщений нового снимка не даёт.
CREATE_COUNTERS = """
    CREATE TABLE IF NOT EXISTS collector_counters (
        unit_id  INTEGER NOT NULL,
        last_t   INTEGER NOT NULL,
        cneh     REAL    NOT NULL,
        cfl      INTEGER,
        taken_at TEXT    NOT NULL,
        PRIMARY KEY (unit_id, last_t)
    ) WITHOUT ROWID
"""


def month_key(stamp):
    """'YYYYMM' of an epoch second, in local time (UTC+5).

    [REASON]: local, not UTC. The file a point lands in has to agree with the
    date the hectares are booked under, and those are local everywhere in this
    project. With UTC months, every point between 19:00 and 24:00 on the last
    day of a month would be filed under the next one.
    """
    return datetime.fromtimestamp(int(stamp), config.TZ).strftime("%Y%m")


def points_path(folder, month):
    return os.path.join(folder, "%s%s.db" % (POINTS_PREFIX, month))


def state_path(folder):
    return os.path.join(folder, STATE_FILE)


def _connect(path, create_sql):
    fresh = not os.path.exists(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    if fresh:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute(create_sql)
    con.commit()
    return con


def open_points(folder, month):
    """Connection to the month's point file, schema in place."""
    con = _connect(points_path(folder, month), CREATE_POINTS)
    _ensure_ignition_column(con)
    return con


def _ensure_ignition_column(con):
    columns = {row[1] for row in con.execute("PRAGMA table_info(points)")}
    if IGNITION_COLUMN in columns:
        return
    try:
        con.execute("ALTER TABLE points ADD COLUMN %s REAL" % IGNITION_COLUMN)
        con.commit()
    except sqlite3.OperationalError as problem:
        # [REASON]: сборщик и дозаполнение могут открыть один старый файл
        # одновременно; оба увидят, что колонки нет, и второй ALTER получит
        # «duplicate column name». Колонка уже есть -- это успех, не ошибка.
        if "duplicate column" not in str(problem):
            raise


def open_state(folder):
    """Connection to the watermark file, schema in place."""
    return _connect(state_path(folder), CREATE_STATE)


def open_units(folder):
    """Connection to the object names -- the same file as the watermarks."""
    return _connect(state_path(folder), CREATE_UNITS)


def write_unit_names(folder, units):
    """Remember the Wialon name of every object of the fleet list.

    `units` is what Client.list_units() returns: [{"id", "name", "last_t"}].
    Returns how many names were stored.

    [REASON]: an object with an empty name in the answer is skipped, not
    stored: replacing a known name with an empty one would bring the bare
    number back on the screen. A row is never deleted -- an object removed
    from Wialon keeps its last name, because the days computed while it
    existed are still on the screen.
    """
    seen_at = datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for unit in units:
        name = (unit.get("name") or "").strip()
        if unit.get("id") and name:
            rows.append((int(unit["id"]), name, seen_at))
    if not rows:
        return 0
    con = open_units(folder)
    try:
        con.executemany(
            "INSERT INTO collector_units (unit_id, name, seen_at) "
            "VALUES (?, ?, ?) ON CONFLICT(unit_id) DO UPDATE SET "
            "name = excluded.name, seen_at = excluded.seen_at", rows)
        con.commit()
    finally:
        con.close()
    return len(rows)


def open_counters(folder):
    """Connection to the counter snapshots -- the same file as the watermarks."""
    return _connect(state_path(folder), CREATE_COUNTERS)


def write_counters(folder, units):
    """Remember Wialon's engine-hours counter of every object of the list.

    `units` is what Client.list_units() returns. Returns how many new
    snapshots were stored.

    [REASON]: an object without a counter or without a last message gives no
    snapshot -- a counter without the time it belongs to cannot bound a
    window. A repeated (object, last_t) is ignored, not replaced: the first
    snapshot of that moment stays the one the windows were built on.
    """
    taken_at = datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for unit in units:
        cneh, last_t = unit.get("cneh"), unit.get("last_t")
        if unit.get("id") and cneh is not None and last_t is not None:
            cfl = unit.get("cfl")
            rows.append((int(unit["id"]), int(last_t), float(cneh),
                         None if cfl is None else int(cfl), taken_at))
    if not rows:
        return 0
    con = open_counters(folder)
    try:
        changes = con.total_changes
        con.executemany(
            "INSERT OR IGNORE INTO collector_counters "
            "(unit_id, last_t, cneh, cfl, taken_at) VALUES (?, ?, ?, ?, ?)",
            rows)
        con.commit()
        return con.total_changes - changes
    finally:
        con.close()


def read_ignition(folder, unit_id, t_from, t_to):
    """[(t, ign)] of one object's points with t_from <= t <= t_to, in order.

    Read only: a month never collected is not created, and a file from before
    GPS-24 (no `ign` column) answers with None for every point.
    """
    out = []
    for month in _months_between(t_from, t_to + 1):
        path = points_path(folder, month)
        if not os.path.exists(path):
            continue
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=30)
        try:
            columns = {row[1] for row in con.execute("PRAGMA table_info(points)")}
            ign = IGNITION_COLUMN if IGNITION_COLUMN in columns else "NULL"
            out.extend((int(t), None if value is None else float(value))
                       for t, value in con.execute(
                           "SELECT t, %s FROM points WHERE unit_id = ? AND "
                           "t >= ? AND t <= ? ORDER BY t" % ign,
                           (int(unit_id), int(t_from), int(t_to))))
        finally:
            con.close()
    return out


# [REASON]: обе цифры возвращаются, а не одна. Журнал приёма (GPS-7) держит
# тождество seen = written + duplicate + no_position по образцу инварианта
# drone_sync_logs, и вывести `duplicate` задним числом неоткуда: повторы
# проглатывает сам ключ, молча и без следа. Третья цифра -- сколько из
# дублей принесли зажигание точке, лёгшей без него (GPS-24); в тождество она
# не входит: точка от этого не стала ни новой, ни потерянной.
WriteResult = collections.namedtuple("WriteResult",
                                     "inserted duplicate ignition_filled")


def _ignition(row):
    return None if len(row) < 8 or row[7] is None else float(row[7])


def write_points(folder, rows):
    """Store (unit_id, t, lon, lat, speed, course, sats[, ign]) rows.

    Returns WriteResult(inserted, duplicate, ignition_filled): сколько строк
    легло, сколько ключ отбросил как уже известные, и скольким из уже
    известных повтор принёс зажигание, которого у них не было.

    Rows are split by the month of their own timestamp, so an interval that
    crosses midnight of the first lands partly in each file and loses nothing.

    [REASON]: the (unit_id, t) primary key is what makes a re-run free. The
    watermark can only ever move backwards by accident -- a crash, a manual
    reset, a re-collection ordered by hand -- and every one of those replays
    an interval that is already stored. Without the key the replay would
    double every hectare computed from it.

    [REASON]: a replay changes nothing of a stored point but a missing `ign`.
    Coordinates, speed and satellites stay as first written -- the hectares
    already computed from them must not move -- and a known `ign` is never
    replaced, so the first value that arrived wins, as the coordinates do.
    """
    by_month = {}
    for row in rows:
        by_month.setdefault(month_key(row[1]), []).append(row)
    inserted = changed = 0
    for month in sorted(by_month):
        con = open_points(folder, month)
        try:
            cur = con.execute("SELECT COUNT(*) FROM points")
            before = cur.fetchone()[0]
            changes = con.total_changes
            con.executemany(
                "INSERT INTO points "
                "(unit_id, t, lon, lat, speed, course, sats, ign) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(unit_id, t) DO UPDATE SET ign = excluded.ign "
                "WHERE points.ign IS NULL AND excluded.ign IS NOT NULL",
                [(int(r[0]), int(r[1]), float(r[2]), float(r[3]), float(r[4]),
                  None if r[5] is None else int(r[5]),
                  None if r[6] is None else int(r[6]),
                  _ignition(r)) for r in by_month[month]])
            con.commit()
            added = con.execute("SELECT COUNT(*) FROM points").fetchone()[0] - before
            inserted += added
            changed += con.total_changes - changes - added
        finally:
            con.close()
    return WriteResult(inserted=inserted, duplicate=len(rows) - inserted,
                       ignition_filled=changed)


FillResult = collections.namedtuple("FillResult", "filled offered")


def fill_ignition(folder, rows):
    """Give stored points the ignition they were written without.

    `rows` are (unit_id, t, ign). Returns FillResult(filled, offered): how many
    points got an `ign`, of how many rows with a value were offered.

    [REASON]: UPDATE only, never INSERT -- this is the whole difference from
    write_points. The refill re-reads past weeks from Wialon, and among those
    messages are ones the nightly run never stored (delivered after the
    watermark had passed them). Inserting them would change the points of
    days already computed, and a recalculation would then move hectares the
    operators have already answered for. The refill brings ignition and
    nothing else.
    """
    by_month = {}
    offered = 0
    for unit_id, stamp, value in rows:
        if value is None:
            continue
        offered += 1
        by_month.setdefault(month_key(stamp), []).append(
            (float(value), int(unit_id), int(stamp)))
    filled = 0
    for month in sorted(by_month):
        if not os.path.exists(points_path(folder, month)):
            continue
        con = open_points(folder, month)
        try:
            changes = con.total_changes
            con.executemany(
                "UPDATE points SET ign = ? "
                "WHERE unit_id = ? AND t = ? AND ign IS NULL", by_month[month])
            con.commit()
            filled += con.total_changes - changes
        finally:
            con.close()
    return FillResult(filled=filled, offered=offered)


def ignition_gaps(folder, unit_id, t_from, t_to):
    """(count, first_t, last_t) of the object's points in [t_from, t_to)
    that have no `ign`; (0, None, None) when there are none.

    Read without opening for writing: a month never collected is not created.
    """
    count, first, last = 0, None, None
    for month in _months_between(t_from, t_to):
        path = points_path(folder, month)
        if not os.path.exists(path):
            continue
        con = sqlite3.connect(path, timeout=30)
        try:
            columns = {row[1] for row in con.execute("PRAGMA table_info(points)")}
            where = ("unit_id = ? AND t >= ? AND t < ?"
                     + (" AND ign IS NULL" if IGNITION_COLUMN in columns else ""))
            n, lo, hi = con.execute(
                "SELECT COUNT(*), MIN(t), MAX(t) FROM points WHERE " + where,
                (int(unit_id), int(t_from), int(t_to))).fetchone()
        finally:
            con.close()
        if n:
            count += n
            first = lo if first is None else min(first, lo)
            last = hi if last is None else max(last, hi)
    return count, first, last


def ignition_coverage(folder, month):
    """(points, points with ign) in one monthly file; (0, 0) if there is none."""
    path = points_path(folder, month)
    if not os.path.exists(path):
        return 0, 0
    con = sqlite3.connect(path, timeout=30)
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(points)")}
        total = con.execute("SELECT COUNT(*) FROM points").fetchone()[0]
        if IGNITION_COLUMN not in columns:
            return total, 0
        with_ign = con.execute(
            "SELECT COUNT(*) FROM points WHERE ign IS NOT NULL").fetchone()[0]
        return total, with_ign
    finally:
        con.close()


def _months_between(t_from, t_to):
    """'YYYYMM' of every local month touched by [t_from, t_to)."""
    if t_to <= t_from:
        return []
    months = []
    current = datetime.fromtimestamp(int(t_from), config.TZ).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)
    last = month_key(int(t_to) - 1)
    while True:
        key = current.strftime("%Y%m")
        months.append(key)
        if key >= last:
            return months
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)


def read_watermark(folder, unit_id):
    """Epoch second of the last message stored for the object, or None."""
    con = open_state(folder)
    try:
        row = con.execute("SELECT last_t FROM collector_watermarks "
                          "WHERE unit_id = ?", (int(unit_id),)).fetchone()
        return None if row is None else int(row[0])
    finally:
        con.close()


def set_watermark(folder, unit_id, stamp):
    """Move the watermark. Called ONLY after the points are committed.

    [REASON]: the order is the whole point. Points are written and committed
    first, the watermark second: a crash between the two replays an interval
    that is already stored (and INSERT OR IGNORE swallows it), while the
    opposite order would leave a hole in the history that nothing would ever
    notice. Holes are invisible -- a day simply comes out with fewer hectares.
    """
    con = open_state(folder)
    try:
        con.execute(
            "INSERT INTO collector_watermarks (unit_id, last_t, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(unit_id) DO UPDATE SET "
            "last_t = excluded.last_t, updated_at = excluded.updated_at",
            (int(unit_id), int(stamp),
             datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
    finally:
        con.close()


def read_day(folder, unit_id, day):
    """One object's points for one local day: [(t, lon, lat, speed, sats)].

    `day` is 'YYYY-MM-DD' local. A local day never straddles two monthly files
    -- months are whole days by construction -- so exactly one file is opened,
    and a missing file means "we have no points", not an error.
    """
    start = int(datetime.strptime(day, "%Y-%m-%d")
                .replace(tzinfo=config.TZ).timestamp())
    finish = start + 86400
    path = points_path(folder, datetime.fromtimestamp(start, config.TZ)
                       .strftime("%Y%m"))
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(path, timeout=30)
    try:
        return [(int(t), float(lon), float(lat), float(speed),
                 -1 if sats is None else int(sats))
                for t, lon, lat, speed, sats in con.execute(
                    "SELECT t, lon, lat, speed, sats FROM points "
                    "WHERE unit_id = ? AND t >= ? AND t < ? ORDER BY t",
                    (int(unit_id), start, finish))]
    finally:
        con.close()


def units_with_points(folder, day):
    """Objects that have at least one point on that local day."""
    start = int(datetime.strptime(day, "%Y-%m-%d")
                .replace(tzinfo=config.TZ).timestamp())
    path = points_path(folder, datetime.fromtimestamp(start, config.TZ)
                       .strftime("%Y%m"))
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(path, timeout=30)
    try:
        return [int(r[0]) for r in con.execute(
            "SELECT DISTINCT unit_id FROM points WHERE t >= ? AND t < ? "
            "ORDER BY unit_id", (start, start + 86400))]
    finally:
        con.close()
