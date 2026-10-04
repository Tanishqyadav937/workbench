#!/usr/bin/env python3
"""Validate every schema in schemas/ and check its examples.

For each <name>.schema.json:
  - the schema itself must be a valid JSON Schema
  - examples/<name>.valid.json   must PASS
  - examples/<name>.invalid.json must be REJECTED

Run from the project root:   python3 scripts/validate_schemas.py
Exit code 0 = everything OK, 1 = at least one problem.
Works on Python 3.9+.
"""
import glob
import json
import os
import sys

from jsonschema import Draft202012Validator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMAS = os.path.join(ROOT, "schemas")
EXAMPLES = os.path.join(SCHEMAS, "examples")


def load(path):
    with open(path) as f:
        return json.load(f)


def main():
    problems = 0
    files = sorted(glob.glob(os.path.join(SCHEMAS, "*.schema.json")))
    if not files:
        print("No schemas found in", SCHEMAS)
        return 1

    for path in files:
        name = os.path.basename(path).replace(".schema.json", "")
        schema = load(path)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as e:  # noqa: BLE001
            print("FAIL  %-20s schema is invalid: %s" % (name, e))
            problems += 1
            continue
        validator = Draft202012Validator(schema)

        valid_path = os.path.join(EXAMPLES, name + ".valid.json")
        invalid_path = os.path.join(EXAMPLES, name + ".invalid.json")
        if not (os.path.exists(valid_path) and os.path.exists(invalid_path)):
            print("FAIL  %-20s missing example file(s)" % name)
            problems += 1
            continue

        good_errors = list(validator.iter_errors(load(valid_path)))
        bad_errors = list(validator.iter_errors(load(invalid_path)))

        if good_errors:
            print("FAIL  %-20s valid example was rejected: %s" % (name, good_errors[0].message))
            problems += 1
        elif not bad_errors:
            print("FAIL  %-20s invalid example was ACCEPTED (schema too loose)" % name)
            problems += 1
        else:
            print("OK    %-20s valid passes, invalid rejected (%d errors)" % (name, len(bad_errors)))

    print("\n%s" % ("ALL SCHEMAS OK" if problems == 0 else "%d problem(s) found" % problems))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
