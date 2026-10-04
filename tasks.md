# Team Task Plan (3 Members)

## Sovereign On-Premise Agentic AI Workbench

Companion docs: `PRD.md`, `design.md`. Requirement IDs (FR-x.x) refer to the PRD.

Adjust the time estimates to your hackathon length. Phases are ordered; the "Day" labels assume roughly 5 working days. If you have fewer days, compress the phases and use the **cut line** in Section 7.

---

## 1. Role Split

| Member | Role | Owns | Why this grouping |
|---|---|---|---|
| **Member A** | **Security & Infrastructure Lead** | Network isolation, egress monitor, ledger, sandbox and tool broker, auth, model weight verification, security tests | All Theme 6 "proof" features live together; this is your main differentiator |
| **Member B** | **AI Core Lead** | Model gateway, router, orchestrator (planner, critic, checkpoints), OCR/VLM pipeline | The agent intelligence and model plumbing |
| **Member C** | **Data, Governance & UX Lead** | RAG with RBAC, PII redaction, consent/erasure, deliverable generation, web UI, pitch deck | Everything the user and the judges see and touch |

Everyone also does: write tests for their own module, keep their section of the README up to date, and join the daily sync.

---

## 2. Ground Rules (agree on Day 0)

1. **One repo, one branch per feature**, merge to `main` through short pull requests. Never commit directly to `main` after Day 1.
2. **Contracts first.** Before coding, agree on the JSON shapes and API paths in `design.md` Sections 3 and 5. Each member can then build against mocks.
3. **Mock everything.** Every module ships a fake version (a stub returning canned data) within the first day so the others are never blocked.
4. **Daily 15-minute sync** (what I finished, what blocks me, what I need from you).
5. **Integration checkpoint every day at the same time.** Run the full flow end to end, even if parts are mocked.
6. **Definition of done** for any task: works in Docker Compose, has at least one test, logs to the ledger where required, and is mentioned in the README.

---

## 3. Phase 0: Setup and Contracts (Day 0, all three together, 2-3 hours)

| # | Task | Who |
|---|---|---|
| 0.1 | Create the repo with the layout from `design.md` §3.1 | A |
| 0.2 | Set up the Docker Compose skeleton with an internal network (`internal: true`) and placeholder services | A |
| 0.3 | Confirm hardware: GPU model, VRAM, driver and CUDA; pick Ollama or vLLM for the demo | B |
| 0.4 | Download models on a connected machine: a small chat model (8B), a coder model, a vision-language model, an embedding model (BGE-M3). Verify they run | B |
| 0.5 | Write shared JSON schemas: task graph, router decision, extraction result, ledger entry, chunk payload. Put them in `/schemas` | B + A + C (one each, review together) |
| 0.6 | Choose and prepare **demo data**: 3-5 synthetic inspection reports (one scanned, one with a low-quality scan), 5-10 SOP documents, one document with an embedded prompt injection, one containing fake PII | C |
| 0.7 | Decide the demo domain (MRPL refinery or neutral) and write the 5-step demo script from `PRD.md` §10 | All |

**Exit criteria:** repo exists, everyone can run `docker compose up`, models run locally, schemas are agreed.

---

## 4. Phase 1: Foundations (Days 1-2)

### Member A: Security & Infrastructure

| # | Task | Requirement | Output |
|---|---|---|---|
| A1 | Write `nftables.rules` (default DROP, allow loopback and the internal subnet) and apply to the demo machine | FR-8.1 | Firewall rules file and a short how-to |
| A2 | Build the **egress monitor** sidecar (capture outbound SYN, DNS, ICMP) and export Prometheus metrics | FR-8.2 | Metrics endpoint, counter at 0 |
| A3 | Set up Prometheus and Grafana with panels: egress counter, DNS queries, blocked packets | FR-8.2 | Working dashboard |
| A4 | Build the **ledger service**: Postgres insert-only table, hash chain, `POST /ledger/events` | FR-8.3 | Service plus unit tests |
| A5 | Write `scripts/verify_ledger.py` (recompute the chain and report the first break) | FR-8.3 | Script and a "tamper then detect" test |
| A6 | Set up Keycloak with 3 test users at different clearances, plus roles (engineer, approver, admin, compliance) | RBAC | Realm export file |
| A7 | Provide a small **ledger client library** that other members import to log events in one line | All | `ledger_client.py` |

