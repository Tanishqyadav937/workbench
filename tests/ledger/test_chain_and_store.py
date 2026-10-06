import json
import os
import sqlite3
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from ledger_service.chain import EVENT_TYPES, GENESIS, compute_hash, load_entries, verify_entries
from ledger_service.store import Ledger, LedgerError

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCHEMA = json.load(open(os.path.join(ROOT, "schemas", "ledger_entry.schema.json")))


@pytest.fixture()
def led(tmp_path):
    l = Ledger(str(tmp_path / "ledger.db"))
    yield l
    l.close()


def fill(led, n=5):
    for i in range(n):
        led.append("agent", "orchestrator", "tool.call", {"i": i})


def raw_conn(path):
    """Attacker view: opens the file directly and may drop the triggers."""
    c = sqlite3.connect(path, isolation_level=None)
    c.execute("DROP TRIGGER IF EXISTS ledger_no_update")
    c.execute("DROP TRIGGER IF EXISTS ledger_no_delete")
    return c


def test_event_types_match_schema():
    assert set(EVENT_TYPES) == set(SCHEMA["properties"]["event"]["enum"])


def test_entries_satisfy_the_json_schema(led):
    fill(led, 3)
    v = Draft202012Validator(SCHEMA)
    for e in led.events():
        assert not list(v.iter_errors(e)), e


def test_chain_links_and_genesis(led):
    fill(led, 3)
    es = led.events()
    assert [e["seq"] for e in es] == [1, 2, 3]
    assert es[0]["prev_hash"] == GENESIS
    assert es[1]["prev_hash"] == es[0]["hash"]
    assert es[2]["prev_hash"] == es[1]["hash"]
    assert es[0]["hash"] == compute_hash(1, es[0]["ts"], es[0]["actor"], "tool.call", es[0]["payload"], GENESIS)


def test_verify_ok_and_empty(led):
    assert led.verify()["ok"] is True
    fill(led, 10)
    r = led.verify()
    assert r["ok"] and r["checked"] == 10 and r["head"]["seq"] == 10


def test_validation_rejects_bad_input(led):
    with pytest.raises(LedgerError):
        led.append("robot", "x", "tool.call", {})
    with pytest.raises(LedgerError):
        led.append("agent", "x", "made.up.event", {})
    with pytest.raises(LedgerError):
        led.append("agent", "", "tool.call", {})
    with pytest.raises(LedgerError):
        led.append("agent", "x", "tool.call", {"blob": "A" * 9000})


def test_database_blocks_update_and_delete(led):
    fill(led, 3)
    with pytest.raises(sqlite3.DatabaseError):
        led._conn.execute("UPDATE ledger SET payload='{}' WHERE seq=2")
    with pytest.raises(sqlite3.DatabaseError):
        led._conn.execute("DELETE FROM ledger WHERE seq=2")
    assert led.verify()["ok"]


def test_detects_modified_payload(led):
    fill(led, 5)
    c = raw_conn(led.path)
    c.execute("""UPDATE ledger SET payload='{"i":999}' WHERE seq=3""")
    r = led.verify()
    assert not r["ok"] and r["first_break"]["seq"] == 3 and "modified" in r["first_break"]["reason"]


def test_detects_modified_actor_and_timestamp(led):
    fill(led, 4)
    c = raw_conn(led.path)
    c.execute("UPDATE ledger SET actor_id='someone-else' WHERE seq=2")
    assert led.verify()["first_break"]["seq"] == 2
    c.execute("UPDATE ledger SET actor_id='orchestrator' WHERE seq=2")
    assert led.verify()["ok"]  # restoring the value restores validity: only real content matters
    c.execute("UPDATE ledger SET ts='2020-01-01T00:00:00.000Z' WHERE seq=4")
    assert led.verify()["first_break"]["seq"] == 4


def test_detects_deleted_middle_entry(led):
    fill(led, 5)
    raw_conn(led.path).execute("DELETE FROM ledger WHERE seq=3")
    r = led.verify()
    assert not r["ok"] and r["first_break"]["seq"] == 4 and "gap" in r["first_break"]["reason"]


def test_detects_reordered_entries(led):
    fill(led, 4)
    c = raw_conn(led.path)
    c.execute("UPDATE ledger SET seq=100 WHERE seq=2")
    c.execute("UPDATE ledger SET seq=2 WHERE seq=3")
    c.execute("UPDATE ledger SET seq=3 WHERE seq=100")
    assert not led.verify()["ok"]


def test_detects_whole_chain_rewrite_only_with_anchor(led):
    """An attacker with write access who recomputes every hash defeats the chain alone: the anchor catches it."""
    fill(led, 4)
    anchor = led.head()
    entries = load_entries(led.path)
    c = raw_conn(led.path)
    prev = GENESIS
    for e in entries:
        payload = {"i": 777} if e["seq"] == 2 else e["payload"]
        h = compute_hash(e["seq"], e["ts"], e["actor"], e["event"], payload, prev)
        c.execute("UPDATE ledger SET payload=?, prev_hash=?, hash=? WHERE seq=?",
                  (json.dumps(payload, sort_keys=True, separators=(",", ":")), prev, h, e["seq"]))
        prev = h
    assert led.verify()["ok"] is True                                   # chain alone is fooled
    r = led.verify(anchor["seq"], anchor["hash"])                       # anchor is not
    assert not r["ok"] and "anchor" in r["first_break"]["reason"]


def test_detects_truncated_tail_only_with_anchor(led):
    fill(led, 5)
    anchor = led.head()
    c = raw_conn(led.path)
    c.execute("DELETE FROM ledger WHERE seq>3")
    assert led.verify()["ok"] is True                                   # undetectable without an anchor
    r = led.verify(anchor["seq"], anchor["hash"])
    assert not r["ok"] and "truncated" in r["first_break"]["reason"]


def test_anchor_matches_on_untouched_chain(led):
    fill(led, 3)
    a = led.head()
    fill(led, 2)
    assert led.verify(a["seq"], a["hash"])["ok"]


def test_cli_verifier(led, tmp_path):
    fill(led, 4)
    script = os.path.join(ROOT, "scripts", "verify_ledger.py")
    anchor_file = str(tmp_path / "anchor.txt")
    ok = subprocess.run([sys.executable, script, "--db", led.path, "--save-anchor", anchor_file],
                        capture_output=True, text=True)
    assert ok.returncode == 0 and "chain intact" in ok.stdout and os.path.exists(anchor_file)
    raw_conn(led.path).execute("""UPDATE ledger SET payload='{"i":5}' WHERE seq=2""")
    bad = subprocess.run([sys.executable, script, "--db", led.path], capture_output=True, text=True)
    assert bad.returncode == 1 and "broken at seq 2" in bad.stdout
    missing = subprocess.run([sys.executable, script, "--db", str(tmp_path / "nope.db")], capture_output=True, text=True)
    assert missing.returncode == 2


def test_concurrent_appends_keep_the_chain_valid(led):
    import threading
    def work(k):
        for j in range(20):
            led.append("agent", "t%d" % k, "tool.call", {"k": k, "j": j})
    ts = [threading.Thread(target=work, args=(k,)) for k in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    r = led.verify()
    assert r["ok"] and r["checked"] == 120
