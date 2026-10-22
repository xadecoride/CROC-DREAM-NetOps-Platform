FROM mirror.gcr.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv
# gnmic нужен платформе для проверки gNMI (gRPC Capabilities) перед переводом устройства на поток.
COPY --from=ghcr.io/openconfig/gnmic:0.49.0 /app/gnmic /usr/local/bin/gnmic

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --no-install-project --extra scrapli

COPY alembic.ini ./
COPY migrations ./migrations
COPY netops ./netops
ENV PYTHONPATH="/app"
RUN uv sync --no-dev --no-install-project --extra scrapli --offline || true

RUN useradd --create-home --uid 1000 netops \
    && mkdir -p /data \
    && chown -R netops:netops /app /data \
    && git config --system safe.directory '*'
USER netops

EXPOSE 8000
CMD ["uvicorn", "netops.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
