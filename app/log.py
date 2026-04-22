"""Centralized logging setup for the app package.

Use ``get_logger(__name__)`` in any module instead of ``print`` or bare
``except`` swallows. Call ``configure_logging`` once at startup
(``run_webapp.py`` / ``app.main``).
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    log_dir: Path | None = None,
    level: str | int | None = None,
) -> None:
    """Configure root logger. Idempotent: safe to call multiple times."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    resolved_level = _resolve_level(level)

    root = logging.getLogger()
    root.setLevel(resolved_level)
    # Remove default handlers to avoid duplicate output under uvicorn reload.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(resolved_level)
    root.addHandler(stream_handler)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(resolved_level)
        root.addHandler(file_handler)

    # Silence overly chatty third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _CONFIGURED = True


def _resolve_level(level: str | int | None) -> int:
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    if isinstance(level, int):
        return level
    mapping = logging.getLevelNamesMapping()
    return mapping.get(str(level).upper(), logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger. Call ``configure_logging`` once first."""
    return logging.getLogger(name)
