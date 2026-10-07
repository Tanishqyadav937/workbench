#!/usr/bin/env bash
# Phase 2 checks. Mirrors the spirit of check_phase1.sh; extend as P2.x steps are implemented.
cd "$(dirname "$0")/.." || exit 1
pass=0; fail=0
ok()  { echo "ok    $1"; pass=$((pass+1)); }
bad() { echo "FAIL  $1"; fail=$((fail+1)); }

[ -x scripts/check_phase1.sh ] && ./scripts/check_phase1.sh >/dev/null 2>&1 && ok "phase 1 baseline still passes" || bad "phase 1 baseline"
python3 -m unittest discover -s tests -p "test_phase2_*.py" >/dev/null 2>&1 && ok "gate logic unit tests" || bad "gate logic unit tests"
python3 -c "import json;json.load(open('tests/data/retrieval_gold.json'));json.load(open('tests/data/extraction_gold.json'))" 2>/dev/null && ok "gold files parse" || bad "gold files parse"
grep -q REPLACE tests/data/retrieval_gold.json && echo "todo  retrieval_gold.json still has REPLACE placeholders"
grep -q REPLACE tests/data/extraction_gold.json && echo "todo  extraction_gold.json still has REPLACE placeholders"
echo "TODO  P2.7 egress test must run inside the isolated network (not on the Mac host, which has internet)"
echo "passed=$pass failed=$fail"; [ "$fail" -eq 0 ]
