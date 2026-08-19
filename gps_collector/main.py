# -*- coding: utf-8 -*-
"""GPS-1 -- one collection run: fetch new messages, store points, move on.

WHAT IT DOES, IN ORDER
  1. One request lists the whole fleet with the time of each object's last
     message. Objects silent longer than SILENT_DAYS are not asked about at
     all -- on 18.08.2026 that was 183 of 481 objects, and asking them nightly
     buys nothing.
  2. For every remaining object: load the interval from its watermark to
     "now minus LAG_MINUTES", store the points, and only then move the
     watermark.
  3. Points land in the monthly file of their own timestamp, so an interval
     that crosses the first of the month is split, not lost.

THE ORDER IN STEP 2 IS THE WHOLE DESIGN. Points are committed before the
watermark moves, so a crash anywhere in between costs a repeated interval --
which INSERT OR IGNORE swallows -- and never a hole. The opposite order would
lose messages silently: the day would simply come out with fewer hectares and
nothing would say why.

Run (PowerShell, one command per line):

  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" -m gps_collector.main *> gps_collect.log

[REASON]: the redirection is not decoration. A Windows console in selection
mode -- entered by a stray click in the window -- stops the process at its next
write, and that cost this track three days of a run everyone believed was
slow. A process writing to a file cannot be stopped that way.

Console output is ASCII.
"""

import argparse
import sys
import time
from datetime import datetime

from . import config, storage, sync_log
from .wialon import Client, WialonError, positions


class Summary:
    """What one run did, in numbers a report can be written from."""

    def __init__(self):
        self.units_total = 0
        self.units_silent = 0
        self.units_asked = 0
        self.units_empty = 0
        self.units_failed = 0
        self.units_uptodate = 0
        self.points_written = 0
        self.points_duplicate = 0
        self.points_no_position = 0
        self.points_seen = 0
        self.truncated = 0
        self.watermark_from = None
        self.watermark_to = None

    def line(self):
        return ("objects: %d | asked: %d | silent, skipped: %d | up to date: %d "
                "| no messages: %d | failed: %d | points written: %d of %d seen "
                "(duplicate %d, no position %d)"
                % (self.units_total, self.units_asked, self.units_silent,
                   self.units_uptodate, self.units_empty, self.units_failed,
                   self.points_written, self.points_seen,
                   self.points_duplicate, self.points_no_position))

    def balances(self):
        """Тождество журнала приёма, по образцу инварианта drone_sync_logs.

        seen = written + duplicate + no_position. Каждое сообщение попало ровно
        в одну корзину: легло точкой, отброшено ключом как уже известное, или
        не имело координат вовсе. Сходящееся тождество -- единственный способ
        заметить, что счётчики начали врать.
        """
        return self.points_seen == (self.points_written + self.points_duplicate
                                    + self.points_no_position)


def collect(client, folder, now=None, backfill_days=config.BACKFILL_DAYS,
            silent_days=config.SILENT_DAYS, lag_minutes=config.LAG_MINUTES,
            only=None, log=print):
    """Fetch and store everything new. Returns a Summary.

    `now` is an epoch second and exists so a test can place the run in time;
    it defaults to the clock.
    """
    now = time.time() if now is None else float(now)
    t_to = int(now - lag_minutes * 60)
    summary = Summary()

    units = client.list_units()
    summary.units_total = len(units)
    wanted = None if not only else {int(u) for u in only}

    for number, unit in enumerate(units, 1):
        unit_id = unit["id"]
        if wanted is not None and unit_id not in wanted:
            continue
        last_t = unit.get("last_t")
        if last_t is None or (now - float(last_t)) > silent_days * 86400:
            summary.units_silent += 1
            continue

        watermark = storage.read_watermark(folder, unit_id)
        t_from = (int(watermark) if watermark is not None
                  else t_to - int(backfill_days * 86400))
        if t_from >= t_to:
            # Nothing new can be there: the watermark already stands past the
            # point the lag lets us ask about.
            summary.units_uptodate += 1
            continue

        log("  [%d/%d] %s %s %s -> %s"
            % (number, len(units), datetime.now(config.TZ).strftime("%H:%M:%S"),
               config.ascii_only(unit["name"])[:32],
               datetime.fromtimestamp(t_from, config.TZ).strftime("%m-%d %H:%M"),
               datetime.fromtimestamp(t_to, config.TZ).strftime("%m-%d %H:%M")))

        messages = _load_with_backoff(client, unit_id, t_from, t_to, log)
        if messages is None:
            summary.units_failed += 1
            continue
        summary.units_asked += 1
        summary.points_seen += len(messages)

        rows = [(unit_id, t, lon, lat, speed, course, sats)
                for t, lon, lat, speed, course, sats in positions(messages)]
        # [REASON]: сообщение без координат -- это не потеря и не дубль, это
        # третья корзина. Без неё тождество журнала приёма не сходится, а
        # сходящееся тождество -- единственный способ заметить, что счётчики
        # начали врать.
        summary.points_no_position += len(messages) - len(rows)
        # Points first, committed, and only then the watermark.
        written = storage.write_points(folder, rows)
        summary.points_written += written.inserted
        summary.points_duplicate += written.duplicate
        summary.watermark_from = (t_from if summary.watermark_from is None
                                  else min(summary.watermark_from, t_from))
        summary.watermark_to = (t_to if summary.watermark_to is None
                                else max(summary.watermark_to, t_to))

        truncated = len(messages) >= config.MAX_MESSAGES_PER_INTERVAL
        if truncated:
            # [REASON]: the server returned exactly as many messages as it was
            # allowed to, so there may be more after the last one. Moving the
            # watermark to t_to here would step over them for ever. Stepping to
            # the last message received instead costs one more request next run
            # and loses nothing.
            summary.truncated += 1
            new_watermark = max(m["t"] for m in messages if m.get("t") is not None)
            log("    otvet obrezan po limitu (%d), vodyanoy znak na posledney "
                "tochke" % len(messages))
        else:
            new_watermark = t_to
        storage.set_watermark(folder, unit_id, new_watermark)
        if not messages:
            summary.units_empty += 1
    return summary


