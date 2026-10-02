from pathlib import Path

from app import notify as notifications
from app.db import Database
from app.services import reminder_watch


def test_reminder_retries_when_no_notifier_or_delivery_fails(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "reminders.db")
    try:
        jid, _, _ = db.upsert_job({"titolo": "Automation", "azienda": "Acme", "link": "url"})
        db.set_job_reminder(jid, "2020-01-01", "follow up")
        db.set_preference(reminder_watch.PREF_ENABLED, "1")
        monkeypatch.setattr(notifications, "_notifier", None)
        assert reminder_watch.check_due(db) == 0
        assert db.get_preference(reminder_watch.PREF_ANNOUNCED, "[]") == "[]"

        def broken(*_):
            raise RuntimeError("tray unavailable")

        monkeypatch.setattr(notifications, "_notifier", broken)
        assert reminder_watch.check_due(db) == 0
        received = []
        monkeypatch.setattr(notifications, "_notifier", lambda *args: received.append(args))
        assert reminder_watch.check_due(db) == 1
        assert len(received) == 1
        assert reminder_watch.check_due(db) == 0
    finally:
        db.close()
