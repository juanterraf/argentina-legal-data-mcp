# Argentina Legal & Data MCP — container image
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (better layer caching). lxml ships manylinux wheels — no build deps.
COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
RUN pip install ".[http]"

# Defaults: remote HTTP transport, auth on, dataset on a mounted volume.
ENV ARGMCP_TRANSPORT=streamable-http \
    ARGMCP_HOST=0.0.0.0 \
    ARGMCP_PORT=8000 \
    ARGMCP_DATA_DIR=/app/data \
    ARGMCP_CACHE_DIR=/cache \
    ARGMCP_DATASET_PATH=/data/infoleg.sqlite \
    ARGMCP_AUTH_ENABLED=true \
    ARGMCP_API_KEYS_PATH=/secrets/api-keys.json

VOLUME ["/data", "/cache", "/secrets"]
EXPOSE 8000

# Liveness: the streamable-http endpoint should respond to a JSON-RPC ping path.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"ARGMCP_PORT\",\"8000\")}/mcp', timeout=4)" || exit 1

CMD ["python", "-m", "arg_legal_mcp", "serve", "--transport", "streamable-http"]
