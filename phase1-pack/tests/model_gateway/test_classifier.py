import json

import pytest

from gateway.classifier import OUTPUT_SCHEMA, TASK_TYPES, IntentClassifier, detect_modality
from helpers import ROUTER_SCHEMA, FakeBackend, reply


@pytest.mark.parametrize("atts, expected", [
    ([], "text"),
    ([{"mime": "application/pdf"}], "text"),
    ([{"mime": "application/pdf", "scanned": True}], "pdf_scanned"),
    ([{"mime": "image/png"}], "image"),
    ([{"mime": "image/png"}, {"mime": "application/pdf"}], "mixed"),
    ([{"mime": "image/png"}, {"mime": "application/pdf", "scanned": True}], "mixed"),
    ([{"mime": "image/png"}, {"mime": "image/jpeg"}], "image"),
])
def test_detect_modality(atts, expected):
    assert detect_modality(atts) == expected


def test_task_types_match_router_schema():
    schema_types = set(ROUTER_SCHEMA["properties"]["classification"]["properties"]["task_type"]["enum"])
    assert set(TASK_TYPES) == schema_types
    assert set(OUTPUT_SCHEMA["properties"]["task_type"]["enum"]) == schema_types - {"ocr_extraction"}


def test_scans_skip_the_llm_entirely():
    fb = FakeBackend()
    c, src = IntentClassifier(fb).classify("read this", [{"mime": "application/pdf", "scanned": True}], "confidential")
    assert src == "prefilter" and c["task_type"] == "ocr_extraction" and c["modality"] == "pdf_scanned"
    assert c["sensitivity"] == "confidential" and fb.calls == []   # no model swap needed


def test_llm_classification_and_request_shape():
    fb = FakeBackend(classifier_replies=[reply("code_generation", "medium", 0.93)])
    c, src = IntentClassifier(fb, "qwen3:8b").classify("Write a Python function", [], "internal")
    assert src == "llm" and c["task_type"] == "code_generation" and c["complexity"] == "medium"
    req = fb.calls[0]
    assert req.think is False                      # otherwise the reasoning block breaks JSON output
    assert req.json_schema == OUTPUT_SCHEMA and req.options["temperature"] == 0 and req.model == "qwen3:8b"


def test_confidence_is_clamped():
    fb = FakeBackend(classifier_replies=[reply("drafting", "low", 7.5)])
    c, _ = IntentClassifier(fb).classify("x", [], "internal")
    assert c["confidence"] == 1.0


def test_retry_once_on_malformed_then_succeed():
    fb = FakeBackend(classifier_replies=["not json at all", reply("summarization")])
    c, src = IntentClassifier(fb).classify("summarise", [], "internal")
    assert src == "llm" and c["task_type"] == "summarization" and len(fb.calls) == 2


@pytest.mark.parametrize("bad", ["garbage", json.dumps({"task_type": "make_coffee", "complexity": "low", "confidence": 1}),
                                 json.dumps({"task_type": "ocr_extraction", "complexity": "low", "confidence": 1}),
                                 json.dumps({"task_type": "drafting"})])
def test_two_bad_answers_use_safe_fallback(bad):
    fb = FakeBackend(classifier_replies=[bad, bad])
    c, src = IntentClassifier(fb).classify("x", [], "internal")
    assert src == "fallback" and c["task_type"] == "analysis" and c["confidence"] == 0.0


def test_backend_error_uses_fallback():
    fb = FakeBackend()
    fb.fail_generate = True
    c, src = IntentClassifier(fb).classify("x", [], "internal")
    assert src == "fallback"
