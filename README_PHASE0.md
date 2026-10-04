# Phase 0 pack: what is in here

| Task | Files |
|---|---|
| 0.5 Schemas | `schemas/*.schema.json` (5), `schemas/examples/*` (10), `scripts/validate_schemas.py` |
| 0.6 Demo data | `demo_data/` (19 files), `demo_data/INDEX.csv`, `demo_data/users.json`, generator `scripts/make_demo_data.py` |
| 0.7 Domain + script | `DEMO_DOMAIN.md`, `DEMO_SCRIPT.md` |
| 9 Models | `config/model_registry/*.yaml`, `config/router_rules.yaml`, `docs/MODELS.md`, `scripts/patch_docs.py` |
| 10 Ollama access | `docker-compose.override.yml` (llm-bridge), `docs/LLM_ACCESS.md` |
| Check | `scripts/check_phase0.sh` |

Check everything at any time:  `bash scripts/check_phase0.sh`
