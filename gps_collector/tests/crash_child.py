# -*- coding: utf-8 -*-
"""One collection run in a child process, killable at a chosen moment.

Started by test_collector.py; not a test by itself. The point of a separate
process is that os._exit(9) skips every buffer and every `finally` Python
would otherwise run on the way out -- the way a machine losing power does, and
the way a `try/except` inside the test never could.

  python -m gps_collector.tests.crash_child <folder> <mode>

  normal            collect and finish
  crash             die between loading the messages and writing the points
  wrong-order-crash write the watermark FIRST, then die before writing the
                    points -- the defective implementation this design exists
                    to rule out. It is here so the test can show it fails the
                    check that the correct order passes; a check that cannot
                    tell the two apart is not a check.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_collector import storage                                  # noqa: E402
from gps_collector.main import collect                             # noqa: E402
from gps_collector.tests.support import (FakeServer, Installed,    # noqa: E402
                                         epoch)
from gps_collector.wialon import Client                            # noqa: E402

NOW = epoch("2026-08-10 07:00")
UNIT = 101


def main():
    folder, mode = sys.argv[1], sys.argv[2]
    server = FakeServer(fleet=[{"id": UNIT, "last_t": NOW - 3600}])

    if mode == "crash":
        def die(*args, **kwargs):
            os._exit(9)
        storage.write_points = die
    elif mode == "wrong-order-crash":
        real_write = storage.write_points

        def watermark_first(folder_, rows):
            if rows:
                storage.set_watermark(folder_, rows[0][0], max(r[1] for r in rows))
            os._exit(9)
            return real_write(folder_, rows)                   # pragma: no cover
        storage.write_points = watermark_first

    with Installed(server):
        client = Client(pause=0.0, log=lambda *a: None)
        client.login("test-token-not-a-real-one")
        collect(client, folder, now=NOW, backfill_days=1,
                log=lambda *a: None)
        client.logout()
    print("finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
