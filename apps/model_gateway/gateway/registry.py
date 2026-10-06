"""Model registry: YAML files in config/model_registry/ become ModelSpec objects.
Adding a model = adding a YAML file (FR-1.2). No code change."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

import yaml

SENSITIVITY_LEVELS = ("public", "internal", "confidential", "board_only")  # low -> high


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    backend: str
    endpoint: str
    capabilities: Tuple[str, ...] = ()
    role: str = "chat"                      # chat | embedding
    context_window: int = 0
    approx_memory_gb: float = 0.0
    priority: int = 1
    fallback: str = ""                      # model_id to try if this one is unavailable ("" = none)
    max_sensitivity: str = "board_only"     # highest data sensitivity this model is approved to process
    request_options: Dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


def sensitivity_rank(level: str) -> int:
    try:
        return SENSITIVITY_LEVELS.index(level)
    except ValueError:
        raise RegistryError("unknown sensitivity level %r" % level)


def load_registry(directory: str) -> Dict[str, ModelSpec]:
    files = sorted(glob.glob(os.path.join(directory, "*.yaml")) + glob.glob(os.path.join(directory, "*.yml")))
    if not files:
        raise RegistryError("no model YAML files found in %s" % directory)
    registry: Dict[str, ModelSpec] = {}
    for path in files:
        with open(path) as f:
            d = yaml.safe_load(f) or {}
        for key in ("model_id", "backend", "endpoint"):
            if not d.get(key):
                raise RegistryError("%s: missing required field %r" % (os.path.basename(path), key))
        if d["model_id"] in registry:
            raise RegistryError("%s: duplicate model_id %r" % (os.path.basename(path), d["model_id"]))
        spec = ModelSpec(
            model_id=d["model_id"], backend=d["backend"], endpoint=d["endpoint"],
            capabilities=tuple(d.get("capabilities") or ()), role=d.get("role", "chat"),
            context_window=int(d.get("context_window", 0)), approx_memory_gb=float(d.get("approx_memory_gb", 0)),
            priority=int(d.get("priority", 1)), fallback=d.get("fallback") or "",
            max_sensitivity=d.get("max_sensitivity", "board_only"),
            request_options=dict(d.get("request_options") or {}),
        )
        sensitivity_rank(spec.max_sensitivity)
        registry[spec.model_id] = spec
    for spec in registry.values():
        if spec.fallback and spec.fallback not in registry:
            raise RegistryError("%s: fallback %r is not in the registry" % (spec.model_id, spec.fallback))
    return registry
