#!/usr/bin/env python3
"""Task B5: measure routing accuracy on the labelled prompts, using the REAL local models.

Run on the Mac with Ollama running (no Docker needed):
    pip install pyyaml
    python3 scripts/eval_router.py
Optional: OLLAMA_URL=http://localhost:11434 python3 scripts/eval_router.py --verbose

Target (PRD success metric): at least 90% routed to the expected model.
Needs only the chat model (qwen3:8b); scans are routed by rules without calling any model.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "model_gateway"))

from gateway.backends import OllamaBackend  # noqa: E402
from gateway.classifier import IntentClassifier  # noqa: E402
from gateway.registry import load_registry  # noqa: E402
from gateway.selector import Selector, load_rules  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true", help="print every prompt")
    ap.add_argument("--classifier-model", default="qwen3:8b")
    ap.add_argument("--prompts", default=os.path.join(ROOT, "tests", "data", "router_eval_prompts.json"), help="path to labelled prompts JSON")
    args = ap.parse_args()

    url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    backend = OllamaBackend(url, timeout=180)
    if not backend.health(args.classifier_model):
        print("ERROR: Ollama not reachable at %s or %s not installed" % (url, args.classifier_model))
        return 2

    registry = load_registry(os.path.join(ROOT, "config", "model_registry"))
    rules = load_rules(os.path.join(ROOT, "config", "router_rules.yaml"), registry)
    selector = Selector(registry, rules, lambda spec: True)   # assume all models installed: we test routing, not health
    classifier = IntentClassifier(backend, args.classifier_model)

    prompts = json.load(open(args.prompts))["prompts"]
    model_ok = task_ok = 0
    misses = []
    t0 = time.time()
    for i, p in enumerate(prompts, 1):
        cls, source = classifier.classify(p["prompt"], p.get("attachments", []), "internal")
        decision = selector.select(cls, "T-000", "n0")
        m_hit = decision["selected"] == p["expected_model"]
        t_hit = cls["task_type"] == p["expected_task"]
        model_ok += m_hit
        task_ok += t_hit
        if args.verbose or not m_hit:
            flag = "ok  " if m_hit else "MISS"
            print("%s %2d  task=%-14s (want %-14s) model=%-18s src=%s conf=%.2f | %s" % (
                flag, i, cls["task_type"], p["expected_task"], decision["selected"], source, cls["confidence"], p["prompt"][:60]))
        if not m_hit:
            misses.append(i)
    n = len(prompts)
    print("\nrouted to the expected model: %d/%d = %.0f%%   (target >= 90%%)" % (model_ok, n, 100.0 * model_ok / n))
    print("task type exactly right:      %d/%d = %.0f%%   (informational: several task types share a model)" % (task_ok, n, 100.0 * task_ok / n))
    print("elapsed: %.0f s" % (time.time() - t0))
    return 0 if model_ok / n >= 0.9 else 1


if __name__ == "__main__":
    sys.exit(main())
