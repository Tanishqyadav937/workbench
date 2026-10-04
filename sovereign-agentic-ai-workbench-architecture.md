# Sovereign On-Premise Agentic AI Workbench
### Advanced Architecture Document
**Problem Statement ID:** 26117 — MRPL (Mangalore Refinery and Petrochemicals Limited)
**Theme:** Smart Automation | **Category:** Software

---

## 1. Design Philosophy

Three non-negotiables drive every architectural decision:

1. **Zero egress, provably.** Not "no internet access configured" but "physically and cryptographically incapable of external calls," with continuous, auditable proof.
2. **Model-agnostic by construction.** The system never hard-codes a model. Every model is a plug-in behind a capability contract; adding GPT-OSS-120B, Llama-4, Qwen3, DeepSeek-V3-local, or a future release is a config change, not a redesign.
3. **Agent-native, not chatbot-with-tools.** Multi-step planning, tool execution, self-critique, and human checkpoints are first-class primitives, not an afterthought bolted onto a chat loop.

---

## 2. High-Level System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  AIR-GAPPED SECURITY PERIMETER (physical + network isolation)             │
│                                                                            │
│  ┌────────────┐    ┌─────────────────────────────────────────────────┐   │
│  │  Client    │◄──►│              API / Orchestration Layer           │   │
│  │  (Web UI,  │    │  FastAPI + WebSocket (streaming) + Auth (LDAP/   │   │
│  │  Desktop)  │    │  Keycloak, on-prem SSO, RBAC)                    │   │
│  └────────────┘    └───────────────────┬─────────────────────────────┘   │
│                                         │                                  │
│                     ┌───────────────────▼──────────────────────┐          │
│                     │        AGENT ORCHESTRATION CORE           │          │
│                     │  - Task Planner / Reasoner                │          │
│                     │  - Model Router (capability-based)        │          │
│                     │  - Tool Dispatcher                        │          │
│                     │  - Memory & State Manager (per-session)   │          │
│                     │  - Critic / Verifier loop                 │          │
│                     │  - Human-in-the-loop checkpoint gate       │          │
│                     └───┬──────────┬──────────┬──────────┬─────┘          │
│                         │          │          │          │                │
│        ┌────────────────┘   ┌──────┘   ┌──────┘    ┌─────┘                │
│        ▼                    ▼          ▼           ▼                     │
│  ┌───────────┐      ┌──────────────┐ ┌──────────┐ ┌────────────────┐     │
│  │  MODEL     │      │  TOOL         │ │  RAG /   │ │  MULTIMODAL     │   │
│  │  SERVING   │      │  EXECUTION    │ │  KB      │ │  PIPELINE       │   │
│  │  LAYER     │      │  SANDBOX      │ │  ENGINE  │ │  (OCR/Vision)   │   │
│  └───────────┘      └──────────────┘ └──────────┘ └────────────────┘     │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │      OBSERVABILITY & SOVEREIGNTY-PROOF LAYER (always-on)          │    │
│  │  Network egress monitor · audit log · model/tool call ledger      │    │
│  │  · tamper-evident hash chain · Grafana dashboard                  │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│  Physical network: no default route to WAN. Egress firewall DROPs all    │
│  non-allowlisted traffic. NIC can be physically disconnected for demo.   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Model Serving Layer — Multi-Model, Pluggable Backend

### 3.1 Serving Infrastructure

- **Inference engine:** vLLM (or TGI) as the serving runtime — supports continuous batching, PagedAttention, tensor/pipeline parallelism, and multi-LoRA serving so several fine-tuned adapters can share one base model's memory footprint.
- **Model registry:** a declarative YAML manifest per model — no code changes to add a model.

