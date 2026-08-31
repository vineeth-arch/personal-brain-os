#!/bin/bash
# Tiny supervisor: run BOTH processes (API + watcher loop) in one container.
# If either dies, the container exits and compose's restart policy revives
# both together — no supervisord, no drift between the two.
set -eu

ROOT="${BRAIN_COCKPIT_ROOT:-/data}"
# Both children must agree on where state lives. The API reads BRAIN_COCKPIT_ROOT;
# so does pipeline/watcher.py. Exporting it here is what stops the watcher from
# falling back to the CWD (/app) and writing an events.db the API never reads.
export BRAIN_COCKPIT_ROOT="$ROOT"

if [ ! -f "$ROOT/config.json" ]; then
    echo "No config.json in $ROOT — first boot, generating one."
    python3 scripts/bootstrap.py --docker --root "$ROOT" || exit 1
fi

# One-time rescue for containers built before the roots were separated: the
# loop used to write /app/events.db, which is ephemeral. Move it onto the
# volume rather than discarding it — it carries the ingest de-dupe table, and
# losing that re-imports every watched recording as a duplicate note.
if [ -f /app/events.db ] && [ ! -f "$ROOT/events.db" ]; then
    echo "Migrating the pre-existing /app/events.db onto $ROOT (was ephemeral)."
    mv /app/events.db "$ROOT/events.db"
    [ -f /app/.watcher-heartbeat ] && mv /app/.watcher-heartbeat "$ROOT/.watcher-heartbeat"
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
