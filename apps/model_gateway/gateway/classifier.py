"""Stage 1 of routing: work out what kind of task this is (design.md 3.2).

Cheap deterministic rules first (attachments tell us the modality for free), the small LLM only when needed.
A scanned document or image goes straight to the vision path WITHOUT loading the classifier model,
which saves a model swap on a 16 GB machine.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .backends import BackendError, GenerateRequest

TASK_TYPES = ("general_chat", "summarization", "drafting", "code_generation", "ocr_extraction", "retrieval_qa", "analysis")
MODALITIES = ("text", "image", "pdf_scanned", "mixed")
COMPLEXITIES = ("low", "medium", "high")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "task_type": {"type": "string", "enum": [t for t in TASK_TYPES if t != "ocr_extraction"]},
        "complexity": {"type": "string", "enum": list(COMPLEXITIES)},
        "confidence": {"type": "number"},
    },
    "required": ["task_type", "complexity", "confidence"],
}

SYSTEM_PROMPT = """You classify a user's request for an engineering assistant. Answer ONLY with JSON.
task_type:
- code_generation: the user wants code, a script, a function, a formula in code, SQL, or a calculation done by running code
- summarization: condense or explain an existing document or text
- drafting: write a new document, note, email, report or memo
- retrieval_qa: a factual question answered from company documents, SOPs or reports
- analysis: reasoning, comparison or judgement over given information
- general_chat: greetings, small talk or simple general questions
complexity: low (one short step), medium (a few steps), high (long, multi-part or needs careful reasoning).
confidence: your confidence from 0 to 1."""


def detect_modality(attachments: List[Dict[str, Any]]) -> str:
    """Deterministic: derived from the attachments, never guessed by a model."""
    visual, text = set(), False
    for a in attachments or []:
        mime = (a.get("mime") or "").lower()
        if mime.startswith("image/"):
            visual.add("image")
        elif mime == "application/pdf" and a.get("scanned"):
            visual.add("pdf_scanned")
        else:
            text = True
    if not visual:
        return "text"
    if text or len(visual) > 1:
        return "mixed"
    return next(iter(visual))


class IntentClassifier:
    def __init__(self, backend, model_id: str = "qwen3:8b"):
        self.backend = backend
        self.model_id = model_id

    def classify(self, prompt: str, attachments: List[Dict[str, Any]], sensitivity: str) -> Tuple[Dict[str, Any], str]:
        """Returns (classification, source) where source is 'prefilter', 'llm' or 'fallback'."""
        modality = detect_modality(attachments)
        base = {"modality": modality, "sensitivity": sensitivity}

        if modality in ("image", "pdf_scanned"):
            return dict(base, task_type="ocr_extraction", complexity="medium", confidence=1.0), "prefilter"

        for _ in range(2):  # one retry on malformed output
            try:
                res = self.backend.generate(GenerateRequest(
                    model=self.model_id, prompt=prompt[:2000], system=SYSTEM_PROMPT,
                    json_schema=OUTPUT_SCHEMA, think=False, keep_alive="10m",
                    options={"temperature": 0, "num_predict": 100}))
                data = json.loads(res.text)
                task, comp, conf = data["task_type"], data["complexity"], float(data["confidence"])
                if task in TASK_TYPES and task != "ocr_extraction" and comp in COMPLEXITIES:
                    return dict(base, task_type=task, complexity=comp, confidence=max(0.0, min(1.0, conf))), "llm"
            except (BackendError, ValueError, KeyError, TypeError):
                continue
        # Safe default: a general reasoning model can handle anything, just not optimally.
        return dict(base, task_type="analysis", complexity="medium", confidence=0.0), "fallback"
