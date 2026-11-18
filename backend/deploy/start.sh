#!/bin/sh
# Container entry point (Dockerfile): the API always; the scheduler only when RUN_WORKER=true.
#
#   RUN_WORKER=false (default)   API only: nothing is scheduled, no API-Football request, nothing is sealed
#   RUN_WORKER=true              + the worker: nightly backfill and the paper betting recorder every 5 minutes
#   WORKER_ARGS="--no-paper"     the worker without the recorder (backfill only)
#
# The worker is restarted if it ever exits; the API is the main process (it receives the platform's stop signal).
set -eu

if [ "${RUN_WORKER:-false}" = "true" ]; then
  (
    while true; do
      # shellcheck disable=SC2086  # WORKER_ARGS is a list of flags
      python -m app.cli worker ${WORKER_ARGS:-} || true
      echo "worker exited; restarting in 30 s" >&2
      sleep 30
    done
  ) &
  echo "worker started (args: ${WORKER_ARGS:-none})"
else
  echo "worker off (RUN_WORKER=${RUN_WORKER:-false}): API only"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
