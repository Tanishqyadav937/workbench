"""Tiny client for the ledger service. Standard library only (Python 3.9+).

    from ledger_client import LedgerClient, hash_obj
    ledger = LedgerClient()                      # reads LEDGER_URL / LEDGER_TOKEN from the environment
    ledger.log("tool.call", "agent", "orchestrator",
               {"tool": "run_python", "args_hash": hash_obj(args)})

Rules for payloads: log ids and HASHES, never document content or personal data.

strict=True (default): if the ledger is unreachable, raise LedgerUnavailable so the action can be refused.
strict=False: write the event to a local spool file and replay it later with flush_spool().
              Spooled events are NOT part of the chain until replayed; use only in development.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class LedgerUnavailable(Exception):
    pass


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_obj(obj: Any) -> str:
    """sha256 of any JSON-able value, in the same format the ledger uses."""
    return "sha256:" + hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class LedgerClient:
    def __init__(self, url: Optional[str] = None, token: Optional[str] = None, strict: bool = True,
                 spool_path: str = "ledger_spool.jsonl", timeout: float = 3.0):
        self.url = (url if url is not None else os.environ.get("LEDGER_URL", "")).rstrip("/")
        self.token = token if token is not None else os.environ.get("LEDGER_TOKEN")
        self.strict = strict
        self.spool_path = spool_path
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("X-Ledger-Token", self.token)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def log(self, event: str, actor_type: str, actor_id: str,
            payload: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Append one event. Returns the stored entry, or None if logging is disabled (no LEDGER_URL)."""
        if not self.enabled:
            return None
        body = {"actor": {"type": actor_type, "id": actor_id}, "event": event, "payload": payload or {}}
        try:
            return self._request("POST", "/v1/ledger/events", body)
        except urllib.error.HTTPError as e:
            # The ledger answered but refused: that is a caller bug, never spool it.
            raise ValueError("ledger rejected event: %s" % e.read().decode("utf-8", "replace")) from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if self.strict:
                raise LedgerUnavailable("ledger unreachable: %s" % e) from e
            with open(self.spool_path, "a") as f:
                f.write(_canonical(body) + "\n")
            return None

    def flush_spool(self) -> int:
        """Replay spooled events in order. Returns how many were sent."""
        if not os.path.exists(self.spool_path):
            return 0
        with open(self.spool_path) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        sent = 0
        for ln in lines:
            self._request("POST", "/v1/ledger/events", json.loads(ln))
            sent += 1
        os.remove(self.spool_path)
        return sent

    def head(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/ledger/head")

    def verify(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/ledger/verify")
