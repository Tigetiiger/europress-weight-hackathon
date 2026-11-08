#!/usr/bin/env bash
# Start the soft-sensor API and the web server (landing page, dashboard, docs).
#   scripts/start.sh                 # http://localhost:8080
#   PORT=9000 scripts/start.sh
# Needs the data from scripts/build_data.sh (or the prebuilt runs/replay400 + models/full400).
# Ctrl+C stops both.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${VENV:-$ROOT/.venv}/bin/python"
PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"
SENSOR_PORT="${SOFTSENSOR_PORT:-8093}"
mkdir -p "$ROOT/dashboard/logs"

for f in soft_sensor/runs/replay400/hourly.parquet soft_sensor/models/full400/time/bundle.json; do
  [ -f "$ROOT/$f" ] || { echo "missing $f: run scripts/build_data.sh first" >&2; exit 1; }
done
[ -x "$PY" ] || { echo "missing $PY: run scripts/build_data.sh first" >&2; exit 1; }
command -v node >/dev/null || { echo "node (v20+) is required" >&2; exit 1; }

cd "$ROOT/dashboard"
SOFTSENSOR_SIM_ROOT="$ROOT/soft_sensor" SOFTSENSOR_HOST=127.0.0.1 SOFTSENSOR_PORT="$SENSOR_PORT" \
  "$PY" -m softsensor >> logs/softsensor.log 2>&1 &
API=$!
trap 'kill $API 2>/dev/null' EXIT INT TERM

echo "soft-sensor API starting on 127.0.0.1:$SENSOR_PORT (log: dashboard/logs/softsensor.log)"
for _ in $(seq 1 90); do
  curl -sf "http://127.0.0.1:$SENSOR_PORT/health" >/dev/null 2>&1 && break
  kill -0 $API 2>/dev/null || { echo "soft-sensor API failed, see dashboard/logs/softsensor.log" >&2; exit 1; }
  sleep 1
done

echo "web server on http://$HOST:$PORT  (landing /, dashboard /dashboard/, docs /docs/)"
PORT="$PORT" HOST="$HOST" SENSOR_URL="http://127.0.0.1:$SENSOR_PORT" node server/server.mjs