```yaml
# model_registry/qwen3-32b-reasoning.yaml
model_id: qwen3-32b-instruct
family: qwen3
weights_path: /models/qwen3-32b-awq
quantization: awq-int4          # fits mid-range GPU (24-48GB)
capabilities:
  - long_context_reasoning
  - document_summarization
  - approval_note_drafting
context_window: 128000
vram_footprint_gb: 22
served_via: vllm
priority: 2

# model_registry/qwen2.5-coder-32b.yaml
model_id: qwen2.5-coder-32b
family: qwen-coder
capabilities: [code_generation, code_execution_agent, debugging]
vram_footprint_gb: 20
served_via: vllm

# model_registry/qwen2.5-vl-7b.yaml
model_id: qwen2.5-vl-7b
family: qwen-vl
capabilities: [ocr, document_vision, drawing_understanding, chart_reading]
vram_footprint_gb: 9
served_via: vllm

# model_registry/gpt-oss-120b.yaml   (venue has full-size GPU)
model_id: gpt-oss-120b
family: gpt-oss
capabilities: [long_context_reasoning, planning, code_generation, general]
quantization: mxfp4
vram_footprint_gb: 80
served_via: vllm
fallback_if_unavailable: qwen3-32b-instruct
```

- **Hot-swap / dynamic loading:** models register with the router at boot via a `/models/manifest` scan. A new GGUF/safetensors folder + manifest file dropped into `/models/` and a registry reload (`POST /admin/models/reload`) makes it available — **no redeploy**.
- **Elastic memory management:** on a single mid-range GPU (e.g., RTX 4090/A6000, 24-48GB), models are served with **swap-on-demand** — an LRU model cache keeps the 2 most-used models resident and pages others in/out using vLLM's sleep/wake API, so the demo works on one GPU even though the design targets multi-GPU/120B-class production hardware.

### 3.2 Model Router — Capability-Based Auto-Selection

This is the core "automatically pick the right model" requirement. Implemented as a **two-stage router**, not a single black-box classifier:

**Stage 1 — Cheap intent classifier** (a small on-prem model, e.g., a fine-tuned 1-3B classifier or even the smallest resident LLM) tags the incoming task with:
- `task_type`: {code, document_qa, summarization, vision/ocr, calculation, drafting, planning, general_chat}
- `modality`: {text, image, pdf_scanned, spreadsheet, mixed}
- `complexity`: {low, medium, high} (heuristics: token length, presence of multi-step verbs, nested sub-questions)
- `sensitivity_tag`: {routine, confidential, board-level} — drives which models/tools are even eligible (some orgs may restrict board-level drafting to the most-audited model)

**Stage 2 — Capability-matched routing table**

| task_type | modality | Selected model class | Why |
|---|---|---|---|
| code_generation, debugging | text | `qwen-coder` family | Code-specialized weights outperform generalists on syntax/logic |
| ocr, drawing_understanding | image/scanned_pdf | `qwen-vl` / vision-language model | Only VLMs can ground on pixels |
| long document summarization, approval notes | text, long context | `qwen3-32b` or `gpt-oss-120b` if resident | Needs large context window + instruction following |
| multi-step planning / agent orchestration | any | the largest resident reasoning model | Planning benefits from stronger chain-of-thought |
| spreadsheet/calculation | text + code | `qwen-coder` (writes pandas/openpyxl code, executes in sandbox) | Calculation reliability comes from code execution, not LLM arithmetic |
| simple lookups / chit-chat | text | smallest resident model | Cost/latency optimization — don't wake the 120B for "what's today's date" |

- **Router is itself swappable/extensible:** new task_type → model mappings are added as rows in a routing config (`router_rules.yaml`), and the intent classifier can be retrained without touching orchestration code.
- **Fallback chain:** if the preferred model is OOM/unavailable, router walks a declared fallback list (see `fallback_if_unavailable` in the manifest) — guarantees the demo never hard-fails on a single GPU.
- **Ensemble/critic mode (advanced):** for high-stakes outputs (approval notes, safety-relevant calculations), the router can optionally run a **second model as verifier** — e.g., coder model's output is checked by the reasoning model before being shown to the user ("LLM-as-judge" self-consistency pass). Configurable per task_type/sensitivity_tag.

---

## 4. Agent Orchestration Core

### 4.1 Planning Loop (ReAct + Plan-and-Execute hybrid)

