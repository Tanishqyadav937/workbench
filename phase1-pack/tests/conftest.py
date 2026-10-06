import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("apps/ledger", "apps/model_gateway", "libs", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
