#!/usr/bin/env bash
# Backend entrypoint. Serves the FastAPI app on $PORT and, when RUN_DAEMONS=1,
# also runs the lightweight intelligence daemons so the brain shows LIVE activity.
# Paper-trading only. Any daemon failure is non-fatal to the web service.
set -e
mkdir -p data

if [ "${RUN_DAEMONS:-1}" = "1" ]; then
  echo "[entrypoint] starting background daemons (RUN_DAEMONS=1)"
  python -m trader.agents.runtime --loop --every 900 >> data/agents.log 2>&1 &
  python -m trader.agents.supervisor --loop --every 120 >> data/sup.log 2>&1 &
  python -m trader.exits >> data/exits.log 2>&1 &
fi

echo "[entrypoint] starting API on 0.0.0.0:${PORT:-8000}"
exec python -m uvicorn dashboard.app:app --host 0.0.0.0 --port "${PORT:-8000}"
