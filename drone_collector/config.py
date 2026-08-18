# -*- coding: utf-8 -*-
"""drone_collector/config.py -- environment configuration, validated once.

Everything the collector needs comes from environment variables, optionally
seeded from drone_collector/.env. Nothing is read from the application's
config.py: the two processes share only the token value, and they share it
through the environment of the machine, not through an import.

Failure mode is deliberate: a missing or unusable variable raises ConfigError
naming the variable, main() turns that into exit code 1, and no request is
ever made. An ingest run that starts with a missing DRONE_API_TOKEN would
otherwise send a batch that the endpoint answers 401 -- a needless round trip
against production for a mistake that is visible before the first byte.

Token values are never logged. CollectorConfig.describe() exists precisely so
that a run can log its own configuration without leaking the token.
"""

import os

from pathlib import Path

# Relative paths (storage state, logs, dry-run dumps) resolve against the
# package directory, so the collector behaves the same whatever the current
# working directory of the scheduler happens to be.
PACKAGE_ROOT = Path(__file__).resolve().parent

DOTENV_PATH = PACKAGE_ROOT / '.env'

# [REASON]: python-dotenv is a real dependency of the collector
# (drone_collector/requirements.txt) but must not be a dependency of *importing*
# this module: the unit tests and the repository CI run in the application's
# Python, where it is not installed. Without it the process still reads the
# real environment -- only the .env file is skipped, and load_dotenv_file()
# reports that it was.
try:
    from dotenv import load_dotenv as _load_dotenv
    HAVE_DOTENV = True
except ImportError:  # pragma: no cover -- depends on the virtualenv, not on logic
    HAVE_DOTENV = False

    def _load_dotenv(*_args, **_kwargs):
        return False


# Endpoint path appended to VEHICLE_SOFT_BASE_URL. Fixed by drones.py:
# blueprint prefix /drones + route /api/flight_sync.
FLIGHT_SYNC_PATH = '/drones/api/flight_sync'

# Endpoint path for the field-contour snapshot (DRONE-LANDS-001). Fixed by
# drones.py: blueprint prefix /drones + route /api/land_sync.
LAND_SYNC_PATH = '/drones/api/land_sync'

# [REASON]: drones.py answers 413 above 1000 flights per request
# (DRONE_SYNC_MAX_BATCH). The operator may set DRONE_BATCH_SIZE to anything;
# the clamp here is what keeps a mistyped 5000 from turning into a rejected
# run instead of a slower one.
MAX_BATCH_SIZE = 1000

DEFAULT_RECORDS_URL = 'https://www.djiag.com/records/list'
# Field Management. Read by --lands only; the flight walk never opens it.
DEFAULT_FIELDS_URL = 'https://www.djiag.com/mission'
DEFAULT_STORAGE_STATE = 'data/storage_state.json'
DEFAULT_WINDOW_DAYS = 30
DEFAULT_TZ_OFFSET_HOURS = 5
DEFAULT_PAGE_TIMEOUT_MS = 45000
DEFAULT_SETTLE_MS = 2500
# [REASON]: 500 was below reality. The live cabinet reports 705 pages for
# 2026-01-01..2026-07-31 at 30 per page, and a full historical window is around
# 970 -- the old default terminated a legitimate backfill as if it had run
# away. 2000 still catches a genuine runaway.
DEFAULT_MAX_PAGES = 2000

# [REASON]: the ingest endpoint creates one drone_sync_logs row PER REQUEST.
# A backfill of ~29 000 flights at 500 per request leaves about 60 rows, and
# "did the 2025 collection succeed" becomes a manual summing exercise. 1000
# halves that. It is also the endpoint's hard limit -- above it, 413 -- so the
# clamp stays.
DEFAULT_BATCH_SIZE = 1000

# [REASON]: the directory is served twenty rows at a time and holds 5 489
# contours -- 275 pages. A thousand is far above any real directory and still
# stops a runaway; it is separate from DJI_MAX_PAGES because the two walks
# have nothing in common but the word "page".
DEFAULT_MAX_LAND_PAGES = 1000


class ConfigError(Exception):
    """Unusable environment. Always names the offending variable."""


def load_dotenv_file(path=None):
    """Seed os.environ from drone_collector/.env when python-dotenv is present.

    Returns True when a file was actually loaded. Existing environment
    variables win over the file (dotenv's default), so a scheduler that
    exports the token wins over a stale .env.
    """
    if not HAVE_DOTENV:
        return False
    target = Path(path) if path else DOTENV_PATH
    if not target.exists():
        return False
    return bool(_load_dotenv(str(target)))


