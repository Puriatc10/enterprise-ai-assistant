#!/usr/bin/env bash
# Run the API and the web client together. Ctrl-C stops both.
#
#   bash scripts/dev.sh
#
# Override the ports if 8000/5173 are taken:
#   API_PORT=8010 WEB_PORT=5174 bash scripts/dev.sh
# (if you move the API port, update VITE_API_BASE_URL in the repo-root .env.local)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="$ROOT/apps/api"
WEB="$ROOT/apps/web"
PY="$API/.venv/Scripts/python.exe"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

if [ ! -x "$PY" ]; then
  echo "No virtualenv at apps/api/.venv."
  echo "Create it first:  cd apps/api && uv sync --python 3.12 --extra llm"
  exit 1
fi

# The support domain reads a SQLite file; recreate the demo rows if the file is gone.
if [ ! -f "$API/data/demo_support.sqlite3" ]; then
  echo "seeding the support demo database…"
  ( cd "$API" && "$PY" scripts/seed_support_sqlite.py )
fi

API_PID=""
WEB_PID=""
cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

( cd "$API" && exec "$PY" -m uvicorn app.main:app --reload --port "$API_PORT" ) &
API_PID=$!
( cd "$WEB" && exec npm run dev -- --port "$WEB_PORT" --strictPort ) &
WEB_PID=$!

echo
echo "  api   http://127.0.0.1:$API_PORT/health      (openapi at /docs)"
echo "  web   http://localhost:$WEB_PORT"
echo
echo "Ctrl-C stops both."
wait
