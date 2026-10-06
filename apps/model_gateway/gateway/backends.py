"""Model backends. The rest of the system only ever sees ModelBackend; swapping Ollama for vLLM means
writing one more class here (design principle: everything behind a contract). Standard library only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


class BackendError(Exception):
    pass


@dataclass
class GenerateRequest:
    model: str
    prompt: str
    system: Optional[str] = None
    images: List[str] = field(default_factory=list)      # base64-encoded images (vision models)
    json_schema: Optional[Dict[str, Any]] = None         # constrain output to this JSON schema
    options: Dict[str, Any] = field(default_factory=dict)  # temperature, num_predict, ...
    think: Optional[bool] = None                         # False = no reasoning block (needed for JSON output)
    keep_alive: Optional[str] = None


@dataclass
class GenerateResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


class OllamaBackend:
    """Talks to Ollama's HTTP API. base_url is where THIS process can reach Ollama, e.g.
    http://llm-bridge:11434 inside Docker, or http://localhost:11434 on the Mac."""

    def __init__(self, base_url: str, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ---- plumbing
    def _open(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            raise BackendError("Ollama HTTP %d on %s: %s" % (e.code, path, e.read().decode("utf-8", "replace")[:300])) from e
        except (urllib.error.URLError, OSError) as e:
            raise BackendError("cannot reach Ollama at %s: %s" % (self.base_url, e)) from e

    def _json(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        with self._open(method, path, body, timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _payload(req: GenerateRequest, stream: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": req.model, "prompt": req.prompt, "stream": stream}
        if req.system:
            body["system"] = req.system
        if req.images:
            body["images"] = req.images
        if req.json_schema is not None:
            body["format"] = req.json_schema
        if req.think is not None:
            body["think"] = req.think
        if req.keep_alive is not None:
            body["keep_alive"] = req.keep_alive
        if req.options:
            body["options"] = req.options
        return body

    # ---- contract
    def generate(self, req: GenerateRequest) -> GenerateResult:
        t0 = time.time()
        out = self._json("POST", "/api/generate", self._payload(req, stream=False))
        return GenerateResult(
            text=out.get("response", ""),
            model=out.get("model", req.model),
            prompt_tokens=int(out.get("prompt_eval_count", 0)),
            completion_tokens=int(out.get("eval_count", 0)),
            latency_ms=round((time.time() - t0) * 1000, 1),
        )

    def stream(self, req: GenerateRequest) -> Iterator[str]:
        with self._open("POST", "/api/generate", self._payload(req, stream=True)) as resp:
            for line in resp:
                line = line.strip()
                if not line:
                    continue
                chunk = json.loads(line.decode("utf-8"))
                if chunk.get("response"):
                    yield chunk["response"]
                if chunk.get("done"):
                    break

    def list_models(self) -> List[str]:
        out = self._json("GET", "/api/tags", timeout=5)
        return [m["name"] for m in out.get("models", [])]

    def health(self, model: Optional[str] = None) -> bool:
        """True if Ollama answers and (when given) the model is installed."""
        try:
            names = self.list_models()
        except BackendError:
            return False
        if model is None:
            return True
        return model in names or (":" not in model and (model + ":latest") in names)

    def load(self, model: str, keep_alive: str = "10m") -> None:
        self._json("POST", "/api/generate", {"model": model, "keep_alive": keep_alive, "stream": False})

    def unload(self, model: str) -> None:
        self._json("POST", "/api/generate", {"model": model, "keep_alive": 0, "stream": False})
