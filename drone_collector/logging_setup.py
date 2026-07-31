# -*- coding: utf-8 -*-
"""drone_collector/logging_setup.py -- rotating file log plus stdout.

The collector runs unattended on a schedule, so the file log is the only
record of what happened; stdout is there for the operator who runs it by hand
and for whatever captures the scheduled process output.

What must never reach either handler: the ingest token, cookies, the contents
of storage_state.json, and request headers. This is not enforceable by the
logging layer -- it is enforced by never passing those values to it. The one
place that could leak by accident is the configuration dump, which is why
CollectorConfig.describe() reports the token as set/missing instead of by
value, and why nothing here formats a config object directly.
"""

import logging
import sys

from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# 5 MB per file, 10 backups -> at most ~55 MB on disk for the whole history.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 10


def setup_logging(log_dir, name='collector', level=logging.INFO):
    """Configure the root logger with a rotating file and a stdout handler.

    Safe to call twice: existing handlers are dropped first, so a second call
    does not double every line.
    """
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    file_handler = RotatingFileHandler(
        str(directory / ('%s.log' % name)),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8',
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # These two are noisy at INFO and say nothing the collector needs.
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    return logging.getLogger(name)


def format_run_summary(pairs):
    """One structured line from an ordered sequence of (key, value) pairs.

    [REASON]: one line per run, always the same keys in the same order, so a
    year of runs can be grepped and diffed without a parser. Values are
    rendered with str(); None becomes '-' so an absent value is visibly absent
    rather than silently empty.
    """
    parts = []
    for key, value in pairs:
        if value is None:
            rendered = '-'
        elif isinstance(value, bool):
            rendered = 'true' if value else 'false'
        else:
            rendered = str(value)
        if ' ' in rendered:
            rendered = '"%s"' % rendered
        parts.append('%s=%s' % (key, rendered))
    return 'RUN SUMMARY ' + ' '.join(parts)
