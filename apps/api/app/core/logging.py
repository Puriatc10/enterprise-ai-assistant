"""Logging configuration.

Rules this module exists to enforce:

* one handler, stdout only (containers collect stdout; nothing is written to disk);
* a consistent line format in development, structured output in Phase 6;
* *never* log credentials, bearer tokens, cookies, full prompts, or raw connector
  payloads. Scrubbing is by allowlist at the call site, not by regex here.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

#: Third-party loggers that are noisy at INFO and can echo request data.
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3")


def configure_logging(level: str = "INFO") -> None:
    """Install the process-wide logging configuration, idempotently."""
    resolved = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved)

    for name in _NOISY_LOGGERS:
        # Request URLs and headers must not be captured at debug-by-accident.
        logging.getLogger(name).setLevel(max(resolved, logging.WARNING))
