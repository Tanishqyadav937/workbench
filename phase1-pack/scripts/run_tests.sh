#!/usr/bin/env bash
# Run the whole test suite in a clean Python 3.12 container (same as the services use), so it does not
# depend on the Python version of your Mac. Needs Docker running and internet for the pip install.
cd "$(dirname "$0")/.." || exit 1
docker run --rm -v "$PWD":/work -w /work python:3.12-slim bash -c \
  "pip install -q fastapi uvicorn httpx pytest pyyaml jsonschema 2>&1 | tail -1; python -m pytest tests -q -p no:cacheprovider"
