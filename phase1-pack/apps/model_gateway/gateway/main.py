"""Model gateway HTTP service (internal network only).

POST /v1/route     classify + choose a model, return the RouterDecision          (B3, B4)
POST /v1/generate  route, then run the chosen model and return its output         (B1, B6)
GET  /v1/models    registry with live health                                       (FR-1.4)

Every routing decision and every model call is written to the ledger (hashes and ids only, never content).
If the ledger is configured but unreachable the request is REFUSED: no audit record, no action.
"""
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .backends import BackendError, GenerateRequest, OllamaBackend
from .classifier import IntentClassifier
from .registry import SENSITIVITY_LEVELS, ModelSpec, load_registry
from .selector import Selector, load_rules

try:  # libs/ledger_client.py is copied next to this package in the Docker image
    from ledger_client import LedgerClient, LedgerUnavailable, hash_text
except ImportError:  # pragma: no cover
    LedgerClient = None


class AttachmentIn(BaseModel):
    name: str = ""
    mime: str = "text/plain"
    scanned: bool = False


class RouteIn(BaseModel):
    prompt: str
    task_id: str = Field(default="T-000", pattern=r"^T-[0-9]+$")
    node_id: str = Field(default="n0", pattern=r"^n[0-9]+$")
    attachments: List[AttachmentIn] = []
    sensitivity: str = "internal"
    user: str = "anonymous"


class GenerateIn(RouteIn):
    system: Optional[str] = None
    images: List[str] = []
    json_schema: Optional[Dict[str, Any]] = None
    options: Dict[str, Any] = {}


def default_backend_factory(spec: ModelSpec):
    override = os.environ.get("OLLAMA_URL_OVERRIDE")  # e.g. http://localhost:11434 when running on the Mac host
    if spec.backend == "ollama":
        return OllamaBackend(override or spec.endpoint)
    raise ValueError("unsupported backend %r (only 'ollama' is implemented; add vLLM in backends.py)" % spec.backend)


def create_app(registry: Optional[Dict[str, ModelSpec]] = None, rules: Optional[Dict[str, Any]] = None,
               backend_factory: Callable[[ModelSpec], Any] = default_backend_factory,
               ledger: Optional[Any] = None, classifier_model: str = "qwen3:8b",
               health_ttl: float = 5.0) -> FastAPI:
    registry = registry or load_registry(os.environ.get("MODEL_REGISTRY_DIR", "/config/model_registry"))
    rules = rules or load_rules(os.environ.get("ROUTER_RULES", "/config/router_rules.yaml"), registry)
    if ledger is None and LedgerClient is not None:
        ledger = LedgerClient(strict=os.environ.get("LEDGER_STRICT", "1") == "1")

    backends: Dict[str, Any] = {}
    health_cache: Dict[str, Any] = {}

    def backend_for(spec: ModelSpec):
        if spec.model_id not in backends:
            backends[spec.model_id] = backend_factory(spec)
        return backends[spec.model_id]

    def is_healthy(spec: ModelSpec) -> bool:
        now = time.time()
        hit = health_cache.get(spec.model_id)
        if hit and now - hit[0] < health_ttl:
            return hit[1]
        ok = bool(backend_for(spec).health(spec.model_id))
        health_cache[spec.model_id] = (now, ok)
        return ok

    classifier = IntentClassifier(backend_for(registry[classifier_model]), classifier_model)
    selector = Selector(registry, rules, is_healthy)

    def log(event: str, payload: Dict[str, Any]) -> None:
        if ledger is None:
            return
        try:
            ledger.log(event, "system", "model-gateway", payload)
        except LedgerUnavailable as e:
            raise HTTPException(status_code=503, detail={
                "code": "LEDGER_UNAVAILABLE", "message": "refusing to act without an audit record: %s" % e})

    def route(body: RouteIn) -> Dict[str, Any]:
        if body.sensitivity not in SENSITIVITY_LEVELS:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_FAILED", "message": "sensitivity must be one of %s" % (SENSITIVITY_LEVELS,)})
        t0 = time.time()
        classification, source = classifier.classify(body.prompt, [a.model_dump() for a in body.attachments], body.sensitivity)
        decision = selector.select(classification, body.task_id, body.node_id)
        decision["latency_ms"] = round((time.time() - t0) * 1000, 1)
        decision["reason"] += " [classified by %s]" % source
        log("router.decision", dict(decision, user=body.user))
        return decision

    app = FastAPI(title="Workbench model gateway", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"status": "ok", "models": len(registry)}

    @app.get("/v1/models")
    def models() -> Dict[str, Any]:
        out = []
        for spec in registry.values():
            try:
                healthy = is_healthy(spec)
            except Exception:  # noqa: BLE001
                healthy = False
            out.append({"model_id": spec.model_id, "role": spec.role, "capabilities": list(spec.capabilities),
                        "approx_memory_gb": spec.approx_memory_gb, "fallback": spec.fallback or None,
                        "max_sensitivity": spec.max_sensitivity, "healthy": healthy})
        return {"models": out}

    @app.post("/v1/route")
    def route_endpoint(body: RouteIn) -> Dict[str, Any]:
        return {"decision": route(body)}

    @app.post("/v1/generate")
    def generate(body: GenerateIn) -> Dict[str, Any]:
        decision = route(body)
        if not decision["selected"]:
            raise HTTPException(status_code=503, detail={"code": "MODEL_UNAVAILABLE", "message": decision["reason"], "decision": decision})
        spec = registry[decision["selected"]]
        opts = dict(spec.request_options)
        think, keep_alive = opts.pop("think", None), opts.pop("keep_alive", None)
        opts.update(body.options)
        try:
            res = backend_for(spec).generate(GenerateRequest(
                model=spec.model_id, prompt=body.prompt, system=body.system, images=body.images,
                json_schema=body.json_schema, options=opts, think=think, keep_alive=keep_alive))
        except BackendError as e:
            raise HTTPException(status_code=502, detail={"code": "MODEL_UNAVAILABLE", "message": str(e), "decision": decision})
        log("model.call", {"task_id": body.task_id, "node_id": body.node_id, "user": body.user, "model": res.model,
                           "prompt_hash": hash_text(body.prompt), "response_hash": hash_text(res.text),
                           "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
                           "latency_ms": res.latency_ms})
        return {"decision": decision, "text": res.text,
                "usage": {"prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens, "latency_ms": res.latency_ms}}

    return app


_app = None


def get_app() -> FastAPI:
    global _app
    if _app is None:
        _app = create_app()
    return _app
