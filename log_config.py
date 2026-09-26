"""Shared logging setup for the webcam pipeline and API.

Import ``get_logger`` in each module to get a module-level logger, e.g.::

    from log_config import get_logger
    logger = get_logger(__name__)

The root logger is configured once, lazily, from the ``WEBCAM_LOG_LEVEL``
environment variable (default ``INFO``; an unknown value falls back to
``INFO`` rather than raising). Genuine CLI/progress output still uses
``print``; this is for errors that used to be swallowed.
"""
import logging
import os

_configured = False
_DEFAULT_LEVEL = "INFO"


def _resolve_level():
    name = (os.environ.get("WEBCAM_LOG_LEVEL") or _DEFAULT_LEVEL).strip().upper()
    level = getattr(logging, name, None)
    return level if isinstance(level, int) else logging.INFO


def _configure_once():
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=_resolve_level(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _configured = True


def get_logger(name=None):
    """Return a configured logger. Safe to call repeatedly."""
    _configure_once()
    return logging.getLogger(name)