```
User Task
   │
   ▼
[Planner] → decomposes into ordered sub-tasks (DAG, not linear list,
             so independent sub-tasks can run in parallel)
   │
   ▼
For each sub-task node:
   [Router] picks model  →  [Executor] calls model/tool  →  [Observer]
   captures result  →  [Critic] checks against sub-task's success
   criteria  →  retry (bounded, max 3) or mark complete
   │
   ▼
[Checkpoint Gate] — if sub-task is flagged `requires_human_review`
   (e.g., before an approval note is finalized, before code executes
   against a real file), pause and surface to user for approve/edit/reject
   │
   ▼
[Aggregator] composes final deliverable from completed sub-task outputs
   │
   ▼
[Output Formatter] renders as .docx / .pptx / .xlsx / code diff / report
```

- **State object** is an explicit, inspectable JSON task graph (not hidden in a prompt) — every sub-task node has `status`, `model_used`, `tool_calls[]`, `output`, `verification_result`. This is what gets shown in the UI as a live "agent trace" panel, and what gets hashed into the audit ledger.
- **Bounded autonomy:** max sub-task depth, max retries, max tool calls per session, and a hard wall-clock timeout are all configurable — prevents runaway agent loops, important for an industrial deployment where compute is shared.
- **Long-horizon memory:** session state persists to a local vector+relational store so a multi-hour task (e.g., "review these 40 scanned inspection reports") can be paused and resumed without re-planning from scratch.

### 4.2 Tool Execution Sandbox

| Tool | Isolation mechanism | Notes |
|---|---|---|
| Code execution | gVisor or Firecracker microVM, per-session ephemeral container, **no network namespace attached** | Python/pandas/numpy pre-installed; filesystem access limited to a scoped workspace dir |
| File read/write | Broker service enforces path allowlist + user's RBAC scope | Agent never gets raw filesystem access — every read/write goes through an audited broker API |
| Spreadsheet ops | openpyxl/pandas inside the same sandbox, with formula-evaluation via LibreOffice headless for verification | Calculation steps are logged, not just the final number — auditability for engineering calcs |
| Internal document search | Read-only connector to the RAG index (Section 5) | No write access, no external URLs resolvable |
| OCR/vision inference | Dedicated internal microservice call | Same air-gap perimeter |

- **Tool contract:** every tool is registered via an OpenAI-compatible function-calling schema, so tools are model-agnostic too — swapping the underlying LLM doesn't require rewriting tool definitions.
- **Egress interceptor inside the sandbox:** even though the network namespace is stripped, an additional syscall-level seccomp filter blocks `connect()` to non-loopback addresses as defense-in-depth (belt-and-braces against a compromised or malicious tool call).

---

## 5. Retrieval-Augmented Grounding — Local Knowledge Base

```
Internal Docs (SOPs, manuals, past correspondence, P&IDs, inspection
reports, board minutes)
        │
        ▼
 [Ingestion Pipeline]
   - Format-aware parsers: PDF (text + scanned), DOCX, XLSX, email
     archives (PST/EML), CAD drawing exports
   - Scanned/handwritten → OCR pipeline (Section 6) before chunking
   - Table-aware chunking (don't split mid-table); drawing callouts
     kept with their reference text
        │
        ▼
 [Embedding Model] — on-prem, e.g., BGE-M3 or Nomic-Embed (open-weight,
   multilingual, handles mixed English/Hindi/Kannada correspondence)
        │
        ▼
 [Vector Store] — Qdrant or Milvus, self-hosted, encrypted at rest
        │
        ▼
 [Hybrid Retriever] — dense (embedding) + sparse (BM25) + metadata
   filters (document type, department, date range, security
   classification) → re-ranked with a local cross-encoder
        │
        ▼
 [Grounded Generation] — retrieved chunks injected into the routed
   model's context with mandatory citation formatting; model is
   prompt-constrained to answer "not found in knowledge base" rather
   than hallucinate when retrieval confidence is low
```

