import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gateway.backends import BackendError, GenerateRequest, OllamaBackend


class FakeOllama:
    """A tiny HTTP server that records what the backend sends, like a real Ollama would receive it."""

    def __init__(self):
        self.requests = []
        self.tags = ["qwen3:8b", "qwen2.5vl:7b"]
        self.status = 200
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj, raw_lines=None):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if raw_lines is not None:
                    for ln in raw_lines:
                        self.wfile.write((json.dumps(ln) + "\n").encode())
                else:
                    self.wfile.write(json.dumps(obj).encode())

            def do_GET(self):
                outer.requests.append(("GET", self.path, None))
                self._send(200, {"models": [{"name": n} for n in outer.tags]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(("POST", self.path, body))
                if outer.status != 200:
                    return self._send(outer.status, {"error": "model not found"})
                if body.get("stream"):
                    return self._send(200, None, raw_lines=[
                        {"response": "Hel", "done": False}, {"response": "lo", "done": False}, {"response": "", "done": True}])
                self._send(200, {"model": body["model"], "response": "hello", "prompt_eval_count": 11, "eval_count": 4})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


@pytest.fixture()
def fake():
    f = FakeOllama()
    yield f
    f.stop()


def test_generate_sends_the_right_payload(fake):
    be = OllamaBackend(fake.url)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    res = be.generate(GenerateRequest(model="qwen3:8b", prompt="hi", system="be brief", json_schema=schema,
                                      think=False, keep_alive="10m", options={"temperature": 0}, images=["QUJD"]))
    assert res.text == "hello" and res.prompt_tokens == 11 and res.completion_tokens == 4 and res.latency_ms >= 0
    method, path, body = fake.requests[-1]
    assert (method, path) == ("POST", "/api/generate")
    assert body["model"] == "qwen3:8b" and body["stream"] is False and body["system"] == "be brief"
    assert body["format"] == schema and body["think"] is False and body["keep_alive"] == "10m"
    assert body["options"] == {"temperature": 0} and body["images"] == ["QUJD"]


def test_optional_fields_are_omitted_when_unset(fake):
    OllamaBackend(fake.url).generate(GenerateRequest(model="m", prompt="p"))
    body = fake.requests[-1][2]
    assert set(body) == {"model", "prompt", "stream"}


def test_streaming_yields_chunks(fake):
    assert "".join(OllamaBackend(fake.url).stream(GenerateRequest(model="m", prompt="p"))) == "Hello"


def test_health_and_listing(fake):
    be = OllamaBackend(fake.url)
    assert be.list_models() == ["qwen3:8b", "qwen2.5vl:7b"]
    assert be.health() and be.health("qwen3:8b") and not be.health("qwen2.5-coder:7b")
    fake.tags = ["mxbai-embed-large:latest"]
    assert be.health("mxbai-embed-large")            # bare name matches ":latest"


def test_unreachable_backend_is_unhealthy_and_errors_cleanly():
    be = OllamaBackend("http://127.0.0.1:1", timeout=0.5)
    assert be.health() is False
    with pytest.raises(BackendError, match="cannot reach"):
        be.generate(GenerateRequest(model="m", prompt="p"))


def test_http_error_becomes_backend_error(fake):
    fake.status = 404
    with pytest.raises(BackendError, match="404"):
        OllamaBackend(fake.url).generate(GenerateRequest(model="nope", prompt="p"))


def test_load_and_unload(fake):
    be = OllamaBackend(fake.url)
    be.load("qwen3:8b", "5m")
    be.unload("qwen3:8b")
    assert fake.requests[-2][2]["keep_alive"] == "5m"
    assert fake.requests[-1][2]["keep_alive"] == 0
