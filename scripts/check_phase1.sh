#!/usr/bin/env bash
# Phase 1 check for the ledger + model gateway. Run from the project root:  bash scripts/check_phase1.sh
# Needs Docker Desktop and the Ollama app running. Prints PASS / FAIL per item.
cd "$(dirname "$0")/.." || exit 1
pass=0; fail=0
ok()  { echo "PASS  $1"; pass=$((pass+1)); }
bad() { echo "FAIL  $1"; fail=$((fail+1)); }

# run a tiny python program inside the 'api' container (it sits on the internal network)
inapi() { docker compose exec -T api python -c "$1" 2>&1; }

echo "== Build and start =="
docker compose up -d --build ledger model-gateway >/tmp/p1_up.log 2>&1 && ok "ledger + model-gateway built and started" || { bad "build/start failed (see /tmp/p1_up.log)"; tail -15 /tmp/p1_up.log; }
for i in $(seq 1 30); do
  inapi "import urllib.request; urllib.request.urlopen('http://model-gateway:8200/healthz', timeout=2)" >/dev/null && break
  sleep 2
done

echo "== Ledger =="
out=$(inapi "import urllib.request,json; print(json.load(urllib.request.urlopen('http://ledger:8100/healthz', timeout=3))['status'])")
[ "$out" = "ok" ] && ok "ledger healthy" || bad "ledger not healthy: $out"

echo "== Model gateway =="
out=$(inapi "import urllib.request,json; m=json.load(urllib.request.urlopen('http://model-gateway:8200/v1/models', timeout=15))['models']; print(','.join(x['model_id'] for x in m if x['healthy']))")
for m in qwen3:8b qwen2.5-coder:7b qwen2.5vl:7b mxbai-embed-large; do
  echo "$out" | grep -q "$m" && ok "gateway sees $m as healthy" || bad "gateway does not see $m as healthy (got: $out)"
done

out=$(inapi "
import urllib.request, json
body = json.dumps({'prompt':'extract the fields','sensitivity':'confidential','attachments':[{'name':'s.pdf','mime':'application/pdf','scanned':True}]}).encode()
req = urllib.request.Request('http://model-gateway:8200/v1/route', data=body, headers={'Content-Type':'application/json'})
print(json.load(urllib.request.urlopen(req, timeout=30))['decision']['selected'])")
[ "$out" = "qwen2.5vl:7b" ] && ok "scanned PDF routes to the vision model" || bad "scan routing returned: $out"

out=$(inapi "
import urllib.request, json
body = json.dumps({'prompt':'Write a Python function that adds two numbers.','user':'u-check','task_id':'T-900','node_id':'n1'}).encode()
req = urllib.request.Request('http://model-gateway:8200/v1/generate', data=body, headers={'Content-Type':'application/json'})
r = json.load(urllib.request.urlopen(req, timeout=240))
print(r['decision']['selected'], len(r['text']) > 0)")
echo "      real generation result: $out"
echo "$out" | grep -q "True" && ok "real end-to-end generation works" || bad "generation failed: $out"
echo "$out" | grep -q "qwen2.5-coder:7b" && ok "coding prompt routed to the coder model" || echo "NOTE  coding prompt was not routed to the coder (classifier decision; see scripts/eval_router.py)"

echo "== Audit trail =="
out=$(inapi "
import urllib.request, json
v = json.load(urllib.request.urlopen('http://ledger:8100/v1/ledger/verify', timeout=5))
ev = json.load(urllib.request.urlopen('http://ledger:8100/v1/ledger/events?limit=1000', timeout=5))['events']
kinds = sorted(set(e['event'] for e in ev))
print(v['ok'], v['checked'], ','.join(kinds))")
echo "      $out"
echo "$out" | grep -q "^True" && ok "ledger chain verifies" || bad "ledger chain does not verify"
echo "$out" | grep -q "model.call" && echo "$out" | grep -q "router.decision" && ok "router.decision and model.call recorded" || bad "gateway events missing from ledger"
docker compose exec -T ledger python /app/verify_ledger.py --db /data/ledger.db | sed 's/^/      /'
docker compose exec -T ledger python /app/verify_ledger.py --db /data/ledger.db >/dev/null && ok "CLI verifier passes on the live database" || bad "CLI verifier failed"

echo "== Isolation (must still hold) =="
for svc in api ledger model-gateway; do
  out=$(docker compose exec -T $svc python -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=3); print('REACHED INTERNET')" 2>&1)
  echo "$out" | grep -q "REACHED INTERNET" && bad "$svc REACHED the internet" || ok "$svc cannot reach the internet"
done

echo; echo "Result: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
