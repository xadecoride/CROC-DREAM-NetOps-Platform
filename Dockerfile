# ==============================================================================
# CROC DREAM NetOps Platform — All-in-One Monolithic Container
# Включает внутри одного контейнера:
#   - PostgreSQL + Redis
#   - Prometheus + Node Exporter + SNMP Exporter
#   - FastAPI Backend + Threaded Worker/Beat + Telegram Bot
#   - React Frontend + Nginx Reverse Proxy (:5173)
#
# Сборка и запуск одной командой:
#   docker build -t croc-netops .
#   docker run -d --name croc-netops -p 5173:5173 -p 8000:8000 -p 9090:9090 croc-netops
# ==============================================================================

FROM mirror.gcr.io/library/node:24-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM mirror.gcr.io/prom/prometheus:v3.15.0 AS prom-bin
FROM mirror.gcr.io/prom/node-exporter:v1.12.1 AS node-exp-bin
FROM mirror.gcr.io/prom/snmp-exporter:v0.30.1 AS snmp-exp-bin

FROM mirror.gcr.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/app/backend/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql \
    postgresql-contrib \
    redis-server \
    nginx \
    git \
    openssh-client \
    iputils-ping \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv
COPY --from=ghcr.io/openconfig/gnmic:0.49.0 /app/gnmic /usr/local/bin/gnmic
COPY --from=prom-bin /bin/prometheus /usr/local/bin/prometheus
COPY --from=node-exp-bin /bin/node_exporter /usr/local/bin/node_exporter
COPY --from=snmp-exp-bin /bin/snmp_exporter /usr/local/bin/snmp_exporter

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --no-dev --no-install-project --extra scrapli

COPY backend/ ./
RUN uv sync --no-dev --extra scrapli

WORKDIR /app
COPY intent ./intent
COPY templates ./templates
COPY lab ./lab
RUN rm -rf /app/lab/running/* && mkdir -p /app/lab/running && \
    echo "[]" > /app/lab/running/prometheus_targets.json && \
    echo "[]" > /app/lab/running/exporter_targets.json && \
    echo "{}" > /app/lab/running/gnmic_targets.yaml
COPY monitoring ./monitoring
COPY scripts ./scripts
RUN mkdir -p /etc/snmp_exporter /etc/gnmic && \
    cp /app/monitoring/snmp.yml /etc/snmp_exporter/snmp.yml && \
    cp /app/monitoring/gnmic/gnmic.yaml /etc/gnmic/gnmic.yaml && \
    sed -i 's|/data/lab/gnmic_targets.yaml|/app/lab/running/gnmic_targets.yaml|g' /etc/gnmic/gnmic.yaml

# Frontend static bundle + Nginx config on port 5173 (and 80)
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html
RUN rm -f /etc/nginx/sites-enabled/default && \
    printf 'server {\n\
    listen 5173 default_server;\n\
    listen 80 default_server;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location /api/ {\n\
        proxy_pass http://127.0.0.1:8000;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header Upgrade $http_upgrade;\n\
        proxy_set_header Connection "upgrade";\n\
        proxy_set_header Host $host;\n\
        proxy_set_header X-Real-IP $remote_addr;\n\
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n\
        proxy_set_header X-Client-Id $http_x_client_id;\n\
        proxy_read_timeout 300s;\n\
    }\n\
    location ~ ^/(healthz|readyz|metrics|docs|openapi\\.json)$ {\n\
        proxy_pass http://127.0.0.1:8000;\n\
        proxy_set_header Host $host;\n\
    }\n\
    location / {\n\
        try_files $uri /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/netops.conf

# Local Prometheus config inside the all-in-one container
RUN mkdir -p /etc/prometheus && \
    cp /app/monitoring/alerts.yml /etc/prometheus/alerts.yml && \
    sed -e 's/api:8000/127.0.0.1:8000/g' \
        -e 's/snmp-exporter:9116/127.0.0.1:9116/g' \
        -e 's/gnmic:9804/127.0.0.1:9804/g' \
        -e '/remote_write:/,+1d' \
        /app/monitoring/prometheus.yml > /etc/prometheus/prometheus.yml && \
    printf '\n  - job_name: "node_exporter"\n    static_configs:\n      - targets: ["127.0.0.1:9100"]\n' >> /etc/prometheus/prometheus.yml

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5173 8000 9090
CMD ["/entrypoint.sh"]
