"""Logging configuration: chatty third-party loggers stay quiet."""

from __future__ import annotations

import logging
from pathlib import Path

import app.log as logmod


def test_openai_logger_is_silenced(tmp_path: Path) -> None:
    """The ``openai`` SDK logger (and its ``_base_client`` child) must be at
    WARNING so request/retry chatter doesn't flood the app log."""
    prev_configured = logmod._CONFIGURED
    prev_level = logging.getLogger("openai").level
    try:
        logmod._CONFIGURED = False
        logmod.configure_logging(log_dir=tmp_path)
        assert logging.getLogger("openai").getEffectiveLevel() >= logging.WARNING
    finally:
        logmod._CONFIGURED = prev_configured
        logging.getLogger("openai").setLevel(prev_level)