- **Access-scoped retrieval:** the retriever filters by the requesting user's RBAC clearance *before* chunks ever reach the LLM context — a junior engineer's query never surfaces board-level financial documents even if semantically similar.
- **Incremental re-indexing:** a filesystem watcher re-embeds new/changed documents on a schedule, so the KB stays current without full re-ingestion.
- **Freshness/provenance metadata:** every retrieved chunk carries source document, page/section, and last-modified date, surfaced in the UI next to any claim the agent makes — critical for engineering sign-off trust.

---

## 6. Multimodal Pipeline (Vision, OCR, Scanned/Handwritten Documents)

```
Input (scanned PDF / photo / handwritten note / P&ID image)
   │
   ▼
[Pre-processing] deskew, denoise, contrast-normalize (OpenCV)
   │
   ▼
   ├── Printed text ──► [On-device OCR: PaddleOCR / Tesseract-LSTM] ──► text + bounding boxes
   │
   ├── Handwritten ───► [Handwriting-tuned OCR/VLM: Qwen2.5-VL or
   │                      TrOCR fine-tuned] ──► text + confidence score
   │                      (low-confidence spans flagged for human review,
   │                      never silently guessed)
   │
   └── Diagrams/P&IDs ─► [Vision-Language Model, e.g., Qwen2.5-VL-7B/32B]
                          ── structured extraction: equipment tags,
                          line numbers, valve states, annotations,
                          cross-referenced against symbol legend
   │
   ▼
[Structured Output] — JSON schema: {extracted_text, entities, tags,
   confidence_per_field, source_bbox} — never free-text-only, so
   downstream agent steps can programmatically verify/cross-check
   │
   ▼
Feeds into: RAG ingestion, agent task context, or direct deliverable
   (e.g., "key findings" table in an approval note)
```

- **Why a VLM and not OCR-only for drawings:** P&IDs need *spatial and symbolic* understanding (this valve connects to that line, this callout modifies that equipment), which a vision-language model captures far better than flat OCR text.
- **Confidence-gated automation:** any extracted field below a configurable confidence threshold is highlighted in the UI for mandatory human confirmation before it flows into a generated deliverable — this is the safety rail that makes the system usable for approval-note-grade work.

---

## 7. Deliverable Generation Layer

Agent outputs are never "just chat text" — they're routed through format-specific generators:

| Deliverable | Generation approach |
|---|---|
| Approval note / report (.docx) | Agent fills a structured template (python-docx) — org letterhead, findings table, recommendation section, auto-inserted citations from RAG |
| Board presentation (.pptx) | Agent produces an outline → slide content JSON → rendered via python-pptx with the org's approved template/theme |
| Calculations (.xlsx) | Agent writes the actual formulas (not just final numbers) into cells via openpyxl, so an engineer can audit/re-run them |
| Code | Delivered as a diff/PR-style patch + sandbox execution log (stdout, test results) — never presented as "trust me it works" |
| Inspection summary | Structured Word doc combining OCR-extracted findings + VLM drawing analysis + RAG-grounded SOP references |

---

## 8. Sovereignty Proof Layer — "Show, Don't Tell"

This directly answers the expected-solution requirement: *prove* no external calls happen.

1. **Network-level:** the entire stack runs in a Docker/K8s namespace with **no default route to any WAN-facing interface**; egress firewall (iptables/nftables) is set to `DROP` for anything not in an explicit allowlist (which, in the air-gapped deployment, is empty).
2. **Real-time egress monitor:** a lightweight `tcpdump`/`nfacct`-based sidecar streams all attempted outbound connections to a Grafana dashboard, live during the demo — showing zero external DNS lookups, zero external TCP handshakes, for the entire session.
3. **Tamper-evident audit ledger:** every model call, tool call, and file access is appended to a local append-only log with a hash chain (each entry includes the hash of the previous entry) — so the audit trail itself is verifiable, not just trusted.
4. **Physical demonstration option:** for the venue proof, the workstation's WAN NIC can be physically unplugged or the switch port disabled — the system continues functioning identically, which is the most convincing possible proof.
5. **Model weight provenance:** SHA-256 checksums of all model weight files are verified against a signed manifest at boot, preventing silent substitution of a compromised model.

---

## 9. Security, Governance & Compliance Layer (industrial/PSU-grade)

