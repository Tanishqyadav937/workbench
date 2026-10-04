#!/usr/bin/env bash
# One-command Phase 0 check. Run from the project root:  bash scripts/check_phase0.sh
# Prints PASS / FAIL / SKIP per item. It never changes anything.
cd "$(dirname "$0")/.." || exit 1
pass=0; fail=0
ok()   { echo "PASS  $1"; pass=$((pass+1)); }
bad()  { echo "FAIL  $1"; fail=$((fail+1)); }
skip() { echo "SKIP  $1"; }

PY=python3; [ -x .venv/bin/python3 ] && PY=.venv/bin/python3

echo "== Schemas =="
if $PY scripts/validate_schemas.py >/tmp/schemas.out 2>&1; then ok "5 schemas valid, examples behave"; else bad "schemas (see below)"; cat /tmp/schemas.out; fi

echo "== Demo data =="
n=$(find demo_data -type f 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -ge 19 ] && ok "demo_data has $n files" || bad "demo_data has only $n files (expected 19)"
[ -f demo_data/users.json ] && ok "users.json present" || bad "users.json missing"
[ -f demo_data/INDEX.csv ] && ok "INDEX.csv present" || bad "INDEX.csv missing"
if command -v pdftotext >/dev/null; then
  pdftotext demo_data/security_tests/vendor_memo_gasket_supply_INJECTION.pdf - 2>/dev/null | grep -qi "ignore all previous" && ok "injection document contains the hidden instruction" || bad "injection document missing its hidden instruction"
else
  skip "injection check (needs pdftotext: brew install poppler)"
fi
[ -f DEMO_DOMAIN.md ] && [ -f DEMO_SCRIPT.md ] && ok "DEMO_DOMAIN.md and DEMO_SCRIPT.md present" || bad "demo docs missing"

echo "== Models (Ollama) =="
if command -v ollama >/dev/null && ollama list >/tmp/ol.out 2>&1; then
  for m in qwen3:8b qwen2.5-coder:7b qwen2.5vl:7b mxbai-embed-large; do
    grep -q "^$m" /tmp/ol.out && ok "model $m installed" || bad "model $m missing (ollama pull $m)"
  done
  grep -q -- "-cloud" /tmp/ol.out && bad "a -cloud model is installed: it sends prompts off-device (ollama rm <name>)" || ok "no cloud models"
else
  skip "ollama not running"
fi

echo "== Docker =="
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  docker compose config --quiet 2>/dev/null && ok "compose files valid" || bad "compose invalid"
  if docker compose ps --status running 2>/dev/null | grep -q api; then
    out=$(docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=3); print('REACHED INTERNET')" 2>&1)
    echo "$out" | grep -q "REACHED INTERNET" && bad "api container REACHED the internet" || { echo "$out" | grep -qiE "resolution|unreachable|refused|timed out|URLError" && ok "api container cannot reach the internet" || bad "isolation test inconclusive: $(echo "$out" | tail -1)"; }
    docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://qdrant:6333/healthz', timeout=3)" >/dev/null 2>&1 && ok "internal networking works (api -> qdrant)" || bad "api cannot reach qdrant"
    docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://ollama.internal:11434/api/tags', timeout=5)" >/dev/null 2>&1 && ok "api reaches Ollama through llm-bridge" || bad "llm-bridge not reachable (see docs/LLM_ACCESS.md)"
  else
    skip "stack not running (docker compose up -d)"
  fi
else
  skip "docker not running"
fi

echo; echo "Result: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
