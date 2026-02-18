FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY exchange/ ./exchange/
COPY sdk/ ./sdk/

RUN pip install --no-cache-dir -e "./sdk" && \
    pip install --no-cache-dir -e ".[exchange]" && \
    pip install --no-cache-dir psycopg2-binary httpx

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=base /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --from=base /app /app

ENV A2A_EXCHANGE_HOST=0.0.0.0
ENV A2A_EXCHANGE_PORT=3000
ENV A2A_EXCHANGE_AUTO_CREATE_SCHEMA=true

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1

CMD ["python", "-m", "exchange"]
