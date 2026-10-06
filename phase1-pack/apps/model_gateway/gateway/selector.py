"""Stage 2 of routing: pick a model for the classified task (design.md 3.2).

First matching rule wins. Inside a rule, take the first preferred model that is installed/healthy and approved for the
request's data sensitivity; otherwise follow that model's fallback chain; otherwise return selected=None with the reasons.
The router NEVER silently uses a model that is not approved for the data.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import yaml

from .registry import ModelSpec, RegistryError, sensitivity_rank


def load_rules(path: str, registry: Dict[str, ModelSpec]) -> Dict[str, Any]:
    with open(path) as f:
        rules = yaml.safe_load(f) or {}
    if "rules" not in rules or "default" not in rules:
        raise RegistryError("%s must contain 'rules' and 'default'" % path)
    ids = [m for r in rules["rules"] for m in r.get("prefer", [])] + [rules["default"]]
    for m in ids:
        if m not in registry:
            raise RegistryError("router_rules.yaml refers to unknown model %r" % m)
    seen = set()
    for r in rules["rules"]:
        if r.get("id") in seen:
            raise RegistryError("duplicate rule id %r" % r.get("id"))
        seen.add(r.get("id"))
    return rules


def _matches(match: Dict[str, Any], classification: Dict[str, Any]) -> bool:
    for key, want in (match or {}).items():
        have = classification.get(key)
        if isinstance(want, list):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


class Selector:
    def __init__(self, registry: Dict[str, ModelSpec], rules: Dict[str, Any], is_healthy: Callable[[ModelSpec], bool]):
        self.registry = registry
        self.rules = rules
        self.is_healthy = is_healthy

    def _chain(self, model_id: str) -> List[str]:
        chain, seen = [], set()
        while model_id and model_id not in seen and model_id in self.registry:
            chain.append(model_id)
            seen.add(model_id)
            model_id = self.registry[model_id].fallback
        return chain

    def select(self, classification: Dict[str, Any], task_id: str, node_id: str, latency_ms: float = 0.0) -> Dict[str, Any]:
        rule_id: Optional[str] = None
        prefer: List[str] = [self.rules["default"]]
        for rule in self.rules["rules"]:
            if _matches(rule.get("match", {}), classification):
                rule_id, prefer = rule["id"], list(rule["prefer"])
                break

        need = sensitivity_rank(classification["sensitivity"])
        skipped: List[str] = []
        chosen: Optional[str] = None
        tried = set()
        for base in prefer:
            for mid in self._chain(base):
                if mid in tried:
                    continue
                tried.add(mid)
                spec = self.registry[mid]
                if spec.role == "embedding":
                    skipped.append("%s skipped: embedding model" % mid)
                elif sensitivity_rank(spec.max_sensitivity) < need:
                    skipped.append("%s skipped: not approved for %s data" % (mid, classification["sensitivity"]))
                elif not self.is_healthy(spec):
                    skipped.append("%s skipped: not installed or backend unreachable" % mid)
                else:
                    chosen = mid
                    break
            if chosen:
                break

        if chosen:
            reason = "matched rule %s; " % rule_id if rule_id else "no rule matched, using default; "
            reason += "%s is healthy and approved for %s data" % (chosen, classification["sensitivity"])
            if skipped:
                reason += " (" + "; ".join(skipped) + ")"
        else:
            reason = "no usable model: " + ("; ".join(skipped) or "no candidates")

        return {
            "task_id": task_id, "node_id": node_id, "classification": classification,
            "rule_matched": rule_id, "candidates": prefer, "selected": chosen,
            "fallback_used": bool(chosen and chosen != prefer[0]),
            "reason": reason, "latency_ms": latency_ms,
        }
