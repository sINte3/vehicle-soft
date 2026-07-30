"""
Incremental sync: pull the last N days of flight records and upsert them.
Reuses DjiClient.fetch_all_flights() — DJI UI loads everything for the
selected period at once, so "incremental" just means a shorter date filter.

Note: the filter on the date picker is set automatically by re-using the
existing /records page. The page itself remembers the last filter, so to
narrow the window we navigate to a URL with explicit query params, or
fall back to clicking the date picker via UI. For simplicity (and because
this works in practice with DJI), we ask the API directly via the same
captured-response approach but with a tighter timestamp range, set on the
page through localStorage / URL hash.

Actually the most robust approach: do exactly what backfill does (it
already pulls everything visible), but rely on the fact that the page
defaults to last 30 days when first opened in a fresh session. Since we
re-use storage_state, the date picker remembers the previously selected
range. To force a 7-day window we set it via URL parameters that the
SmartFarm page accepts.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from collector.api_client import DjiClient
from collector.backfill import UPSERT_SQL, init_db, normalize

log = logging.getLogger(__name__)

INCREMENTAL_DAYS = 7


def run_incremental(days: int = INCREMENTAL_DAYS) -> tuple[int, int]:
    """Run a sync covering the last `days` days. Returns (seen, written)."""
    conn = init_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sync_runs (kind, started_at, status) VALUES (?,?,?)",
        ("incremental", int(time.time()), "running"),
    )
    run_id = cur.lastrowid
    conn.commit()

    seen = written = 0
    try:
        with DjiClient() as client:
            # The site shows last ~30 days by default when freshly opened.
            # Our 7-day window is a subset; we just upsert everything we see
            # and the ON CONFLICT clause makes it idempotent.
            flights = client.fetch_all_flights()

            # Filter to the requested window in Python (post-fetch).
            # The DJI page may show more than 7 days, but ON CONFLICT
            # protects us from drift; we still want to limit memory pressure.
            cutoff = int(time.time()) - days * 86400
            flights = [f for f in flights if (f.get("start_timestamp") or 0) >= cutoff]
            seen = len(flights)

            batch = [normalize(r) for r in flights]
            if batch:
                cur.executemany(UPSERT_SQL, batch)
                conn.commit()
                written = len(batch)

        cur.execute(
            "UPDATE sync_runs SET finished_at=?, status=?, records_seen=?, records_written=? WHERE id=?",
            (int(time.time()), "ok", seen, written, run_id),
        )
        conn.commit()
        log.info("Incremental done. seen=%s written=%s window=%sd", seen, written, days)
        return seen, written
    except Exception as e:
        log.exception("Incremental failed")
        cur.execute(
            "UPDATE sync_runs SET finished_at=?, status=?, records_seen=?, records_written=?, error_text=? WHERE id=?",
            (int(time.time()), "error", seen, written, str(e)[:500], run_id),
        )
        conn.commit()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    from collector.logging_setup import setup_logging
    setup_logging("incremental")
    run_incremental()
