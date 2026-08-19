# -*- coding: utf-8 -*-
"""Constants and paths of the collector. Standard library only.

Everything here is either a decision recorded in
docs/GPS_PLAN_FAKT_VISION_ROADMAP.md section 5.1, or a number measured on the
live server and quoted with its measurement.
"""

import os
import sys
from datetime import timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_URL = os.environ.get("WIALON_BASE_URL", "https://web.gpstrack.uz")
AJAX = BASE_URL.rstrip("/") + "/wialon/ajax.html"

# [REASON]: the whole project counts days in local time (UTC+5). A day that
# starts at midnight UTC would cut every evening shift in half and put its
# hectares on the previous date.
TZ = timezone(timedelta(hours=5))

# [REASON]: 1 is the name, 1024 is the last known position with its timestamp.
# Summed, as Wialon flags always are. This is the request tools/wialon_last_seen.py
# already uses -- one call answers "when did each object last report" for the
# whole fleet, and no messages are loaded at all.
FLAG_NAME_AND_LAST_POSITION = 1025

# [REASON]: measured 18.08.2026 -- 298 objects of 481 had reported within three
# days, 70 had been silent longer than a month, 30 longer than half a year and
# one had never reported at all. Asking the server nightly about a tracker that
# has been dead since spring costs a request and buys nothing. Thirty days is
# far longer than any plausible idle spell of a working machine in season.
SILENT_DAYS = 30

# [REASON]: a tracker delivers with a delay -- it buffers while out of coverage
# and sends the backlog on reconnect. Asking up to "now" would mark that
# interval collected before its messages existed, and the watermark would step
# over them for ever. Half an hour of slack costs one extra run's worth of
# lateness and nothing else.
LAG_MINUTES = 30

# [REASON]: a first run for an object with no watermark has to start somewhere.
# One day matches the nightly cadence; a longer backfill is an argument
# (--backfill-days), not a default, because it multiplies the first night's
# load by the number of days.
BACKFILL_DAYS = 1

# [REASON]: decision G9. Access from the cluster's address was lost on
# 14.08.2026 during an unpaced run of ~2000 requests in 40 minutes and the
# cause is still unproven (question V-22). Until the integrator answers, every
# tool of this track holds one request per two seconds.
PAUSE_S = 2.0

# [REASON]: measured, not estimated. tools/wialon_measure_cost.py timed
# messages/load_interval on an object with 2718 messages a day: 0.4 s. The
# nightly run is therefore bounded by this pause, not by the server -- 298
# objects x 2 requests x 2 s = about 20 minutes.
REQUESTS_PER_UNIT = 2                       # load_interval + unload

RETRIES = 3
RETRY_PAUSE_S = (3, 8, 20)
TIMEOUT = 60
# [REASON]: urlopen's timeout is per socket operation, not per request, so a
# peer that dribbles bytes holds a read open for ever -- the fleet run of
# 16.08 stood at one object for a day without printing a line. This is the
# ceiling on one whole request; a stuck one is abandoned and the run goes on.
HARD_DEADLINE_S = 150.0

# [REASON]: a tractor writes 1500-13000 messages a day. 50 000 is well above
# that and far below the 2 GB response limit, so a normal interval is never
# truncated -- and when a backfill does hit the cap, the watermark stops at the
# last message received instead of jumping past the rest (see main.py).
MAX_MESSAGES_PER_INTERVAL = 50000

# [REASON]: ресурс 285 -- «Бухоро агрокластер», 17 812 геозон; у дилера 170 их
# ноль. Контуры наши, зависимости от дилера в них нет. Флаг списка 0x1001
# подтверждён зондом 2, флаг данных берётся полным: имя, тип и точки нужны все.
ZONE_RESOURCE_ID = 285
ZONE_LIST_FLAGS = 0x1001
ZONE_DATA_FLAGS = 0xFFFFFFFF

# Тип геозоны в Wialon: 1 -- линия, 2 -- полигон, 3 -- окружность.
ZONE_TYPE_POLYGON = 2

LIMIT_ERRORS = (1003, 1004, 1005)
LIMIT_BACKOFF_S = 30.0

# [REASON]: documented ceiling is 15 mln messages per user per two minutes;
# half of it is used here, so a second tool running against the same account
# at the same time still cannot push the pair of us over.
MESSAGE_WINDOW_S = 120.0
MESSAGE_WINDOW_LIMIT = 7000000

ERRORS = {1: "invalid session", 2: "invalid service", 4: "invalid input",
          5: "request failed", 7: "access denied",
          1001: "no messages for the interval",
          1003: "only one request is allowed at the moment",
          1004: "the limit of messages has been exceeded",
          1005: "the execution time has exceeded the limit",
          1011: "your IP has changed, or the session has expired"}

# [REASON]: the charter says console output is ASCII, and this is the run that
# proved why: on 18.08 the fleet probe died on "Niva 80 350 SBA (Hokimiyat)"
# because U+04B2 has no place in cp1251. Half this fleet is named in Uzbek
# Cyrillic. Files keep the real name; the console gets a transliteration.
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "ў": "o", "қ": "q",
    "ғ": "g", "ҳ": "h", "і": "i", "ї": "yi", "є": "e",
}


def points_dir():
    """Where the monthly point files and the watermark file live.

    [REASON]: decision G3 -- next to instance/transport.db but never inside it.
    A million points a day is 30 million a month; retention is then a file
    delete, not a DELETE and a VACUUM on the database the application serves
    from. GPS_POINTS_DIR exists so the tests can point this at a temp folder
    without touching the real instance directory.
    """
    return os.environ.get("GPS_POINTS_DIR") or os.path.join(ROOT, "instance")


def ascii_only(text):
    """Anything printable, whatever the console code page is."""
    out = []
    for char in str(text):
        if char.isascii():
            out.append(char)
            continue
        replacement = TRANSLIT.get(char.lower())
        if replacement is None:
            out.append("?")
        elif char.isupper():
            out.append(replacement.upper() if len(replacement) == 1
                       else replacement.capitalize())
        else:
            out.append(replacement)
    return "".join(out)


def read_token():
    """The Wialon token, from a file or the environment. Never printed.

    Looked for in the working directory first, then next to the repository
    root, then in WIALON_TOKEN.
    """
    for folder in (os.getcwd(), ROOT):
        path = os.path.join(folder, "wialon_token.txt")
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as fh:
                value = fh.readline().strip()
            if value:
                return value
    value = os.environ.get("WIALON_TOKEN", "").strip()
    if value:
        return value
    sys.stderr.write("ERROR: wialon_token.txt not found and WIALON_TOKEN "
                     "is not set\n")
    sys.exit(2)
