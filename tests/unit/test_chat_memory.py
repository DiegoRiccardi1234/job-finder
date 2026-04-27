"""Unit tests for chat memory summarization."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.services.chat.memory import load_session_summary, maybe_summarize


class _StubProvider:
    def __init__(self, reply: str = "Summary: user wants Python roles, remote, min 35k."):
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, max_tokens=250, provider_name=None, model_name=None):
        self.calls.append(messages)
        return self.reply


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "mem.db")
    yield d
    d.close()


def _populate(db: Database, n: int, session_id: str = "s") -> None:
    for i in range(n):
        db.save_chat_message(session_id, "user" if i % 2 == 0 else "assistant", f"msg-{i}")


def test_no_summarize_below_threshold(db: Database) -> None:
    _populate(db, 10)
    provider = _StubProvider()
    assert maybe_summarize(db, "s", provider, threshold=20) is False
    assert provider.calls == []
    assert load_session_summary(db, "s") == ""


def test_summarize_compresses_and_deletes_old(db: Database) -> None:
    _populate(db, 25)
    provider = _StubProvider()
    assert maybe_summarize(db, "s", provider, threshold=20, keep_last=10) is True
    assert len(provider.calls) == 1

    # 25 regular messages → compress 15, keep 10 recent, plus 1 summary row.
    regular = db.list_chat_messages("s", limit=999, include_types=("message",))
    summaries = db.list_chat_messages("s", limit=999, include_types=("summary",))
    assert len(regular) == 10
    assert len(summaries) == 1
    assert summaries[0]["role"] == "system"
    assert "Summary" in summaries[0]["content"]


def test_provider_failure_does_not_break(db: Database) -> None:
    _populate(db, 25)

    class _BoomProvider:
        def chat(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert maybe_summarize(db, "s", _BoomProvider(), threshold=20) is False
    # Original messages still intact
    assert len(db.list_chat_messages("s", limit=999, include_types=("message",))) == 25


def test_load_session_summary_returns_latest(db: Database) -> None:
    db.save_chat_message("s", "system", "first summary", content_type="summary")
    db.save_chat_message("s", "system", "second summary", content_type="summary")
    assert load_session_summary(db, "s") == "second summary"


def test_list_chat_messages_filters_content_types(db: Database) -> None:
    _populate(db, 3)
    db.save_chat_message("s", "system", "a summary", content_type="summary")
    only_regular = db.list_chat_messages("s", limit=999, include_types=("message",))
    only_summary = db.list_chat_messages("s", limit=999, include_types=("summary",))
    assert len(only_regular) == 3
    assert len(only_summary) == 1
