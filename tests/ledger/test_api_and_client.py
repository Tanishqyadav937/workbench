import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from ledger_client import LedgerClient, LedgerUnavailable, hash_obj
from ledger_service.chain import hash_obj as server_hash_obj
from ledger_service.main import create_app


def test_client_and_server_hash_agree():
    obj = {"b": 1, "a": [1, 2, {"z": "é"}]}
    assert hash_obj(obj) == server_hash_obj(obj)


def test_api_roundtrip(tmp_path):
    c = TestClient(create_app(str(tmp_path / "l.db"), token=""))
    r = c.post("/v1/ledger/events", json={"actor": {"type": "user", "id": "u-42"}, "event": "auth.login", "payload": {"ip": "10.0.0.5"}})
    assert r.status_code == 201 and r.json()["seq"] == 1
    c.post("/v1/ledger/events", json={"actor": {"type": "agent", "id": "orch"}, "event": "tool.call", "payload": {}})
    assert c.get("/v1/ledger/head").json()["seq"] == 2
    ev = c.get("/v1/ledger/events", params={"event": "tool.call"}).json()["events"]
    assert len(ev) == 1 and ev[0]["actor"]["id"] == "orch"
    assert c.get("/v1/ledger/verify").json()["ok"] is True
    assert c.get("/healthz").json()["status"] == "ok"


def test_api_validation_errors(tmp_path):
    c = TestClient(create_app(str(tmp_path / "l.db"), token=""))
    r = c.post("/v1/ledger/events", json={"actor": {"type": "user", "id": "u"}, "event": "nope", "payload": {}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert c.get("/v1/ledger/events", params={"event": "nope"}).status_code == 422


def test_api_token_required_for_writes_only(tmp_path):
    c = TestClient(create_app(str(tmp_path / "l.db"), token="s3cret"))
    body = {"actor": {"type": "system", "id": "x"}, "event": "auth.login", "payload": {}}
    assert c.post("/v1/ledger/events", json=body).status_code == 401
    assert c.post("/v1/ledger/events", json=body, headers={"X-Ledger-Token": "s3cret"}).status_code == 201
    assert c.get("/v1/ledger/head").status_code == 200


@pytest.fixture()
def live_server(tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(create_app(str(tmp_path / "live.db"), token=""), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield "http://127.0.0.1:%d" % port
    server.should_exit = True
    t.join(timeout=5)


def test_client_against_live_server(live_server):
    cl = LedgerClient(url=live_server)
    e = cl.log("model.call", "system", "model-gateway", {"model": "qwen3:8b", "prompt_hash": hash_obj("hi")})
    assert e["seq"] == 1
    assert cl.head()["seq"] == 1 and cl.verify()["ok"]


def test_client_rejected_event_is_a_value_error(live_server):
    with pytest.raises(ValueError):
        LedgerClient(url=live_server).log("not.an.event", "system", "x")


def test_client_disabled_without_url(monkeypatch):
    monkeypatch.delenv("LEDGER_URL", raising=False)
    cl = LedgerClient()
    assert cl.enabled is False and cl.log("auth.login", "user", "u") is None


def test_client_strict_vs_spool(tmp_path, live_server):
    dead = "http://127.0.0.1:1"  # nothing listens here
    with pytest.raises(LedgerUnavailable):
        LedgerClient(url=dead, strict=True, timeout=0.5).log("auth.login", "user", "u")
    spool = str(tmp_path / "spool.jsonl")
    lenient = LedgerClient(url=dead, strict=False, spool_path=spool, timeout=0.5)
    assert lenient.log("auth.login", "user", "u1") is None
    assert lenient.log("auth.login", "user", "u2") is None
    assert os.path.exists(spool)
    replay = LedgerClient(url=live_server, spool_path=spool)
    assert replay.flush_spool() == 2 and not os.path.exists(spool)
    assert replay.head()["seq"] == 2
