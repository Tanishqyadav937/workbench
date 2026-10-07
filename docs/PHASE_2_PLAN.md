# Phase 2 plan (inferred from the tasks.md line-129 checkpoint)

Flow: upload scan -> extract fields (qwen2.5vl:7b) -> HUMAN confirms flagged fields -> retrieve SOPs (mxbai-embed-large)
-> draft note (qwen3:8b) -> HUMAN approval checkpoint -> .docx with citations. Audit ledger records every step.
Whole flow runs air-gapped; exfiltration test must fail safely with 0 egress.

| # | Step | Verify |
|---|------|--------|
| P2.1 | Upload + storage of scans | file stored, ledger entry written |
| P2.2 | Field extraction with per-field confidence | phase2/extraction.py flags fields below threshold; gold set in tests/data |
| P2.3 | Human confirmation gate | draft cannot start while any flagged field is unconfirmed |
| P2.4 | SOP ingestion + retrieval | top-k contains the gold SOP for each question in retrieval_gold.json |
| P2.5 | Drafting with citations | every claim in the note maps to a retrieved passage id |
| P2.6 | Approval checkpoint + .docx export | export refused before approval; docx lists citations |
| P2.7 | Exfiltration test | run INSIDE the isolated network; expect 0 egress |

Open question: confirm numbering, thresholds and acceptance criteria against tasks.md.
