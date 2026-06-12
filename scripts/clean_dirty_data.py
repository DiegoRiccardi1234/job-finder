"""One-shot maintenance: clean dirty data accumulated before the v1.4.x fixes.

Removes:
  * orphaned consecutive user chat messages with no assistant reply
    (left behind by chat turns that failed mid-flight before the handler fix);
  * empty chat sessions (no messages) other than ``default``.

Rewrites:
  * job ``riassunto`` fields that leaked a raw provider error
    (e.g. ``Error code: 401 - {'message': 'Wrong API Key'...}``) into a
    generic message, preserving the estimated score.

A timestamped backup of the SQLite file (plus its -wal/-shm sidecars) is taken
before any write. Run with the web app stopped to avoid lock contention.

Usage:
    python scripts/clean_dirty_data.py [--db data/searcher.db] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

GENERIC_SUMMARY = "Analisi euristica usata (IA non disponibile). Match stimato {score}/10."
DIRTY_MARKERS = ("Error code", "Wrong API", "invalid_request", "Traceback")
_SCORE_RE = re.compile(r"Match stimato\s+(\d+)\s*/\s*10")


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.stem}.cleanup_backup_{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup)
    for sidecar in (f"{db_path.name}-wal", f"{db_path.name}-shm"):
        src = db_path.with_name(sidecar)
        if src.exists():
            shutil.copy2(src, backup.with_name(backup.name + sidecar[len(db_path.name):]))
    return backup


def find_orphan_user_messages(cur: sqlite3.Cursor) -> list[int]:
    """User messages (content_type='message') with no later assistant reply."""
    orphans: list[int] = []
    for (session_id,) in cur.execute("SELECT DISTINCT session_id FROM chat_messages"):
        rows = cur.execute(
            "SELECT id, role, content_type FROM chat_messages "
            "WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        for idx, (mid, role, ctype) in enumerate(rows):
            if role != "user" or ctype != "message":
                continue
            # An answered user turn has an assistant 'message' somewhere after it.
            has_reply = any(
                r[1] == "assistant" and r[2] == "message" for r in rows[idx + 1:]
            )
            if not has_reply:
                orphans.append(mid)
    return orphans


def find_empty_sessions(cur: sqlite3.Cursor) -> list[str]:
    empty: list[str] = []
    for (sid,) in cur.execute("SELECT id FROM chat_sessions"):
        if sid == "default":
            continue
        n = cur.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id=?", (sid,)
        ).fetchone()[0]
        if n == 0:
            empty.append(sid)
    return empty


def find_dirty_jobs(cur: sqlite3.Cursor) -> list[tuple[int, str]]:
    """Return (job_id, new_analysis_json) for jobs whose riassunto leaked an error."""
    updates: list[tuple[int, str]] = []
    for jid, aj in cur.execute("SELECT id, analysis_json FROM jobs"):
        if not aj:
            continue
        try:
            data = json.loads(aj)
        except (json.JSONDecodeError, TypeError):
            continue
        riassunto = str(data.get("riassunto", ""))
        if not any(marker in riassunto for marker in DIRTY_MARKERS):
            continue
        m = _SCORE_RE.search(riassunto)
        score = m.group(1) if m else str(data.get("punteggio", 0))
        data["riassunto"] = GENERIC_SUMMARY.format(score=score)
        updates.append((jid, json.dumps(data, ensure_ascii=False)))
    return updates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/searcher.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    orphans = find_orphan_user_messages(cur)
    empty_sessions = find_empty_sessions(cur)
    dirty_jobs = find_dirty_jobs(cur)

    print(f"Orphan user messages : {len(orphans)} {orphans}")
    print(f"Empty sessions       : {len(empty_sessions)} {empty_sessions}")
    print(f"Dirty job summaries  : {len(dirty_jobs)} {[j for j, _ in dirty_jobs]}")

    if args.dry_run:
        print("\n[dry-run] no changes written.")
        conn.close()
        return

    if not (orphans or empty_sessions or dirty_jobs):
        print("\nNothing to clean.")
        conn.close()
        return

    backup = backup_db(db_path)
    print(f"\nBackup written: {backup}")

    if orphans:
        cur.executemany("DELETE FROM chat_messages WHERE id=?", [(i,) for i in orphans])
    for sid in empty_sessions:
        cur.execute("DELETE FROM chat_sessions WHERE id=?", (sid,))
    for jid, new_aj in dirty_jobs:
        cur.execute("UPDATE jobs SET analysis_json=? WHERE id=?", (new_aj, jid))

    conn.commit()
    try:
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError:
        pass
    conn.close()
    print("Cleanup complete.")


if __name__ == "__main__":
    main()
