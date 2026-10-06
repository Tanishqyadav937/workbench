import json

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from gateway.main import create_app
from gateway.registry import load_registry
from gateway.selector import load_rules
from helpers import REGISTRY_DIR, ROUTER_SCHEMA, RULES_PATH, FakeBackend, FakeLedger, reply

VALIDATOR = Draft202012Validator(ROUTER_SCHEMA)


def build(backend=None, ledger=None):
    backend = backend or FakeBackend()
    ledger = ledger if ledger is not None else FakeLedger()
    reg = load_registry(REGISTRY_DIR)
    app = create_app(registry=reg, rules=load_rules(RULES_PATH, reg), backend_factory=lambda spec: backend,
                     ledger=ledger, health_ttl=0)
    return TestClient(app), backend, ledger


def test_models_endpoint_reports_health():
    c, _, _ = build(FakeBackend(installed={"qwen3:8b"}))
    ms = {m["model_id"]: m for m in c.get("/v1/models").json()["models"]}
    assert ms["qwen3:8b"]["healthy"] is True and ms["qwen2.5vl:7b"]["healthy"] is False
    assert c.get("/healthz").json()["status"] == "ok"


def test_route_code_prompt_goes_to_coder_and_is_schema_valid():
    c, _, _ = build(FakeBackend(classifier_replies=[reply("code_generation")]))
    r = c.post("/v1/route", json={"prompt": "Write a Python function", "task_id": "T-005", "node_id": "n2"})
    d = r.json()["decision"]
    assert r.status_code == 200 and d["selected"] == "qwen2.5-coder:7b" and d["rule_matched"] == "r1"
    assert not list(VALIDATOR.iter_errors(d)) and "[classified by llm]" in d["reason"]


def test_route_scan_goes_to_vision_without_classifier_call():
    c, be, _ = build()
    r = c.post("/v1/route", json={"prompt": "extract fields", "sensitivity": "confidential",
                                  "attachments": [{"name": "ir14.pdf", "mime": "application/pdf", "scanned": True}]})
    d = r.json()["decision"]
    assert d["selected"] == "qwen2.5vl:7b" and d["classification"]["modality"] == "pdf_scanned"
    assert be.calls == []


def test_generate_runs_the_chosen_model_with_registry_options():
    c, be, _ = build(FakeBackend(classifier_replies=[reply("summarization")]))
    r = c.post("/v1/generate", json={"prompt": "Summarise the pump report", "user": "u-42", "options": {"num_predict": 50}})
    body = r.json()
    assert r.status_code == 200 and body["text"] == "OK from qwen3:8b" and body["usage"]["completion_tokens"] == 3
    gen = be.calls[-1]
    assert gen.model == "qwen3:8b" and gen.think is False and gen.keep_alive == "10m" and gen.options == {"num_predict": 50}


def test_ledger_gets_hashes_not_content():
    secret = "TP3 reads 4.8 mm on confidential vessel V-210"
    c, _, led = build(FakeBackend(classifier_replies=[reply("summarization")]))
    c.post("/v1/generate", json={"prompt": secret, "user": "u-42", "task_id": "T-009", "node_id": "n3"})
    events = [e[0] for e in led.events]
    assert events == ["router.decision", "model.call"]
    dump = json.dumps(led.events)
    assert secret not in dump and "OK from qwen3:8b" not in dump          # no prompt or response text
    call = led.events[1]
    assert call[1:3] == ("system", "model-gateway")
    assert call[3]["prompt_hash"].startswith("sha256:") and call[3]["user"] == "u-42" and call[3]["task_id"] == "T-009"


def test_refuses_to_act_when_ledger_is_down():
    c, be, _ = build(ledger=FakeLedger(down=True))
    r = c.post("/v1/generate", json={"prompt": "hello"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "LEDGER_UNAVAILABLE"
    # classification may have run locally, but no generation call was made without an audit record
    assert all(call.json_schema is not None for call in be.calls)


def test_no_ledger_configured_still_works():
    reg = load_registry(REGISTRY_DIR)
    app = create_app(registry=reg, rules=load_rules(RULES_PATH, reg), backend_factory=lambda s: FakeBackend(), ledger=None, health_ttl=0)
    assert TestClient(app).post("/v1/route", json={"prompt": "hi"}).status_code == 200


def test_model_unavailable_returns_503_with_the_decision():
    c, _, _ = build(FakeBackend(installed={"qwen3:8b"}))
    r = c.post("/v1/generate", json={"prompt": "x", "attachments": [{"mime": "image/png"}]})
    d = r.json()["detail"]
    assert r.status_code == 503 and d["code"] == "MODEL_UNAVAILABLE" and d["decision"]["selected"] is None


def test_backend_failure_during_generation_is_502():
    be = FakeBackend(classifier_replies=[reply("drafting")])
    c, _, _ = build(be)
    be.fail_generate = False
    orig = be.generate
    def flaky(req):
        if req.json_schema is None:
            from gateway.backends import BackendError
            raise BackendError("GPU out of memory")
        return orig(req)
    be.generate = flaky
    r = c.post("/v1/generate", json={"prompt": "draft a note"})
    assert r.status_code == 502 and "out of memory" in r.json()["detail"]["message"]


@pytest.mark.parametrize("payload", [
    {"prompt": "x", "sensitivity": "top_secret"},
    {"prompt": "x", "task_id": "task-1"},
    {"prompt": "x", "node_id": "node1"},
    {},
])
def test_input_validation(payload):
    c, _, _ = build()
    assert c.post("/v1/route", json=payload).status_code == 422


def test_fallback_when_coder_missing_is_visible_in_decision():
    c, _, _ = build(FakeBackend(installed={"qwen3:8b"}, classifier_replies=[reply("code_generation")]))
    d = c.post("/v1/route", json={"prompt": "write code"}).json()["decision"]
    assert d["selected"] == "qwen3:8b" and d["fallback_used"] is True