### Member B: AI Core

| # | Task | Requirement | Output |
|---|---|---|---|
| B1 | Build the **model backend adapter** interface plus the Ollama (or vLLM) implementation | FR-1.1 | `ModelBackend` class with `generate`, `health`, `load`, `unload` |
| B2 | Create the **YAML model registry** and loader; add 3-4 models | FR-1.2 | `config/model_registry/*.yaml` |
| B3 | Build the **intent classifier**: a small model with JSON-constrained output, plus the rules prefilter | FR-1.3 | Function `classify(request) -> classification` |
| B4 | Build the **selector** using `router_rules.yaml`, with health and VRAM checks and the fallback chain | FR-1.3, FR-1.5 | Function `select(classification)`, router decision record |
| B5 | Test the router on 20 labelled prompts (coding, summarising, chat, image) and record accuracy | Success metric | Test file and a result table |
| B6 | Expose the model gateway as a service (`/v1/models`, internal `/generate`) | FR-1.4 | Running service |

### Member C: Data, Governance & UX

| # | Task | Requirement | Output |
|---|---|---|---|
| C1 | Set up Qdrant, MinIO and Postgres (documents table) in Compose | FR-4.3 | Services running |
| C2 | Build **document parsers** (PDF text, DOCX, XLSX) and a table-aware chunker | FR-4.1, FR-4.2 | `ingest()` function |
| C3 | Add the **embedding service** (BGE-M3, local) and upsert chunks with the full payload (classification, purposes, department, consent_status) | FR-4.2 | Chunks visible in Qdrant |
| C4 | Implement **RBAC-filtered search** (filter applied inside the Qdrant query) plus hybrid retrieval and re-ranking | FR-4.4, FR-4.5 | `POST /v1/search` |
| C5 | Return **citations** (doc, page, section) and the "not found" threshold behaviour | FR-4.6 | Search response format |
| C6 | Scaffold the **React UI**: login, layout, status bar, Workspace page with a mock agent trace | UI | Clickable skeleton |

**Phase 1 integration checkpoint (end of Day 2):** a user logs in, uploads a document, searches it with RBAC working (two users see different results), the router picks a model for a sample prompt, and every step appears in the ledger.

---

## 5. Phase 2: Core Features (Days 2-3)

### Member A

| # | Task | Requirement | Output |
|---|---|---|---|
| A8 | Build the **sandbox runner**: ephemeral container, `--network none`, read-only rootfs, resource limits, timeout | FR-3.1 | `run_in_sandbox(code, files)` |
| A9 | Add the **seccomp profile** blocking non-loopback connect, ptrace and mount | FR-3.3 | `seccomp-sandbox.json` |
| A10 | Build the **file broker** (path allowlist, clearance check, logging) | FR-3.2 | Broker API |
| A11 | Build the **tool registry** and tool broker with schema validation and policy checks; register `run_python` | FR-3.5 | `/tools/execute` |
| A12 | Create the **exfiltration test fixture** (socket, HTTP, DNS, write-outside-workspace) and confirm all attempts fail with 0 egress | FR-9.2 | Test script and demo recording |
| A13 | Implement **model weight verification** (signed manifest, SHA-256 at boot) | FR-8.4 | `verify_models.py` and a tamper test |

### Member B

| # | Task | Requirement | Output |
|---|---|---|---|
| B7 | Build the **orchestrator core**: task graph model, state persistence in Postgres, execution loop with limits | FR-2.1 to FR-2.3, FR-2.5 | `/v1/tasks` endpoints |
| B8 | Build the **planner** (JSON-schema output) and the plan validator (no cycles, allowed node types only) | FR-2.1 | Planner module with tests |
| B9 | Build the **critic**: deterministic checks (schema valid, citations present, confidence thresholds) and the retry-with-feedback loop | FR-2.2 | Critic module |
| B10 | Build the **checkpoint gate** (pause, WebSocket event, resume on approve/edit/reject) | FR-2.4 | Gate working end to end |
| B11 | Add the **WebSocket stream** for node, router, token, tool and checkpoint events | Design §5.2 | `/v1/tasks/{id}/stream` |
| B12 | Build the **multimodal pipeline**: preprocessing, PaddleOCR, VLM for handwriting and diagrams, JSON output with confidences | FR-5.1 to FR-5.4 | `/extract` endpoint |
| B13 | Implement **confidence gating**: fields under 0.80 get `needs_review` and block export | FR-5.5 | Flagging works on the bad-scan sample |
| B14 | Connect the orchestrator to A's tool broker and C's search service | Integration | Agent can run code and retrieve |

