# Build context is the project root:  docker compose build model-gateway
FROM python:3.12-slim
RUN useradd -m -u 10001 app
WORKDIR /app
COPY apps/model_gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/model_gateway/gateway ./gateway
COPY libs/ledger_client.py ./ledger_client.py
USER app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 \
    MODEL_REGISTRY_DIR=/config/model_registry ROUTER_RULES=/config/router_rules.yaml
EXPOSE 8200
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8200/healthz', timeout=2)"
CMD ["uvicorn", "gateway.main:get_app", "--factory", "--host", "0.0.0.0", "--port", "8200"]
