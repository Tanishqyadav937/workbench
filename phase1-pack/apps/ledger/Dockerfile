# Build context is the project root:  docker compose build ledger
FROM python:3.12-slim
RUN useradd -m -u 10001 app && mkdir /data && chown app /data
WORKDIR /app
COPY apps/ledger/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/ledger/ledger_service ./ledger_service
COPY scripts/verify_ledger.py ./verify_ledger.py
USER app
ENV LEDGER_DB=/data/ledger.db PYTHONUNBUFFERED=1
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=2)"
CMD ["uvicorn", "ledger_service.main:get_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
