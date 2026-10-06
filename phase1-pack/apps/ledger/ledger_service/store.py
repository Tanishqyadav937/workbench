"""Append-only SQLite store for the ledger.

Why SQLite (a change from the Postgres plan in design.md 4.1):
  * the ledger lives in its own file/volume, separate from application data, so a bug or breach
    in the app database cannot touch it;
  * triggers make UPDATE and DELETE fail at the database level;
  * no extra server to run, and it is fully testable offline.
Production can swap this class for an INSERT-only Postgres role without changing the API.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .chain import (ACTOR_TYPES, EVENT_TYPES, GENESIS, canonical, compute_hash, load_entries,
                    row_to_entry, verify_entries)

MAX_PAYLOAD_BYTES = 8192  # payloads carry ids and hashes, not content; large payloads are refused

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq        INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id   TEXT NOT NULL,
    event      TEXT NOT NULL,
    payload    TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE is not allowed'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE is not allowed'); END;
"""


class LedgerError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(SCHEMA)

    def append(self, actor_type: str, actor_id: str, event: str,
               payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        if actor_type not in ACTOR_TYPES:
            raise LedgerError("actor.type must be one of %s" % (ACTOR_TYPES,))
        if not actor_id or not isinstance(actor_id, str):
            raise LedgerError("actor.id must be a non-empty string")
        if event not in EVENT_TYPES:
            raise LedgerError("unknown event type %r" % event)
        if not isinstance(payload, dict):
            raise LedgerError("payload must be an object")
        text = canonical(payload)
        if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise LedgerError("payload too large (max %d bytes): log hashes and ids, not content" % MAX_PAYLOAD_BYTES)

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                last = self._conn.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
                seq = (last["seq"] + 1) if last else 1
                prev_hash = last["hash"] if last else GENESIS
                ts = utc_now()  # server-side time: clients cannot backdate entries
                actor = {"type": actor_type, "id": actor_id}
                h = compute_hash(seq, ts, actor, event, payload, prev_hash)
                self._conn.execute(
                    "INSERT INTO ledger (seq, ts, actor_type, actor_id, event, payload, prev_hash, hash) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (seq, ts, actor_type, actor_id, event, text, prev_hash, h))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return {"seq": seq, "ts": ts, "actor": actor, "event": event,
                "payload": payload, "prev_hash": prev_hash, "hash": h}

    def head(self) -> Dict[str, Any]:
        row = self._conn.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return {"seq": row["seq"], "hash": row["hash"]} if row else {"seq": 0, "hash": GENESIS}

    def events(self, after_seq: int = 0, limit: int = 100, event: Optional[str] = None,
               actor_id: Optional[str] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        sql, args = "SELECT * FROM ledger WHERE seq > ?", [after_seq]
        if event:
            sql += " AND event = ?"
            args.append(event)
        if actor_id:
            sql += " AND actor_id = ?"
            args.append(actor_id)
        sql += " ORDER BY seq ASC LIMIT ?"
        args.append(limit)
        return [row_to_entry(r) for r in self._conn.execute(sql, args).fetchall()]

    def verify(self, expect_seq: Optional[int] = None, expect_hash: Optional[str] = None) -> Dict[str, Any]:
        return verify_entries(load_entries(self.path), expect_seq, expect_hash)

    def close(self) -> None:
        self._conn.close()
