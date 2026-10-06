#!/usr/bin/env python3
"""Verify the ledger hash chain. Standard library only; works on Python 3.9+.

Usage (from the project root, against a copy of the database):
    python3 scripts/verify_ledger.py --db /path/to/ledger.db
    python3 scripts/verify_ledger.py --db ledger.db --anchor 42:sha256:abcd...   # also detects deleted tail entries
    python3 scripts/verify_ledger.py --save-anchor anchor.txt --db ledger.db    # remember the current head
    python3 scripts/verify_ledger.py --db ledger.db --anchor-file anchor.txt

Inside Docker:
    docker compose exec -T ledger python /app/verify_ledger.py --db /data/ledger.db

Exit code 0 = chain intact, 1 = problem found, 2 = usage / file error.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (os.path.join(HERE, "..", "apps", "ledger"), HERE):  # repo layout, then container layout
    if os.path.isdir(os.path.join(candidate, "ledger_service")):
        sys.path.insert(0, candidate)
        break

from ledger_service.chain import load_entries, verify_entries  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="path to ledger.db")
    ap.add_argument("--anchor", help="saved head as SEQ:HASH, e.g. 42:sha256:...")
    ap.add_argument("--anchor-file", help="file containing a saved anchor (SEQ:HASH)")
    ap.add_argument("--save-anchor", help="write the current head to this file after verifying")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("ERROR: database not found: %s" % args.db)
        return 2

    anchor = args.anchor
    if args.anchor_file:
        anchor = open(args.anchor_file).read().strip()
    expect_seq = expect_hash = None
    if anchor:
        seq_s, _, hash_s = anchor.partition(":")
        expect_seq, expect_hash = int(seq_s), hash_s

    try:
        entries = load_entries(args.db)
    except Exception as e:  # noqa: BLE001
        print("ERROR: could not read ledger: %s" % e)
        return 2

    result = verify_entries(entries, expect_seq, expect_hash)
    if result["ok"]:
        print("OK    chain intact: %d entries, head seq=%d" % (result["checked"], result["head"]["seq"]))
        print("      head hash: %s" % result["head"]["hash"])
        if anchor:
            print("      matches saved anchor at seq %d" % expect_seq)
        if args.save_anchor:
            with open(args.save_anchor, "w") as f:
                f.write("%d:%s\n" % (result["head"]["seq"], result["head"]["hash"]))
            print("      anchor saved to %s" % args.save_anchor)
        return 0
    fb = result["first_break"]
    print("FAIL  chain broken at seq %d" % fb["seq"])
    print("      reason: %s" % fb["reason"])
    print("      %d entries verified before the break" % result["checked"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
