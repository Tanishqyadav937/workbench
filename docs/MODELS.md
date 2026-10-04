# Models actually installed (replaces the larger models in the early design)

| Role | Model | Size | Registry file |
|---|---|---|---|
| Router classifier, planner, drafting, Q&A | `qwen3:8b` | 5.2 GB | `config/model_registry/qwen3-8b.yaml` |
| Code generation for the sandbox | `qwen2.5-coder:7b` | 4.7 GB | `config/model_registry/qwen2.5-coder-7b.yaml` |
| Scanned documents, handwriting, diagrams | `qwen2.5vl:7b` | 6.0 GB | `config/model_registry/qwen2.5vl-7b.yaml` |
| Embeddings for retrieval | `mxbai-embed-large` | 0.7 GB | `config/model_registry/mxbai-embed-large.yaml` |

`my-assistant` in `ollama list` is a personal model and is NOT part of this project. Never add a `-cloud` model: it sends prompts to a remote server.

## Differences from the original design documents
| Original | Now | Why |
|---|---|---|
| Qwen3-32B / GPT-OSS-120B | `qwen3:8b` | 16 GB Mac. The 32B class needs a 24-48 GB GPU |
| Qwen2.5-Coder-32B | `qwen2.5-coder:7b` | Same reason |
| BGE-M3 | `mxbai-embed-large` | Already installed, strong English retrieval; demo data is English |
| vLLM | Ollama | Metal on Mac. vLLM is still the production backend (adapter is pluggable) |

## Gotchas
* `qwen3:8b` prints a long "thinking" block by default. Send `"think": false` for the router, planner and any JSON output.
* Only one model is kept loaded (`OLLAMA_MAX_LOADED_MODELS=1`); expect a short pause when the model type changes.
* Smaller models hallucinate more: keep citations, "not found" behaviour and human checkpoints in the demo.