### Member C

| # | Task | Requirement | Output |
|---|---|---|---|
| C7 | Build the **PII redactor** (NER plus regexes for Aadhaar, PAN, phone, email) with placeholders and a redaction report | FR-7.5 | `redact()` function |
| C8 | Create a **labelled synthetic PII test set** and measure recall (target 90% or more) | Success metric | Test and a results table |
| C9 | Add classification assignment at ingestion and **inheritance** of the highest classification to outputs | FR-7.6 | Metadata flows through |
| C10 | Build the **consent registry** and **usage tracking** (which sessions and purposes touched each document) | FR-7.1 | Tables and `/v1/governance/usage` |
| C11 | Build the **governance UI** page: document list with badges, usage timeline, consent toggles | FR-7.1, FR-7.2 | UI page |
| C12 | Build the **`.docx` generator** (approval note template, findings table, footnote citations) and the provenance sidecar | FR-6.1, FR-6.5 | `generate_docx()` |
| C13 | Build the **Review panel** UI: extracted fields with confidence bars and the page image with bounding-box overlay, approve/edit/reject | UI | Review screen |
| C14 | Connect the UI to the real WebSocket stream and render the live **agent trace** (React Flow) | FR-2.3 | Live trace |

**Phase 2 integration checkpoint (end of Day 3):** upload a scanned inspection report, agent extracts fields (some flagged), human confirms, agent retrieves SOPs, drafts the note, a checkpoint appears, approve, `.docx` downloads with citations. The exfiltration test fails safely with 0 egress.

---

## 6. Phase 3: Privacy, Proof and Polish (Days 4-5)

### Member A

| # | Task | Requirement | Output |
|---|---|---|---|
| A14 | Build the **Sovereignty page** data: embed Grafana panels, "Verify ledger" button calling `/v1/audit/verify`, model verification status | FR-8.2, FR-8.3 | Backend plus embed URLs for C |
| A15 | Write the **prompt-injection test**: documents with embedded instructions, verify no unauthorised tool call | FR-9.3 | Test and demo |
| A16 | Write the **ledger tamper demo** (edit a row on a copy, run verify, show the break) | FR-9.4 | Demo script |
| A17 | Rehearse the **NIC unplug demo** (system keeps working) and write a fallback plan if the venue network is awkward | FR-8.6 | Rehearsed procedure |
| A18 | Write the **threat model** page (use `PRD.md` §8) and make sure every threat has a matching demo or test | FR-9.1 | `THREAT_MODEL.md` |
| A19 | Run the **full test plan** from `design.md` §10 and record pass/fail | Quality | Test report |

### Member B

| # | Task | Requirement | Output |
|---|---|---|---|
| B15 | Tune prompts and the planner so the main demo flow succeeds reliably (run it 10 times, fix failures) | Demo | Stable flow |
| B16 | Add **LRU model swapping** and fallback handling for limited VRAM | FR-1.6 | Works on the demo GPU |
| B17 | Add the **admin reload endpoint** for the model registry | FR-1.8 | `/v1/admin/models/reload` |
| B18 | Prepare a **"drop in a new model" demo** (edit YAML, reload, router uses it) | FR-1.2 | Demo script |
| B19 | Stretch: **verifier pass** for high-stakes outputs | FR-1.7 | Optional |
| B20 | Stretch: `.xlsx` calculation flow (sandbox computes, openpyxl writes formulas) if C is busy | FR-6.2 | Optional |

### Member C

| # | Task | Requirement | Output |
|---|---|---|---|
| C15 | Build the **erasure engine**: revoke consent, delete from Qdrant, BM25, caches, object store, purge sessions, verify 0 hits, write a ledger event with a hashed doc id | FR-7.3 | `/v1/governance/erase` |
| C16 | Implement **purpose-limited retrieval** (declared purpose must be in `allowed_purposes`) | FR-7.4 | Search enforces purpose |
| C17 | Build the **erase UI** with typed confirmation and the resulting erasure report | FR-7.3 | UI flow |
| C18 | Build the **Sovereignty page** in the UI (status bar: Egress, Ledger, Models) using A's endpoints | UI | Page complete |
| C19 | **UI polish**: classification badges everywhere, clickable citations, error states, loading states | UX rules | Polished UI |
| C20 | Build the **pitch deck** (problem, why Theme 6, architecture, demo, threat model, "why not Ollama") | Pitch | Slides |
| C21 | Stretch: **aggregate private query** with minimum group size | FR-7.7 | Optional |

