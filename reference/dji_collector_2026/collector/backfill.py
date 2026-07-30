"""
One-shot backfill: pull every flight from BACKFILL_START_MS to now
into data/app.db.

Usage:
    python -m collector.backfill

Idempotent: re-running just upserts the same rows by DJI id.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv

from collector.api_client import DjiClient

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "app.db"
SCHEMA = ROOT / "db" / "schema.sql"

log = logging.getLogger(__name__)


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    return conn


DRONE_NICKNAME_ALIASES = {
    "4 Ғиждувон": "4 Gijduvon",
}
EXCLUDED_NICKNAMES = {"9 Garden"}


def normalize(rec: dict) -> tuple:
    """Convert a raw DJI record into a DB row tuple matching schema.sql order."""
    area_raw = rec.get("new_work_area") or 0           # m²
    spray_raw = rec.get("spray_usage") or 0            # ml
    nickname = rec.get("nickname")
    nickname = DRONE_NICKNAME_ALIASES.get(nickname, nickname)
    excluded = 1 if nickname in EXCLUDED_NICKNAMES else 0
    return (
        int(rec["id"]),
        rec.get("serial_number"),
        nickname,
        rec.get("flyer_name"),
        rec.get("team_name"),
        int(rec.get("start_timestamp") or 0),
        int(rec.get("end_timestamp") or 0),
        int(rec.get("work_time_seconds") or 0),
        round(area_raw / 10000.0, 4),                  # ha
        round(spray_raw / 1000.0, 3),                  # liters
        int(rec.get("sow_usage") or 0),
        rec.get("work_speed"),
        rec.get("spray_width"),
        rec.get("radar_height"),
        1 if rec.get("manual_mode") else 0,
        rec.get("mode_name"),
        rec.get("usage_type"),
        rec.get("lng"),
        rec.get("lat"),
        rec.get("location"),
        rec.get("created_at"),
        rec.get("updated_at"),
        json.dumps(rec, ensure_ascii=False),
        int(time.time()),
        excluded,
    )


UPSERT_SQL = """
INSERT INTO flights (
    id, drone_sn, drone_nickname, flyer_name, team_name,
    start_ts, end_ts, work_seconds, area_ha, spray_liters,
    sow_usage_raw, work_speed, spray_width, radar_height,
    manual_mode, mode_name, usage_type, lng, lat, location_text,
    created_at, updated_at, raw_json, synced_at, excluded
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(id) DO UPDATE SET
    drone_nickname=excluded.drone_nickname,
    flyer_name=excluded.flyer_name,
    area_ha=excluded.area_ha,
    spray_liters=excluded.spray_liters,
    end_ts=excluded.end_ts,
    work_seconds=excluded.work_seconds,
    updated_at=excluded.updated_at,
    raw_json=excluded.raw_json,
    synced_at=excluded.synced_at,
    excluded=MAX(flights.excluded, excluded.excluded)
"""


def run_backfill():
    conn = init_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sync_runs (kind, started_at, status) VALUES (?,?,?)",
        ("backfill", int(time.time()), "running"),
    )
    run_id = cur.lastrowid
    conn.commit()

    seen = written = 0
    try:
        with DjiClient() as client:
            flights = client.fetch_all_flights()
            seen = len(flights)
            batch = [normalize(r) for r in flights]
            if batch:
                cur.executemany(UPSERT_SQL, batch)
                conn.commit()
                written = len(batch)
                log.info("Wrote %s rows", written)
        cur.execute(
            "UPDATE sync_runs SET finished_at=?, status=?, records_seen=?, records_written=? WHERE id=?",
            (int(time.time()), "ok", seen, written, run_id),
        )
        conn.commit()
        log.info("Backfill done. seen=%s written=%s", seen, written)
    except Exception as e:
        log.exception("Backfill failed")
        cur.execute(
            "UPDATE sync_runs SET finished_at=?, status=?, records_seen=?, records_written=?, error_text=? WHERE id=?",
            (int(time.time()), "error", seen, written, str(e)[:500], run_id),
        )
        conn.commit()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_backfill()
