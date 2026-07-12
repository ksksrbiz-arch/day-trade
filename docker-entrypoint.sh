#!/usr/bin/env bash
# Backend entrypoint. Serves the FastAPI app on $PORT and, when RUN_DAEMONS=1,
# also runs the lightweight intelligence daemons so the brain shows LIVE activity.
# Paper-trading only. Any daemon failure is non-fatal to the web service.
set -e
mkdir -p data

# Load hosted secrets (Render Secret File / etc.) from wherever the platform
# mounted them, exporting every KEY=VALUE into the environment for the API AND
# the daemons. Covers Render's known mount paths.
for _f in /etc/secrets/.env /opt/render/project/src/.env /app/.env /.env; do
  if [ -f "$_f" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$_f"
    set +a
    echo "[entrypoint] loaded secrets from $_f"
    break
  fi
done

if [ "${RUN_DAEMONS:-1}" = "1" ]; then
  echo "[entrypoint] starting background daemons (RUN_DAEMONS=1)"
  python -m trader.agents.runtime --loop --every 300 >> data/agents.log 2>&1 &
  python -m trader.agents.supervisor --loop --every 120 >> data/sup.log 2>&1 &
  python -m trader.exits >> data/exits.log 2>&1 &
  # ML: train an initial model now (fast, Alpaca-backed) + keep it improving
  python -m trader.ml.train >> data/ml_train.log 2>&1 &
  python -m trader.ml.daemon --every 6 >> data/ml.log 2>&1 &
  # TensorTrade RL retrain daemon (opt-in, needs requirements-rl.txt + heavy TF).
  # Champion/challenger gated: the live model only improves. OFF by default.
  if [ "${RUN_RL_DAEMON:-0}" = "1" ]; then
    python -m trader.rl.daemon --every "${RL_RETRAIN_EVERY_H:-12}" --episodes "${RL_RETRAIN_EPISODES:-12}" >> data/rl.log 2>&1 &
  fi
  # heartbeat: publish real cached state to the mesh every ~30s so the /brain
  # network stays visibly alive between the slower daemon cycles (esp. while closed).
  python -m trader.pulse --loop --every 30 >> data/pulse.log 2>&1 &
  # autonomous PAPER trading loop, launched as a MANAGED bot so it registers in
  # data/bots.json and shows up (and is controllable) in the terminal's Bots
  # panel + /api/bots -- instead of an invisible raw process. autostart creates a
  # 'main' bot on first boot and resumes enabled bots on redeploy (idempotent:
  # start_bot no-ops if the PID is still alive). Self-halts on the daily
  # drawdown breaker. Disable trading with RUN_TRADER=0.
  if [ "${RUN_TRADER:-1}" = "1" ]; then
    python -m dashboard.autostart >> data/trader.log 2>&1 &
  fi
fi

echo "[entrypoint] starting API on 0.0.0.0:${PORT:-8000}"
exec python -m uvicorn dashboard.app:app --host 0.0.0.0 --port "${PORT:-8000}"