### Final phase tasks (everyone, last day)

| # | Task | Who |
|---|---|---|
| F1 | Freeze features. Only bug fixes after this point | All |
| F2 | **Full demo rehearsal x3** with a timer, following `PRD.md` §10 | All |
| F3 | Assign speakers: A presents the security and sovereignty proof, B presents routing and agent flow, C presents governance and the UI | All |
| F4 | Record a **backup demo video** in case the live demo fails | C |
| F5 | Prepare **offline copies**: models, images, data on USB; confirm the demo works with Wi-Fi off | A |
| F6 | Write answers to likely judge questions (see Section 8) | All |
| F7 | Final README with setup steps, architecture diagram and a quick-start | B |

---

## 7. Cut Line (if time runs short)

Drop things in this order, from the bottom up:

1. Stretch items (B19, B20, C21), then P2 items from the PRD.
2. `.xlsx` and `.pptx` generation. Keep `.docx` only.
3. LRU swapping (B16). Keep a single resident model per type.
4. Purpose limitation and consent withdrawal (C16). Keep the usage view and erasure.
5. Weight verification (A13). Mention it as design only.

**Never cut these (the P0 story):** zero-egress proof, ledger plus verification, sandbox with the exfiltration demo, RBAC retrieval with citations, OCR with confidence flags and human review, PII redaction, `.docx` output, and the threat model.

---

## 8. Likely Judge Questions (prepare answers early)

| Question | Owner | Answer should cover |
|---|---|---|
| How is this different from Ollama plus a chat UI? | B | Ollama is one layer; we add routing, agents, sandbox, governance and proof |
| How do you prove no data leaves? | A | Firewall DROP, live egress monitor at 0, NIC unplug demo |
| What if the AI-generated code is malicious? | A | Network-less sandbox, seccomp, file broker, exfiltration demo |
| What about prompt injection? | A | Policy checks in code, tool allowlists, checkpoints, injection test |
| How do you handle personal data and erasure? | C | PII redaction, purpose limitation, consent withdrawal, erasure with verification |
| What if the model hallucinates? | B | Citations, "not found" behaviour, confidence gating, critic and human sign-off |
| Can it run on modest hardware? | B | Quantised models, LRU swapping, runs on a single GPU |
| How would this scale or deploy for real? | A | Kubernetes with default-deny NetworkPolicy, multi-GPU, offline update path |

---

## 9. Daily Checklist Summary

| Day | Goal | Integration checkpoint |
|---|---|---|
| 0 | Repo, Compose, models running, schemas agreed, demo data chosen | Everyone can run `docker compose up` |
| 1 | Firewall, monitor, ledger, model adapter, parsers, Qdrant, UI skeleton | Services up, ledger receiving events |
| 2 | Router, RBAC search with citations, Keycloak users | Two users see different results; router picks the right model |
| 3 | Orchestrator, sandbox, OCR, PII, docx, live agent trace | End-to-end inspection-to-note flow works |
| 4 | Erasure, purpose limits, injection and tamper tests, Sovereignty page | All P0 demos work individually |
| 5 | Polish, rehearsals, backup video, pitch deck | Three clean full-demo rehearsals |

---

## 10. Dependencies Between Members (who waits for whom)

| Needs | From | By when | Fallback while waiting |
|---|---|---|---|
| Ledger client library | A to B and C | Day 1 | Log to a local file, swap later |
| Keycloak users and tokens | A to C | Day 1-2 | Hardcoded test tokens |
| Model gateway `/generate` | B to C | Day 2 | Canned model responses |
| Search API with citations | C to B | Day 2 | Mock search returning fixed chunks |
| Tool broker `/tools/execute` | A to B | Day 3 | Mock tool returning sample output |
| Extraction endpoint | B to C | Day 3 | Static JSON sample for UI work |
| WebSocket event stream | B to C | Day 3 | Replay recorded events |
| Grafana embed URLs, verify endpoint | A to C | Day 4 | Screenshot placeholders |

When anyone is blocked for more than 30 minutes, raise it in chat instead of waiting for the daily sync.
