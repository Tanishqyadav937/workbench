import dataclasses
import os

import pytest
from jsonschema import Draft202012Validator

from gateway.registry import RegistryError, load_registry
from gateway.selector import Selector, load_rules
from helpers import REGISTRY_DIR, ROUTER_SCHEMA, RULES_PATH, cls

VALIDATOR = Draft202012Validator(ROUTER_SCHEMA)


@pytest.fixture()
def registry():
    return load_registry(REGISTRY_DIR)


@pytest.fixture()
def rules(registry):
    return load_rules(RULES_PATH, registry)


def make_selector(registry, rules, unhealthy=()):
    return Selector(registry, rules, lambda spec: spec.model_id not in unhealthy)


def test_real_registry_loads(registry):
    assert set(registry) == {"qwen3:8b", "qwen2.5-coder:7b", "qwen2.5vl:7b", "mxbai-embed-large"}
    assert registry["qwen3:8b"].request_options["think"] is False
    assert registry["mxbai-embed-large"].role == "embedding"
    assert registry["qwen2.5-coder:7b"].fallback == "qwen3:8b"


def test_registry_errors(tmp_path):
    (tmp_path / "a.yaml").write_text("model_id: a\nbackend: ollama\n")
    with pytest.raises(RegistryError, match="endpoint"):
        load_registry(str(tmp_path))
    (tmp_path / "a.yaml").write_text("model_id: a\nbackend: ollama\nendpoint: http://x\nfallback: ghost\n")
    with pytest.raises(RegistryError, match="fallback"):
        load_registry(str(tmp_path))
    (tmp_path / "a.yaml").write_text("model_id: a\nbackend: ollama\nendpoint: http://x\n")
    (tmp_path / "b.yaml").write_text("model_id: a\nbackend: ollama\nendpoint: http://x\n")
    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(str(tmp_path))
    with pytest.raises(RegistryError, match="no model YAML"):
        load_registry(str(tmp_path / "empty-does-not-exist"))


def test_adding_a_model_needs_only_a_yaml_file(tmp_path, registry):
    import shutil
    shutil.copytree(REGISTRY_DIR, str(tmp_path / "reg"))
    (tmp_path / "reg" / "new.yaml").write_text("model_id: llama3.1:8b\nbackend: ollama\nendpoint: http://x\ncapabilities: [general_chat]\n")
    assert "llama3.1:8b" in load_registry(str(tmp_path / "reg"))


def test_rules_reject_unknown_model(tmp_path, registry):
    p = tmp_path / "r.yaml"
    p.write_text("rules:\n  - id: r1\n    match: {task_type: code_generation}\n    prefer: [ghost-model]\ndefault: qwen3:8b\n")
    with pytest.raises(RegistryError, match="unknown model"):
        load_rules(str(p), registry)


@pytest.mark.parametrize("c, expected, rule", [
    (cls("code_generation"), "qwen2.5-coder:7b", "r1"),
    (cls("ocr_extraction", modality="pdf_scanned"), "qwen2.5vl:7b", "r2"),
    (cls("summarization", modality="text", complexity="medium"), "qwen3:8b", "r3"),
    (cls("drafting"), "qwen3:8b", "r3"),
    (cls("retrieval_qa"), "qwen3:8b", "r3"),
    (cls("general_chat"), "qwen3:8b", "r4"),
    (cls("general_chat", modality="image"), "qwen2.5vl:7b", "r2"),
])
def test_routing_table(registry, rules, c, expected, rule):
    d = make_selector(registry, rules).select(c, "T-001", "n1")
    assert d["selected"] == expected and d["rule_matched"] == rule and d["fallback_used"] is False
    assert not list(VALIDATOR.iter_errors(d)), list(VALIDATOR.iter_errors(d))


def test_first_matching_rule_wins(registry, rules):
    # a scanned code request matches r1 (code) before r2 (modality) because r1 is listed first
    d = make_selector(registry, rules).select(cls("code_generation", modality="image"), "T-1", "n1")
    assert d["rule_matched"] == "r1"


def test_falls_back_when_preferred_model_is_down(registry, rules):
    d = make_selector(registry, rules, unhealthy={"qwen2.5-coder:7b"}).select(cls("code_generation"), "T-1", "n1")
    assert d["selected"] == "qwen3:8b" and d["fallback_used"] is True
    assert "qwen2.5-coder:7b skipped" in d["reason"]
    assert not list(VALIDATOR.iter_errors(d))


def test_no_fallback_for_vision_model_means_explicit_failure(registry, rules):
    d = make_selector(registry, rules, unhealthy={"qwen2.5vl:7b"}).select(cls("ocr_extraction", modality="image"), "T-1", "n1")
    assert d["selected"] is None and "no usable model" in d["reason"]
    assert not list(VALIDATOR.iter_errors(d))


def test_sensitivity_gate_never_uses_unapproved_model(registry, rules):
    limited = dict(registry)
    limited["qwen3:8b"] = dataclasses.replace(registry["qwen3:8b"], max_sensitivity="internal")
    sel = Selector(limited, rules, lambda s: True)
    ok = sel.select(cls("drafting", sensitivity="internal"), "T-1", "n1")
    assert ok["selected"] == "qwen3:8b"
    blocked = sel.select(cls("drafting", sensitivity="confidential"), "T-1", "n1")
    assert blocked["selected"] is None and "not approved for confidential" in blocked["reason"]


def test_fallback_cycle_does_not_hang(registry, rules):
    cyc = dict(registry)
    cyc["qwen3:8b"] = dataclasses.replace(registry["qwen3:8b"], fallback="qwen2.5-coder:7b")
    cyc["qwen2.5-coder:7b"] = dataclasses.replace(registry["qwen2.5-coder:7b"], fallback="qwen3:8b")
    d = Selector(cyc, rules, lambda s: False).select(cls("drafting"), "T-1", "n1")
    assert d["selected"] is None


def test_embedding_models_are_never_selected(registry, rules):
    r = dict(registry)
    sel = Selector(r, {"rules": [], "default": "mxbai-embed-large"}, lambda s: True)
    assert sel.select(cls(), "T-1", "n1")["selected"] is None
