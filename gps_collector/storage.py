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
from datetime import datetime

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
        PRIMARY KEY (unit_id, t)
    ) WITHOUT ROWID
"""

CREATE_STATE = """
    CREATE TABLE IF NOT EXISTS collector_watermarks (
        unit_id    INTEGER PRIMARY KEY,
        last_t     INTEGER NOT NULL,
        updated_at TEXT    NOT NULL
    )
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
    return _connect(points_path(folder, month), CREATE_POINTS)


def open_state(folder):
    """Connection to the watermark file, schema in place."""
    return _connect(state_path(folder), CREATE_STATE)


# [REASON]: обе цифры возвращаются, а не одна. Журнал приёма (GPS-7) держит
# тождество seen = written + duplicate + no_position по образцу инварианта
# drone_sync_logs, и вывести `duplicate` задним числом неоткуда: повторы
# проглатывает сам ключ, молча и без следа.
WriteResult = collections.namedtuple("WriteResult", "inserted duplicate")


def write_points(folder, rows):
    """Store (unit_id, t, lon, lat, speed, course, sats) rows.

    Returns WriteResult(inserted, duplicate): сколько строк легло и сколько
    ключ отбросил как уже известные.

    Rows are split by the month of their own timestamp, so an interval that
    crosses midnight of the first lands partly in each file and loses nothing.

    [REASON]: INSERT OR IGNORE against the (unit_id, t) primary key is what
    makes a re-run free. The watermark can only ever move backwards by accident
    -- a crash, a manual reset, a re-collection ordered by hand -- and every one
    of those replays an interval that is already stored. Without the key the
    replay would double every hectare computed from it.
    """
    by_month = {}
    for row in rows:
        by_month.setdefault(month_key(row[1]), []).append(row)
    inserted = 0
    for month in sorted(by_month):
        con = open_points(folder, month)
        try:
            cur = con.execute("SELECT COUNT(*) FROM points")
            before = cur.fetchone()[0]
            con.executemany(
                "INSERT OR IGNORE INTO points "
                "(unit_id, t, lon, lat, speed, course, sats) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(int(r[0]), int(r[1]), float(r[2]), float(r[3]), float(r[4]),
                  None if r[5] is None else int(r[5]),
                  None if r[6] is None else int(r[6])) for r in by_month[month]])
            con.commit()
            inserted += con.execute("SELECT COUNT(*) FROM points").fetchone()[0] - before
        finally:
            con.close()
    return WriteResult(inserted=inserted, duplicate=len(rows) - inserted)


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
