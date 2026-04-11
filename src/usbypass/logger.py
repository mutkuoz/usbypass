"""Small logging helper that writes to syslog and stderr.

We avoid structured logging frameworks to keep the hot PAM path's import
cost low. The verify module should not import this by default.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from usbypass.config import LOG_TAG

_LOGGER: logging.Logger | None = None


def get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger(LOG_TAG)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(stderr)

    # Syslog is best-effort — may not exist in containers or tests.
    try:
        syslog = logging.handlers.SysLogHandler(address="/dev/log")
        syslog.setFormatter(
            logging.Formatter(f"{LOG_TAG}[%(process)d]: %(levelname)s %(message)s")
        )
        logger.addHandler(syslog)
    except OSError:
        pass

    _LOGGER = logger
    return logger
