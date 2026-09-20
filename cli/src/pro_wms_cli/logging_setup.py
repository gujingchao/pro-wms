"""Central logging configuration for pro-wms.

Everything diagnostic goes to stderr via the standard library; the CLI keeps
printing its JSON payload to stdout because that is the contract callers rely on.

Levels (see docs/style.md):
    DEBUG    low-level tracing
    INFO     state transitions (seed, status moves)
    WARNING  recoverable domain failures (version conflict, short stock)
    ERROR    an operation failed
"""

from __future__ import annotations

import logging
import os
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"

_CONFIGURED = False


def log_level() -> int:
    """Read the desired level from PRO_WMS_LOG (default INFO)."""
    raw = os.environ.get("PRO_WMS_LOG", "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def configure(level: int | None = None) -> None:
    """Configure the root `pro_wms` logger once.

    Safe to call repeatedly: later calls only adjust the level.
    """
    global _CONFIGURED
    logger = logging.getLogger("pro_wms")
    logger.setLevel(level if level is not None else log_level())
    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(handler)
        # Keep our records out of the root logger so importing apps stay in control.
        logger.propagate = False
        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger, configuring the root on first use."""
    configure()
    return logging.getLogger(f"pro_wms.{name}")
