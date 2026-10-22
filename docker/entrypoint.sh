#!/usr/bin/env bash
set -euo pipefail

echo "============================================================"
echo "  CROC DREAM NetOps Platform — All-in-One Container Boot"
echo "============================================================"

mkdir -p /app/lab/running
for f in prometheus_targets.json exporter_targets.json; do
    [ -f "/app/lab/running/$f" ] || echo "[]" > "/app/lab/running/$f"
done
[ -f /app/lab/running/gnmic_targets.yaml ] || echo "{}" > /app/lab/running/gnmic_targets.yaml
if [ ! -d /app/.git ]; then
    git init -b main /app && git config --global --add safe.directory /app
fi

# 1. Start PostgreSQL
echo "[1/6] Starting internal PostgreSQL..."
service postgresql start
su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='netops'\" | grep -q 1 || psql -c \"CREATE USER netops WITH SUPERUSER PASSWORD 'netops';\""
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='netops'\" | grep -q 1 || psql -c \"CREATE DATABASE netops OWNER netops;\""

# 2. Start Redis
echo "[2/6] Starting internal Redis..."
service redis-server start

# 3. Apply Alembic migrations
echo "[3/6] Running Alembic database migrations..."
cd /app/backend
/app/backend/.venv/bin/alembic upgrade head
cd /app

# 4. Start Prometheus, Node Exporter, SNMP Exporter & gnmic
echo "[4/6] Starting Prometheus + Node Exporter + SNMP Exporter + gnmic..."
mkdir -p /var/lib/prometheus /var/log/netops
/usr/local/bin/node_exporter --web.listen-address="127.0.0.1:9100" > /var/log/netops/node_exporter.log 2>&1 &
if [ -f /etc/snmp_exporter/snmp.yml ]; then
    /usr/local/bin/snmp_exporter --config.file=/etc/snmp_exporter/snmp.yml --web.listen-address="127.0.0.1:9116" > /var/log/netops/snmp_exporter.log 2>&1 &
fi
if [ -x /usr/local/bin/gnmic ] && [ -f /etc/gnmic/gnmic.yaml ]; then
    GNMIC_USERNAME="${GNMIC_USERNAME:-admin}" GNMIC_PASSWORD="${GNMIC_PASSWORD:-admin}" \
        /usr/local/bin/gnmic --config /etc/gnmic/gnmic.yaml subscribe > /var/log/netops/gnmic.log 2>&1 &
fi
/usr/local/bin/prometheus \
    --config.file=/etc/prometheus/prometheus.yml \
    --storage.tsdb.path=/var/lib/prometheus \
    --web.listen-address="0.0.0.0:9090" > /var/log/netops/prometheus.log 2>&1 &

# 5. Start Nginx (Frontend on :5173 + reverse proxy /api -> 127.0.0.1:8000)
echo "[5/6] Starting Nginx Frontend (:5173)..."
nginx

# 6. Start NetOps Backend + Beat + Worker + Telegram Bot on :8000
echo "[6/6] Starting NetOps FastAPI + Beat + Worker + Bot (:8000)..."
export NETOPS_DATABASE_URL="${NETOPS_DATABASE_URL:-postgresql+psycopg://netops:netops@127.0.0.1:5432/netops}"
export NETOPS_REDIS_URL="${NETOPS_REDIS_URL:-redis://127.0.0.1:6379/0}"
export NETOPS_PROMETHEUS_URL="${NETOPS_PROMETHEUS_URL:-http://127.0.0.1:9090}"
export NETOPS_PORT="${NETOPS_PORT:-8000}"

exec /app/backend/.venv/bin/python /app/backend/run_local.py