def _load_with_backoff(client, unit_id, t_from, t_to, log):
    """Messages, or None when the object could not be read this run.

    [REASON]: 1003/1004/1005 mean "wait", not "no data". Treating them as a
    failure would move nothing and lose nothing -- the watermark stays put --
    but it would also give up on an object for the whole night over a
    momentary limit. One pause and one retry costs 30 seconds.
    """
    for attempt in range(2):
        try:
            messages = client.load_interval(unit_id, t_from, t_to)
            client.unload()
            return messages
        except WialonError as problem:
            client.unload()
            if problem.code in config.LIMIT_ERRORS and attempt == 0:
                log("    server prosit podozhdat: %s -- pauza %d s"
                    % (problem, int(config.LIMIT_BACKOFF_S)))
                time.sleep(config.LIMIT_BACKOFF_S)
                continue
            log("    ne prochitan: %s" % problem)
            return None
        except Exception as problem:                               # noqa: BLE001
            client.unload()
            log("    ne prochitan: %s" % type(problem).__name__)
            return None
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=None,
                        help="where the point files live (default: instance/)")
    parser.add_argument("--pause", type=float, default=config.PAUSE_S,
                        help="seconds between requests (decision G9: 2.0)")
    parser.add_argument("--backfill-days", type=float,
                        default=config.BACKFILL_DAYS,
                        help="how far back a first run for an object goes")
    parser.add_argument("--silent-days", type=int, default=config.SILENT_DAYS,
                        help="skip objects silent longer than this")
    parser.add_argument("--lag-minutes", type=int, default=config.LAG_MINUTES,
                        help="stop this far short of now, for late deliveries")
    parser.add_argument("--only", action="append", default=[],
                        help="wialon id to collect; may repeat")
    parser.add_argument("--db", default=None,
                        help="path to transport.db for the ingestion journal")
    args = parser.parse_args(argv)

    folder = args.dir or config.points_dir()
    token = config.read_token()
    client = Client(pause=args.pause)
    try:
        client.login(token)
    except Exception as problem:                                   # noqa: BLE001
        sys.stderr.write(
            "\nERROR: ne udalos voyti na %s (%s).\n"
            "Proverte v PowerShell odnoy strokoy:\n"
            "  Test-NetConnection web.gpstrack.uz -Port 443\n"
            % (config.BASE_URL, type(problem).__name__))
        return 3
    print("login OK | tochki: %s" % folder)

    started_at = datetime.now(config.TZ)
    summary, failure = Summary(), None
    try:
        summary = collect(client, folder, backfill_days=args.backfill_days,
                          silent_days=args.silent_days,
                          lag_minutes=args.lag_minutes, only=args.only)
    except Exception as problem:                                   # noqa: BLE001
        # [REASON]: прогон, умерший на середине, обязан оставить о себе запись
        # -- иначе утром он неотличим от прогона, которого не было. Точки,
        # успевшие лечь, уже зафиксированы: watermark их не потеряет.
        failure = problem
    finally:
        client.logout()

    print("\n" + summary.line())
    print("requests: %d | logins: %d | abandoned: %d"
          % (client.requests, client.logins, client.abandoned))
    if summary.truncated:
        print("intervals truncated by the message limit: %d (they continue "
              "next run)" % summary.truncated)
    if not summary.balances():
        # [REASON]: тождество не сходится -- значит счётчики врут, и всё, что
        # по ним потом решат, будет решено по вранью. Говорим об этом громко.
        print("VNIMANIE: schetchiki ne shodyatsya: %d seen != %d + %d + %d"
              % (summary.points_seen, summary.points_written,
                 summary.points_duplicate, summary.points_no_position))
    sync_log.record(summary, started_at, datetime.now(config.TZ),
                    client=client, error=failure, db_path=args.db)
    if failure is not None:
        sys.stderr.write("\nERROR: progon prervan: %s\n"
                         % type(failure).__name__)
        return 1
    return 0 if summary.units_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
