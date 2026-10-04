#!/usr/bin/env python3
"""Update design.md / PRD.md to the models you actually installed (Task 9).
Makes a .bak copy of each file first and is safe to run twice.
Run from the project root:  python3 scripts/patch_docs.py
"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["design.md", "PRD.md"]
MARKER = "<!-- implementation-note -->"
NOTE = (
    MARKER + "\n"
    "> **Implementation note (Mac dev machine, 16 GB):** the demo runs `qwen3:8b`, `qwen2.5-coder:7b`, `qwen2.5vl:7b` and "
    "`mxbai-embed-large` on Ollama. Larger models (32B / 120B) and vLLM remain the production target; the GPU budget "
    "example (design.md section 9.3) describes that target, not the demo laptop. See `docs/MODELS.md` and `docs/LLM_ACCESS.md`.\n"
)
# order matters: longer / more specific strings first
REPLACEMENTS = [
    ("qwen3-32b-instruct", "qwen3:8b"),
    ("qwen3-32b-awq", "qwen3-8b"),
    ("qwen3-32b.sig", "qwen3-8b.sig"),
    ("qwen3-32b / gpt-oss", "qwen3:8b"),
    ("qwen3-8b-instruct", "qwen3:8b"),
    ("qwen2.5-coder-32b", "qwen2.5-coder:7b"),
    ("qwen2.5-vl-7b", "qwen2.5vl:7b"),
    ("Qwen3-32B AWQ", "Qwen3-8B"),
    ("Qwen3-32B", "Qwen3-8B"),
    ("Qwen2.5-Coder-32B", "Qwen2.5-Coder-7B"),
    ("BGE-M3", "mxbai-embed-large"),
    ("[gpt-oss-120b, qwen3:8b]", "[qwen3:8b]"),
]

for name in FILES:
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        print("skip (not found):", name)
        continue
    text = open(path, encoding="utf-8").read()
    if MARKER in text:
        print("already patched:", name)
        continue
    shutil.copyfile(path, path + ".bak")
    count = 0
    for old, new in REPLACEMENTS:
        count += text.count(old)
        text = text.replace(old, new)
    lines = text.split("\n")
    # insert the note after the first heading line
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            lines.insert(i + 1, "\n" + NOTE)
            break
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    print("patched %-10s %d replacements (backup: %s.bak)" % (name, count, name))