- **RBAC + classification tagging:** every document and every generated deliverable inherits a classification (Public/Internal/Confidential/Board-only); the agent cannot retrieve or output above the requesting user's clearance.
- **Human-in-the-loop gates:** configurable per task type — e.g., any deliverable tagged "approval note" or "board presentation" requires explicit human sign-off before export; code execution against real files requires confirmation.
- **PII/sensitive-data redaction pass:** an optional local NER-based scrubber can redact personal data before documents enter the RAG index, for HR/vendor-negotiation content.
- **Full reproducibility:** every generated deliverable is linked back to the exact model version, prompt, retrieved chunks, and tool calls that produced it — enabling post-hoc technical audit, important for defence-linked and PSU compliance requirements.
- **Air-gapped update mechanism:** new models/patches arrive via a one-way data diode or manually vetted USB transfer with checksum verification — never a live internet pull.

---

## 10. Tech Stack Summary

| Layer | Technology |
|---|---|
| Model serving | vLLM / TGI, multi-LoRA, AWQ/GPTQ/MXFP4 quantization |
| Open-weight models | Qwen3 / Qwen2.5-Coder / Qwen2.5-VL, GPT-OSS-120B (venue-dependent), Llama-4 (pluggable) |
| Orchestration | Custom agent core (LangGraph-style DAG executor) or LangGraph/AutoGen as base, extended for capability-routing |
| Tool sandbox | gVisor / Firecracker microVMs, seccomp |
| Vector DB | Qdrant / Milvus (self-hosted) |
| Embeddings | BGE-M3 / Nomic-Embed (open-weight, on-prem) |
| OCR | PaddleOCR / Tesseract-LSTM |
| Vision-language | Qwen2.5-VL family |
| Deliverable generation | python-docx, python-pptx, openpyxl, LibreOffice headless |
| API/backend | FastAPI, WebSockets |
| Auth | Keycloak / on-prem LDAP, RBAC |
| Observability | Prometheus + Grafana, custom hash-chained audit ledger |
| Infra | Docker Compose (demo) / Kubernetes (production), NVIDIA GPU Operator |
| Network isolation | iptables/nftables DROP-all egress, network namespace isolation, optional physical NIC disconnect |

---

## 11. Demo Script (maps directly to "Expected Solution")

1. **Model auto-selection across ≥2 task types:** submit a code-generation prompt and a document-summarization prompt in the same session; show the live agent trace panel routing each to a different resident model, with the routing decision (task_type → model) visible in the UI.
2. **End-to-end agentic task:** feed a sample scanned inspection report (public dataset) → OCR/VLM extraction → key-findings identification → drafted approval note exported as .docx, with citations back to extracted source regions.
3. **Coding task in sandbox:** ask the agent to write and run a script (e.g., parse an inspection dataset and compute a summary statistic) — show sandboxed execution, stdout/test results, and the network-namespace isolation of that container.
4. **Multimodal task:** feed a sample P&ID or photographed handwritten note → structured extraction with confidence scores → flagged low-confidence fields for human review.
5. **Sovereignty proof:** live Grafana egress-monitor panel showing zero outbound connections throughout the entire demo, plus (optionally) the WAN NIC physically unplugged.

---

## 12. What Makes This "Advanced" vs. a Baseline Implementation

- **Capability-contract model registry** instead of if/else model selection — genuinely extensible to future open-weight releases without redesign.
- **DAG-based planner with parallel sub-tasks and bounded autonomy**, not a naive single-threaded ReAct loop.
- **Second-model verification/critic pass** for high-stakes deliverables (approval notes, calculations) — a lightweight internal check-and-balance before human review.
- **Confidence-gated multimodal extraction** — structured, auditable output rather than opaque free-text OCR dumps.
- **Access-scoped RAG** enforcing document classification at retrieval time, not just at the UI layer.
- **Tamper-evident, hash-chained audit ledger** as the actual sovereignty proof mechanism, not just a firewall rule and a claim.
- **Elastic single-GPU model swapping** so the same architecture scales from a hackathon demo box to a multi-GPU production cluster without a redesign.
