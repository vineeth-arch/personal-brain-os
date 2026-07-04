#!/bin/bash
# Mac interim path: install the two launchd agents (API + watcher loop).
# Run from anywhere: it anchors on its own location to find the repo.
#   bash deploy/launchd/install.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOGS_DIR="$HOME/Library/Logs"

if [ ! -x "$REPO/.venv/bin/uvicorn" ]; then
    echo "No .venv in $REPO yet. Create it first:"
    echo "  cd $REPO && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
if [ ! -f "$REPO/config.json" ]; then
    echo "No config.json in $REPO yet. Copy config.example.json and fill in the paths."
    exit 1
fi

mkdir -p "$AGENTS_DIR" "$LOGS_DIR"

# Fill each template: repo path, home path, and the API keys from THIS shell
# (launchd agents don't read your shell profile — the values are baked into
# the plists, which live in your home folder, not the repo).
install_plist() {
    local name="$1"
    sed -e "s|__REPO__|$REPO|g" \
        -e "s|__HOME__|$HOME|g" \
        -e "s|__ANTHROPIC_API_KEY__|${ANTHROPIC_API_KEY:-}|g" \
        -e "s|__GEMINI_API_KEY__|${GEMINI_API_KEY:-}|g" \
        -e "s|__GROQ_API_KEY__|${GROQ_API_KEY:-}|g" \
        -e "s|__OPENROUTER_API_KEY__|${OPENROUTER_API_KEY:-}|g" \
        -e "s|__OPENAI_API_KEY__|${OPENAI_API_KEY:-}|g" \
        -e "s|__APIFY_TOKEN__|${APIFY_TOKEN:-}|g" \
        "$HERE/$name.plist" > "$AGENTS_DIR/$name.plist"

    # unload first so re-running the installer picks up changes
    launchctl bootout "gui/$(id -u)/$name" 2>/dev/null || true
    if launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/$name.plist" 2>/dev/null; then
        echo "Loaded $name (launchctl bootstrap)."
    else
        # older macOS fallback
        launchctl load -w "$AGENTS_DIR/$name.plist"
        echo "Loaded $name (launchctl load -w)."
    fi
}

install_plist "com.innsaeit.cockpit-api"
install_plist "com.innsaeit.cockpit-watcher"

cat <<EOF

Both agents installed. They start now and on every login, and restart if they
crash (KeepAlive).

Check on them:
  launchctl print gui/$(id -u)/com.innsaeit.cockpit-api | head -20
  launchctl print gui/$(id -u)/com.innsaeit.cockpit-watcher | head -20
  tail -f $LOGS_DIR/cockpit-api.log $LOGS_DIR/cockpit-watcher.log
  curl http://127.0.0.1:8000/api/health

Stop them:
  launchctl bootout gui/$(id -u)/com.innsaeit.cockpit-api
  launchctl bootout gui/$(id -u)/com.innsaeit.cockpit-watcher

Lid-closed note: a sleeping Mac stops the watcher (the watchdog will push
"the watcher looks stopped"). To run with the lid closed, either keep it on
power and run:  caffeinate -s   in a terminal you leave open, or disable
sleep on AC entirely:  sudo pmset -c sleep 0
EOF
