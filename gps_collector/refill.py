# -*- coding: utf-8 -*-
"""GPS-24 -- refill: give points already stored the ignition they lack.

WHY
The «time» method counts engine hours from the ignition sensor (owner's
decision of 09.10.2026: an engine hour is time with the ignition on). The
collector has stored ignition beside each point only since GPS-24; the points
of earlier weeks -- September, whose 853 applications wait for «time» -- lie
on disk without it. This run re-reads those weeks from Wialon and writes the
ignition value into the points that are already there.

WHAT IT DOES, IN ORDER
  1. One request lists the fleet with the parameter of each object's ignition
     sensor (the same request as the nightly run).
  2. For every object with an ignition sensor: the local point files say
     whether it has points in the period without ignition. None -- no request
     at all, so a repeated run asks only about what is still missing.
  3. Loads the messages over the span of those points, in pieces of at most
     MAX_MESSAGES_PER_INTERVAL; a truncated piece continues from its last
     message.
  4. Writes the ignition value into the points with the same (object,
     second). Nothing else.

WHAT IT DOES NOT DO
  * It inserts no point. A message the nightly run never stored stays
    unstored: inserting it would change days already computed (storage.
    fill_ignition says why).
  * It never replaces an ignition value already there, and never touches a
    coordinate.
  * It never moves a watermark: the nightly run goes on from where it was.
  * Nothing is written to Wialon or to transport.db.

Run (PowerShell, one command per line; not while the nightly collection of
01:00 is running -- two runs on one token wait for each other):

  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" -m gps_collector.refill --from 2026-09-01 --to 2026-10-19 *> gps_refill.log

`--to` is inclusive. Console output is ASCII.

EXIT CODES
  0  done, every object read;
  1  some objects could not be read (the run can simply be repeated);
  2  bad input: dates, an empty period, no point folder;
  3  login refused.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

from . import config, storage
from .main import _load_with_backoff
from .wialon import Client, ignition_value, login_failure

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_BAD_INPUT = 2
EXIT_LOGIN = 3


class RefillSummary:
    def __init__(self):
        self.units_total = 0
        self.units_no_ignition = 0
        self.units_complete = 0
        self.units_asked = 0
        self.units_failed = 0
        self.points_missing = 0
        self.messages_seen = 0
        self.with_value = 0
        self.filled = 0
        self.truncated = 0

    def line(self):
        return ("objects: %d | without an ignition sensor: %d | nothing to "
                "fill: %d | asked: %d | failed: %d" % (
                    self.units_total, self.units_no_ignition,
                    self.units_complete, self.units_asked, self.units_failed))

    def points_line(self):
        return ("points without ignition before: %d | messages read: %d, with "
                "an ignition value: %d | points filled: %d | pieces truncated "
                "by the limit: %d" % (
                    self.points_missing, self.messages_seen, self.with_value,
                    self.filled, self.truncated))


def local_midnight(day):
    return int(datetime.strptime(day, "%Y-%m-%d")
               .replace(tzinfo=config.TZ).timestamp())


def refill(client, folder, t_from, t_to, only=None, log=print):
    """Fill `ign` of the points in [t_from, t_to). Returns a RefillSummary."""
    summary = RefillSummary()
    units = client.list_units()
    summary.units_total = len(units)
    wanted = None if not only else {int(u) for u in only}
    for number, unit in enumerate(units, 1):
        unit_id = unit["id"]
        if wanted is not None and unit_id not in wanted:
            continue
        param = unit.get("ignition")
        if not param:
            summary.units_no_ignition += 1
            continue
        missing, first, last = storage.ignition_gaps(folder, unit_id,
                                                     t_from, t_to)
        if not missing:
            summary.units_complete += 1
            continue
        summary.points_missing += missing
        log("  [%d/%d] %s %s %d points %s -> %s"
            % (number, len(units), datetime.now(config.TZ).strftime("%H:%M:%S"),
               config.ascii_only(unit["name"])[:32], missing,
               datetime.fromtimestamp(first, config.TZ).strftime("%m-%d %H:%M"),
               datetime.fromtimestamp(last, config.TZ).strftime("%m-%d %H:%M")))
        # [REASON]: the span of the points still missing, not the whole
        # period: an object that reported a week of the six costs a week of
        # messages. Both ends are padded by a second, so the first and the
        # last such point are inside whether the server reads `timeFrom` and
        # `timeTo` inclusively or not; a message at a padding second matches
        # no point and fills nothing.
        piece_from, piece_to = first - 1, last + 1
        failed = False
        while piece_from < piece_to:
            messages = _load_with_backoff(client, unit_id, piece_from,
                                          piece_to, log)
            if messages is None:
                failed = True
                break
            summary.messages_seen += len(messages)
            rows = []
            for message in messages:
                stamp = message.get("t")
                value = ignition_value(message, param)
                if stamp is not None and value is not None:
                    rows.append((unit_id, int(stamp), value))
            summary.with_value += len(rows)
            summary.filled += storage.fill_ignition(folder, rows).filled
            if len(messages) < config.MAX_MESSAGES_PER_INTERVAL:
                break
            # [REASON]: the server stopped at its limit, so there is more
            # after the last message. The next piece starts AT that second,
            # not after it -- another message of the same second may be among
            # the ones cut off, and filling twice is harmless (`ign IS NULL`
            # guards it). A piece that did not advance would loop for ever;
            # one second forward is the guard.
            summary.truncated += 1
            reached = max(int(m["t"]) for m in messages if m.get("t") is not None)
            piece_from = max(reached, piece_from + 1)
        if failed:
            summary.units_failed += 1
        else:
            summary.units_asked += 1
    return summary


def months_of(t_from, t_to):
    return storage._months_between(t_from, t_to)


def coverage_lines(folder, months):
    out = []
    for month in months:
        total, with_ign = storage.ignition_coverage(folder, month)
        out.append("  %s: points %d, with ignition %d" % (month, total, with_ign))
    return out


def main(argv=None, client_factory=Client, now=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="day_from", required=True,
                        help="first local day, YYYY-MM-DD")
    parser.add_argument("--to", dest="day_to", required=True,
                        help="last local day, YYYY-MM-DD, inclusive")
    parser.add_argument("--dir", default=None,
                        help="where the point files live (default: instance/)")
    parser.add_argument("--pause", type=float, default=config.PAUSE_S,
                        help="seconds between requests (decision G9: 2.0)")
    parser.add_argument("--only", action="append", default=[],
                        help="wialon id to refill; may repeat")
    args = parser.parse_args(argv)

    try:
        t_from = local_midnight(args.day_from)
        t_to = local_midnight(args.day_to) + 86400
    except ValueError:
        sys.stderr.write("ERROR: dates are YYYY-MM-DD, e.g. --from 2026-09-01\n")
        return EXIT_BAD_INPUT
    now = time.time() if now is None else float(now)
    # [REASON]: the last LAG_MINUTES belong to the nightly run -- the same
    # slack it keeps for late deliveries.
    t_to = min(t_to, int(now - config.LAG_MINUTES * 60))
    if t_to <= t_from:
        sys.stderr.write("ERROR: the period is empty: --from must not be "
                         "after --to, nor in the future\n")
        return EXIT_BAD_INPUT
    folder = args.dir or config.points_dir()
    if not os.path.isdir(folder):
        sys.stderr.write("ERROR: no point folder %s\n"
                         % config.ascii_only(folder))
        return EXIT_BAD_INPUT
    months = months_of(t_from, t_to)
    print("period: %s -> %s | points: %s"
          % (datetime.fromtimestamp(t_from, config.TZ).strftime("%Y-%m-%d %H:%M"),
             datetime.fromtimestamp(t_to, config.TZ).strftime("%Y-%m-%d %H:%M"),
             config.ascii_only(folder)))
    print("before:")
    for line in coverage_lines(folder, months):
        print(line)

    token = config.read_token()
    client = client_factory(pause=args.pause)
    try:
        client.login(token)
    except Exception as problem:                                   # noqa: BLE001
        sys.stderr.write("\nERROR: ne udalos voyti na %s (%s).\n"
                         % (config.BASE_URL, login_failure(problem)))
        return EXIT_LOGIN
    try:
        summary = refill(client, folder, t_from, t_to, only=args.only)
    finally:
        client.logout()

    print("\n" + summary.line())
    print(summary.points_line())
    print("requests: %d | logins: %d | abandoned: %d"
          % (client.requests, client.logins, client.abandoned))
    print("after:")
    for line in coverage_lines(folder, months):
        print(line)
    print("REFILL ONLY: no point inserted, no watermark moved, nothing "
          "written to Wialon")
    return EXIT_OK if summary.units_failed == 0 else EXIT_PARTIAL


if __name__ == "__main__":
    sys.exit(main())
