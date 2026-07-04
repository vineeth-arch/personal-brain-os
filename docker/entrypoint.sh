#!/bin/bash
# Tiny supervisor: run BOTH processes (API + watcher loop) in one container.
# If either dies, the container exits and compose's restart policy revives
# both together — no supervisord, no drift between the two.
set -eu

ROOT="${BRAIN_COCKPIT_ROOT:-/data}"

if [ ! -f "$ROOT/config.json" ]; then
    echo "No config.json in $ROOT."
    echo "Copy config.example.json to $ROOT/config.json, fill in the paths"
    echo "(they must be paths INSIDE the container, e.g. /vault), and restart."
    exit 1
fi

uvicorn api.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

python3 -m pipeline --loop --config "$ROOT/config.json" &
LOOP_PID=$!

trap 'kill "$API_PID" "$LOOP_PID" 2>/dev/null' TERM INT

# exit as soon as EITHER child exits — the restart policy takes it from there
# (|| captures the code without tripping set -e)
exit_code=0
wait -n "$API_PID" "$LOOP_PID" || exit_code=$?
kill "$API_PID" "$LOOP_PID" 2>/dev/null || true
exit "$exit_code"
