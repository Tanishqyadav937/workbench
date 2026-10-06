import json
import os

from gateway.backends import BackendError, GenerateRequest, GenerateResult
from gateway.classifier import OUTPUT_SCHEMA

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY_DIR = os.path.join(ROOT, "config", "model_registry")
RULES_PATH = os.path.join(ROOT, "config", "router_rules.yaml")
ROUTER_SCHEMA = json.load(open(os.path.join(ROOT, "schemas", "router_decision.schema.json")))
ALL_MODELS = {"qwen3:8b", "qwen2.5-coder:7b", "qwen2.5vl:7b", "mxbai-embed-large"}


class FakeBackend:
    """Stands in for Ollama. `installed` controls health; `classifier_replies` is a queue of raw strings."""

    def __init__(self, installed=None, classifier_replies=None):
        self.installed = set(ALL_MODELS if installed is None else installed)
        self.classifier_replies = list(classifier_replies or [])
        self.calls = []
        self.fail_generate = False

    def health(self, model=None):
        return model is None or model in self.installed

    def generate(self, req: GenerateRequest):
        self.calls.append(req)
        if self.fail_generate:
            raise BackendError("boom")
        if req.json_schema == OUTPUT_SCHEMA:
            text = self.classifier_replies.pop(0) if self.classifier_replies else json.dumps(
                {"task_type": "general_chat", "complexity": "low", "confidence": 0.9})
            return GenerateResult(text=text, model=req.model)
        return GenerateResult(text="OK from " + req.model, model=req.model, prompt_tokens=7, completion_tokens=3, latency_ms=12.5)


def cls(task_type="general_chat", modality="text", complexity="low", sensitivity="internal", confidence=0.9):
    return {"task_type": task_type, "modality": modality, "complexity": complexity,
            "sensitivity": sensitivity, "confidence": confidence}


def reply(task_type, complexity="medium", confidence=0.9):
    return json.dumps({"task_type": task_type, "complexity": complexity, "confidence": confidence})


class FakeLedger:
    def __init__(self, down=False):
        self.events = []
        self.down = down

    def log(self, event, actor_type, actor_id, payload=None):
        from ledger_client import LedgerUnavailable
        if self.down:
            raise LedgerUnavailable("down")
        self.events.append((event, actor_type, actor_id, payload))
