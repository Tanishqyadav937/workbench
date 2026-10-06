"""End to end (in one process): real gateway + real ledger service over HTTP + fake Ollama backend."""
import os
import socket
import sqlite3
import threading
import time

import sys

import pytest
import uvicorn
from fastapi.testclient import TestClient

from gateway.main import create_app as create_gateway
from gateway.registry import load_registry
from gateway.selector import load_rules
from ledger_client import LedgerClient
from ledger_service.main import create_app as create_ledger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "model_gateway"))
from helpers import REGISTRY_DIR, RULES_PATH, FakeBackend, reply  # noqa: E402


@pytest.fixture()
def stack(tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    db = str(tmp_path / "ledger.db")
    server = uvicorn.Server(uvicorn.Config(create_ledger(db, token=""), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    ledger = LedgerClient(url="http://127.0.0.1:%d" % port)
    reg = load_registry(REGISTRY_DIR)
    be = FakeBackend(classifier_replies=[reply("code_generation"), reply("summarization")])
    app = create_gateway(registry=reg, rules=load_rules(RULES_PATH, reg), backend_factory=lambda s: be, ledger=ledger, health_ttl=0)
    yield TestClient(app), ledger, db
    server.should_exit = True
    t.join(timeout=5)


def test_two_requests_produce_a_verifiable_audit_trail(stack):
    gw, ledger, db = stack
    r1 = gw.post("/v1/generate", json={"prompt": "write a python function", "user": "u-1", "task_id": "T-1", "node_id": "n1"})
    r2 = gw.post("/v1/generate", json={"prompt": "summarise the report", "user": "u-1", "task_id": "T-1", "node_id": "n2"})
    assert r1.json()["decision"]["selected"] == "qwen2.5-coder:7b"
    assert r2.json()["decision"]["selected"] == "qwen3:8b"

    ev = ledger._request("GET", "/v1/ledger/events")["events"]
    assert [e["event"] for e in ev] == ["router.decision", "model.call", "router.decision", "model.call"]
    assert ev[0]["actor"] == {"type": "system", "id": "model-gateway"}
    assert ledger.verify()["ok"] is True

    # tamper with the stored record of which model handled request 1
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("DROP TRIGGER ledger_no_update")
    c.execute("UPDATE ledger SET payload = replace(payload, 'qwen2.5-coder:7b', 'qwen3:8b') WHERE seq = 1")
    bad = ledger.verify()
    assert bad["ok"] is False and bad["first_break"]["seq"] == 1
