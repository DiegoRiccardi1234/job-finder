import logging
from pathlib import Path

from app.log import configure_logging, get_logger


def test_configure_logging_is_idempotent() -> None:
    configure_logging(level="DEBUG")
    root = logging.getLogger()
    handler_count = len(root.handlers)
    configure_logging(level="DEBUG")
    assert len(root.handlers) == handler_count


def test_file_handler_writes(tmp_path: Path) -> None:
    # Reset global state first
    from app import log as log_module

    log_module._CONFIGURED = False
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    configure_logging(log_dir=tmp_path / "logs", level="INFO")
    log = get_logger("tests.log")
    log.info("unit-test-marker")

    for h in logging.getLogger().handlers:
        h.flush()

    log_file = tmp_path / "logs" / "app.log"
    assert log_file.exists()
    assert "unit-test-marker" in log_file.read_text(encoding="utf-8")