def _raw(name):
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def _as_int(name, default, minimum=None):
    raw = _raw(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            raise ConfigError('%s must be a whole number, got %r' % (name, raw))
    if minimum is not None and value < minimum:
        raise ConfigError('%s must be at least %d, got %d' % (name, minimum, value))
    return value


def _as_float(name, default):
    raw = _raw(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError('%s must be a number, got %r' % (name, raw))


def _as_bool(name, default):
    raw = _raw(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in ('1', 'true', 'yes', 'on'):
        return True
    if lowered in ('0', 'false', 'no', 'off'):
        return False
    raise ConfigError('%s must be true or false, got %r' % (name, raw))


def resolve_path(value):
    """Absolute paths as given; relative ones against the package directory."""
    path = Path(value)
    return path if path.is_absolute() else (PACKAGE_ROOT / path)


class CollectorConfig(object):
    """Validated configuration of one collector run."""

    def __init__(self, records_url, storage_state, headless, window_days,
                 tz_offset_hours, page_timeout_ms, settle_ms, max_pages,
                 base_url, api_token, batch_size, expected_region=None,
                 allow_empty_window=False, fields_url=DEFAULT_FIELDS_URL,
                 max_land_pages=DEFAULT_MAX_LAND_PAGES):
        self.records_url = records_url
        self.fields_url = fields_url
        self.max_land_pages = max_land_pages
        self.storage_state = storage_state
        self.headless = headless
        self.window_days = window_days
        self.tz_offset_hours = tz_offset_hours
        self.page_timeout_ms = page_timeout_ms
        self.settle_ms = settle_ms
        self.max_pages = max_pages
        self.base_url = base_url
        self.api_token = api_token
        self.batch_size = batch_size
        self.expected_region = expected_region
        self.allow_empty_window = allow_empty_window

    @property
    def flight_sync_url(self):
        if not self.base_url:
            return None
        return self.base_url.rstrip('/') + FLIGHT_SYNC_PATH

    @property
    def land_sync_url(self):
        if not self.base_url:
            return None
        return self.base_url.rstrip('/') + LAND_SYNC_PATH

    @property
    def log_dir(self):
        return PACKAGE_ROOT / 'logs'

    @property
    def out_dir(self):
        return PACKAGE_ROOT / 'out'

    def describe(self):
        """Loggable view of the configuration.

        [REASON]: the token is reported as set/missing and never by value.
        This method is the only sanctioned way to log the configuration; the
        object itself must not be formatted into a log line.
        """
        return {
            'records_url': self.records_url,
            'fields_url': self.fields_url,
            'storage_state': str(self.storage_state),
            'headless': self.headless,
            'window_days': self.window_days,
            'tz_offset_hours': self.tz_offset_hours,
            'page_timeout_ms': self.page_timeout_ms,
            'settle_ms': self.settle_ms,
            'max_pages': self.max_pages,
            'flight_sync_url': self.flight_sync_url,
            'land_sync_url': self.land_sync_url,
            'max_land_pages': self.max_land_pages,
            'api_token': 'set' if self.api_token else 'missing',
            'batch_size': self.batch_size,
            'expected_region': self.expected_region or 'not set',
            'allow_empty_window': self.allow_empty_window,
        }


def load_config(require_ingest=True, load_dotenv=True):
    """Read and validate the environment.

    require_ingest=False is used by --save-session and --dry-run: neither
    sends anything, so demanding VEHICLE_SOFT_BASE_URL and DRONE_API_TOKEN
    would make a purely local operation fail for a reason that does not apply
    to it. Every code path that does send passes require_ingest=True, so a
    missing token still stops the run before the first request.
    """
    if load_dotenv:
        load_dotenv_file()

    records_url = _raw('DJI_RECORDS_URL') or DEFAULT_RECORDS_URL
    if not records_url.startswith(('http://', 'https://')):
        raise ConfigError('DJI_RECORDS_URL must start with http:// or https://,'
                          ' got %r' % records_url)

    fields_url = _raw('DJI_FIELDS_URL') or DEFAULT_FIELDS_URL
    if not fields_url.startswith(('http://', 'https://')):
        raise ConfigError('DJI_FIELDS_URL must start with http:// or https://,'
                          ' got %r' % fields_url)

    storage_state = resolve_path(_raw('DJI_STORAGE_STATE') or DEFAULT_STORAGE_STATE)

    base_url = _raw('VEHICLE_SOFT_BASE_URL')
    api_token = _raw('DRONE_API_TOKEN')
    if require_ingest:
        if not base_url:
            raise ConfigError('VEHICLE_SOFT_BASE_URL is not set -- the collector'
                              ' has nowhere to send the flights')
        if not base_url.startswith(('http://', 'https://')):
            raise ConfigError('VEHICLE_SOFT_BASE_URL must start with http:// or'
                              ' https://, got %r' % base_url)
        if not api_token:
            raise ConfigError('DRONE_API_TOKEN is not set -- the ingest endpoint'
                              ' denies by default and would answer 401')

    batch_size = _as_int('DRONE_BATCH_SIZE', DEFAULT_BATCH_SIZE, minimum=1)
    if batch_size > MAX_BATCH_SIZE:
        batch_size = MAX_BATCH_SIZE

    return CollectorConfig(
        records_url=records_url,
        storage_state=storage_state,
        headless=_as_bool('DJI_HEADLESS', False),
        window_days=_as_int('DJI_WINDOW_DAYS', DEFAULT_WINDOW_DAYS, minimum=1),
        tz_offset_hours=_as_float('DJI_TZ_OFFSET_HOURS', DEFAULT_TZ_OFFSET_HOURS),
        page_timeout_ms=_as_int('DJI_PAGE_TIMEOUT_MS', DEFAULT_PAGE_TIMEOUT_MS,
                                minimum=1000),
        settle_ms=_as_int('DJI_SETTLE_MS', DEFAULT_SETTLE_MS, minimum=0),
        max_pages=_as_int('DJI_MAX_PAGES', DEFAULT_MAX_PAGES, minimum=1),
        base_url=base_url,
        api_token=api_token,
        batch_size=batch_size,
        expected_region=_raw('DJI_EXPECTED_REGION'),
        allow_empty_window=_as_bool('DJI_ALLOW_EMPTY_WINDOW', False),
        fields_url=fields_url,
        max_land_pages=_as_int('DJI_MAX_LAND_PAGES', DEFAULT_MAX_LAND_PAGES,
                               minimum=1),
    )
