#!/bin/bash
# ponytail: naive sync — no conflict resolution beyond git's own, no lock file.
# Upgrade to a real merge queue / lock if two machines edit at once and this breaks.
set -euo pipefail
cd "$(dirname "$0")/.."

LOG="/tmp/personal-brain-os-sync.log"
echo "[$(date)] sync start" >> "$LOG"

git pull --rebase --autostash origin main >> "$LOG" 2>&1

if [[ -n "$(git status --porcelain)" ]]; then
  git add -A
  git commit -m "auto-sync: $(date '+%Y-%m-%d %H:%M')" >> "$LOG" 2>&1
fi

git push origin main >> "$LOG" 2>&1
echo "[$(date)] sync done" >> "$LOG"
