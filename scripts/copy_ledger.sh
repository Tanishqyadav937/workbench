#!/usr/bin/env bash
# Copy a consistent snapshot of the live ledger out of the container, for verification or the tamper demo.
# Usage: bash scripts/copy_ledger.sh [destination, default ./ledger_copy.db]
cd "$(dirname "$0")/.." || exit 1
dest="${1:-./ledger_copy.db}"
docker compose exec -T ledger python -c "
import sqlite3
src = sqlite3.connect('/data/ledger.db'); dst = sqlite3.connect('/tmp/ledger_copy.db'); src.backup(dst); dst.close()" || exit 1
docker compose cp ledger:/tmp/ledger_copy.db "$dest" && echo "snapshot saved to $dest"
