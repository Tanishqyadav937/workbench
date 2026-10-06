"""Hash-chain primitives and verification. Standard library only (Python 3.9+).

Single source of truth for hashing: the service, the CLI verifier and the tests all use this module,
so they can never disagree about what a valid chain is.
"""
import hashlib
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

GENESIS = "sha256:GENESIS"

# Must match schemas/ledger_entry.schema.json (a test enforces this).
EVENT_TYPES = (
    "auth.login", "model.call", "router.decision", "tool.call", "file.access",
    "retrieval.query", "checkpoint.decision", "consent.change", "erasure",
    "deliverable.export", "admin.model_reload", "boot.weights_verified",
)
ACTOR_TYPES = ("user", "agent", "system")


def canonical(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(seq: int, ts: str, actor: Dict[str, str], event: str,
                 payload: Dict[str, Any], prev_hash: str) -> str:
    body = {"seq": seq, "ts": ts, "actor": actor, "event": event,
            "payload": payload, "prev_hash": prev_hash}
    return "sha256:" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def hash_obj(obj: Any) -> str:
    """Hash any JSON-able object (used to log content by hash instead of storing it)."""
    return "sha256:" + hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def row_to_entry(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "seq": row["seq"],
        "ts": row["ts"],
        "actor": {"type": row["actor_type"], "id": row["actor_id"]},
        "event": row["event"],
        "payload": json.loads(row["payload"]),
        "prev_hash": row["prev_hash"],
        "hash": row["hash"],
    }


def load_entries(db_path: str) -> List[Dict[str, Any]]:
    """Read every entry in order, using a READ-ONLY connection."""
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM ledger ORDER BY seq ASC").fetchall()
        return [row_to_entry(r) for r in rows]
    finally:
        conn.close()


def verify_entries(entries: Iterable[Dict[str, Any]],
                   expect_seq: Optional[int] = None,
                   expect_hash: Optional[str] = None) -> Dict[str, Any]:
    """Recompute the whole chain. Reports the FIRST problem found.

    expect_seq / expect_hash: a previously saved 'anchor' (the head at some earlier time).
    Without an anchor, deleting the newest entries cannot be detected, because nothing later
    refers to them. With an anchor it can.
    """
    prev_hash = GENESIS
    prev_seq = 0
    checked = 0
    for e in entries:
        problem = None
        if e["seq"] != prev_seq + 1:
            problem = "sequence gap or reorder (expected %d, found %d)" % (prev_seq + 1, e["seq"])
        elif e["prev_hash"] != prev_hash:
            problem = "prev_hash does not match the previous entry (chain broken)"
        else:
            recomputed = compute_hash(e["seq"], e["ts"], e["actor"], e["event"], e["payload"], e["prev_hash"])
            if recomputed != e["hash"]:
                problem = "hash mismatch (this entry was modified)"
        if problem:
            return {"ok": False, "checked": checked, "head": {"seq": prev_seq, "hash": prev_hash},
                    "first_break": {"seq": e["seq"], "reason": problem}}
        prev_hash, prev_seq = e["hash"], e["seq"]
        checked += 1

    result = {"ok": True, "checked": checked, "head": {"seq": prev_seq, "hash": prev_hash}, "first_break": None}

    if expect_seq is not None:
        if prev_seq < expect_seq:
            result.update(ok=False, first_break={
                "seq": prev_seq + 1,
                "reason": "chain truncated: anchor says seq %d existed, chain ends at %d" % (expect_seq, prev_seq)})
        elif expect_hash is not None and prev_seq >= expect_seq:
            anchored = next((x for x in entries if x["seq"] == expect_seq), None)
            if anchored is None or anchored["hash"] != expect_hash:
                result.update(ok=False, first_break={
                    "seq": expect_seq, "reason": "entry at the anchored sequence differs from the saved anchor"})
    return result
