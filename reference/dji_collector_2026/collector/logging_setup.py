"""
Centralised logging setup. Used by scheduler.py, incremental.py and any
script that runs unattended (under NSSM, no console).

Logs go to:
  - logs/<name>.log    (rotating, 5 MB per file, keep 10 backups)
  - stderr             (so NSSM captures something even if file write fails)
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging(name: str = "collector", level: int = logging.INFO) -> None:
    """Configure root logger with rotating file + stderr handlers."""
    root = logging.getLogger()
    root.setLevel(level)

    # Clear any pre-existing handlers (e.g. when called twice)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 5 MB per file, keep 10 backups → ~50 MB max per log
    fh = RotatingFileHandler(
        LOG_DIR / f"{name}.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
