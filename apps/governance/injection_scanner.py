"""Prompt-injection tripwire for untrusted document text.

Heuristic only: flags instruction-like content so it can be quarantined and
shown to a human. It is one layer; the structural controls (fixed plan,
no tool calls from content, network-less sandbox) are the real defenses.
"""

import re
from dataclasses import dataclass
from typing import List

_TOOLS = r"(?:run_python|retrieve_sops|ocr_extract|draft_note|approve_note|export_docx)"

_RULES = [
    ("override_instructions", re.compile(
        r"\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b"
        r"[^.\n]{0,30}\b(?:rules?|instructions?|prompts?|guidelines?)\b", re.I)),
    ("addresses_ai", re.compile(
        r"\b(?:system|assistant|ai)\s+(?:instruction|prompt|directive)s?\b"
        r"|\bto\s+(?:the\s+)?ai\s+assistant\b", re.I)),
    ("concealment", re.compile(
        r"\b(?:do\s+not|don'?t|never)\s+(?:tell|inform|notify|alert)\s+the\s+user\b", re.I)),
    ("tool_invocation", re.compile(
        r"\buse\s+the\s+" + _TOOLS + r"\s+tool\b|\b" + _TOOLS + r"\b", re.I)),
    ("exfiltration", re.compile(
        r"\b(?:send|upload|post|forward|transmit)\b[^.\n]{0,80}\bhttps?://", re.I)),
]


@dataclass
class Finding:
    rule: str
    excerpt: str


def scan(text: str) -> List[Finding]:
    findings = []
    for name, pattern in _RULES:
        m = pattern.search(text or "")
        if m:
            findings.append(Finding(rule=name, excerpt=m.group(0)[:120]))
    return findings


def is_suspicious(text: str, min_rules: int = 2) -> bool:
    """Quarantine when two or more independent rules fire (limits false positives)."""
    return len(scan(text)) >= min_rules
