# Phase 1 pack: ledger + model gateway

## What is in this pack, mapped to tasks.md

| Task | What | Status |
|---|---|---|
| A4 | Ledger service (hash chain, append-only, HTTP API) | Built, 25 tests pass |
| A5 | `scripts/verify_ledger.py` (finds the first broken entry; anchors detect deleted tails) | Built, tested |
| A7 | `libs/ledger_client.py` (one-line logging, strict or spool mode) | Built, tested |
| B1 | Backend adapter + Ollama implementation (`gateway/backends.py`) | Built, tested against a fake Ollama server |
| B2 | YAML registry loader (`gateway/registry.py`) | Built, tested |
| B3 | Intent classifier (rules for scans, `qwen3:8b` with JSON-constrained output otherwise) | Built, tested with fakes |
| B4 | Selector: rules, health, sensitivity gate, fallback chain | Built, tested |
| B5 | 20 labelled prompts + `scripts/eval_router.py` | Script ready; **real accuracy not measured yet** |
| B6 | Gateway service: `/v1/route`, `/v1/generate`, `/v1/models`, logs to the ledger | Built, tested |
| A1-A3, A6 | Firewall, egress monitor, Grafana, Keycloak | Not started |
| C1-C6 | Qdrant, parsers, embeddings, RBAC search, UI skeleton | Not started |

## What was and was NOT tested
* Tested (79 automated tests, Python 3.12): all logic above, including a gateway-to-ledger test over real HTTP,
  tamper detection (modify, delete, reorder, whole-chain rewrite, truncation) and refusal to act when the ledger is down.
* NOT tested: the Dockerfiles, the compose changes, and anything involving real Ollama models. Those run for the first time on your Mac
  via `scripts/check_phase1.sh`.

## Decisions to know about
1. **Ledger storage is SQLite, not Postgres** (design.md 4.1 said Postgres). Own file and volume, separate from app data;
   triggers make UPDATE and DELETE fail; fully testable offline. The store class can be swapped for an INSERT-only Postgres role later.
2. **Fail closed.** If the ledger is configured but unreachable, the gateway refuses the request (HTTP 503). No audit record, no action.
3. **Ledger entries hold hashes and ids, never prompt or response text.** A test enforces this.
4. **A hash chain alone cannot detect deletion of the newest entries**, and an attacker with write access could rewrite the whole chain.
   Save the head as an anchor somewhere else (`--save-anchor`) and verify with `--anchor-file`; the tests prove the anchor catches both.
5. `min_clearance_for_use` in the model YAMLs was ambiguous, so it is now `max_sensitivity` (highest data sensitivity the model may process).
6. Scans and images are routed by rules with no model call, so the classifier model is not loaded just to route a scan (saves a swap on 16 GB).

## Run it (from the project root, Docker Desktop + Ollama running)

```bash
# 1. unzip over the project (merges; does not touch docker-compose.yml, llm-bridge-nginx.conf or schemas/)
unzip -o ~/Downloads/phase1-pack.zip -d .

# 2. automated tests in a clean Python 3.12 container (expect: 79 passed)
bash scripts/run_tests.sh

# 3. build and start the two new services, then check everything end to end with the REAL models
docker compose up -d --build ledger model-gateway
bash scripts/check_phase1.sh

# 4. measure real routing accuracy (task B5; needs only qwen3:8b; target >= 90%)
python3 -m venv .venv 2>/dev/null; source .venv/bin/activate; pip install -q pyyaml
python3 scripts/eval_router.py --verbose
```

## Demo: tamper detection (demo step 5)

```bash
bash scripts/copy_ledger.sh /tmp/ledger_copy.db
python3 scripts/verify_ledger.py --db /tmp/ledger_copy.db --save-anchor /tmp/anchor.txt     # OK, saves the head
sqlite3 /tmp/ledger_copy.db "DROP TRIGGER ledger_no_update; UPDATE ledger SET payload='{}' WHERE seq=2;"
python3 scripts/verify_ledger.py --db /tmp/ledger_copy.db                                    # FAIL at seq 2
bash scripts/copy_ledger.sh /tmp/ledger_copy2.db
sqlite3 /tmp/ledger_copy2.db "DROP TRIGGER ledger_no_delete; DELETE FROM ledger WHERE seq > 2;"
python3 scripts/verify_ledger.py --db /tmp/ledger_copy2.db                                   # OK: deleted tail is invisible alone
python3 scripts/verify_ledger.py --db /tmp/ledger_copy2.db --anchor-file /tmp/anchor.txt     # FAIL: chain truncated
```
Always tamper with a COPY. The live database refuses UPDATE and DELETE.

## Using the gateway from other services
Inside the Docker network: `http://model-gateway:8200` and `http://ledger:8100` (no ports are published to the Mac).
```python
import json, urllib.request
body = json.dumps({"prompt": "Summarise IR-2026-011", "user": "u-42", "task_id": "T-001", "node_id": "n1",
                   "sensitivity": "confidential"}).encode()
req = urllib.request.Request("http://model-gateway:8200/v1/generate", data=body, headers={"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=240)))   # {"decision": {...}, "text": "...", "usage": {...}}
```
Log your own events: `from ledger_client import LedgerClient, hash_obj` then
`LedgerClient().log("tool.call", "agent", "orchestrator", {"tool": "run_python", "args_hash": hash_obj(args)})`.

## Known limits (say so if asked)
* The ledger API has no per-caller authentication on the internal network (optional shared token only) and no TLS between services.
* Anyone with root on the host can still rewrite the database; the anchor is what exposes it, so keep it somewhere else.
* One model is loaded at a time on the 16 GB Mac, so switching between chat, coder and vision models adds roughly 10-20 s.
* `gateway/main.py` exposes no streaming endpoint yet (comes with the orchestrator WebSocket, task B11).
