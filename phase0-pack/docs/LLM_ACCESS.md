# How containers reach Ollama (Task 10 decision)

## The problem
Ollama runs on the **Mac host** (it needs Metal; Docker on Mac cannot use the Apple GPU).
The workbench containers sit on `workbench_net`, which is `internal: true`, so they cannot reach the host.

## Options considered
| Option | Verdict |
|---|---|
| Run Ollama in a container on `workbench_net` | Cleanest isolation, but CPU-only on a Mac (very slow). **Use this on the Linux/NVIDIA demo box.** |
| Put the model gateway on a normal network | Works, but the gateway then has internet access, which weakens the sovereignty claim |
| **One narrow bridge container (`llm-bridge`)** | **Chosen for the Mac dev machine.** Only this container touches the host; every other service stays internal |

## What was set up
`docker-compose.override.yml` adds `llm-bridge` (a `socat` forwarder). It listens on `llm-bridge:11434`
on the internal network and forwards to `host.docker.internal:11434`, where Ollama runs.
Services call `http://llm-bridge:11434` (this is the `endpoint` in `config/model_registry/*.yaml`).

## Verify (Mac)
```bash
ollama serve            # skip if the Ollama app is already running
docker compose up -d
docker compose ps -a                      # llm-bridge must be "Up"

# 1) an internal container can reach Ollama THROUGH the bridge
docker compose exec -T api python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://llm-bridge:11434/api/tags', timeout=5))['models'][0]['name'])"

# 2) the same container STILL cannot reach the internet
docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=3); print('REACHED INTERNET')"
```
Expected: (1) prints a model name, (2) raises a name-resolution error and never prints `REACHED INTERNET`.

If (1) times out: Ollama may not be reachable from Docker. Quit Ollama, then run
`OLLAMA_HOST=0.0.0.0 ollama serve` and retry. Be aware that this makes Ollama listen on your LAN;
turn it back off after the demo and stay off public Wi-Fi while it is on.

## Honest limits (say this if a judge asks)
* `llm-bridge` is on a non-internal network, so in principle it has a route out. It only runs `socat` with a single fixed
  destination and no application code, but on the Mac this is **not enforced by a firewall**.
* Strict enforcement belongs on the Linux demo box: Ollama/vLLM as a container on `workbench_net`, no bridge at all,
  `nftables` default-DROP and the egress monitor (tasks A1-A3). Then the zero-egress claim is enforced and measured.
* Mac = development machine. Linux box = proof machine.

## Memory on a 16 GB Mac
Set `export OLLAMA_MAX_LOADED_MODELS=1` before starting Ollama and give Docker Desktop about 4 GB.
Switching between the chat, coder and vision model reloads weights (roughly 10-20 s). That is the "LRU model swapping"
behaviour; warm each model once before the demo so the first call is not slow.
