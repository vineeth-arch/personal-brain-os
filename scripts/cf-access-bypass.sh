#!/usr/bin/env bash
# One-off: create the Cloudflare Access "Bypass" app that lets the iOS Shortcuts
# reach the cockpit API (capture, search, health). The rest of the site keeps
# its login. Needs CF_API_TOKEN (Access: Apps and Policies, Edit) and
# CF_ACCOUNT_ID in the environment; the token is never printed.
#   usage: scripts/cf-access-bypass.sh [hostname]
set -euo pipefail
: "${CF_API_TOKEN:?set CF_API_TOKEN first}"
if ! [[ "${CF_ACCOUNT_ID:-}" =~ ^[0-9a-f]{32}$ ]]; then  # unset or a placeholder: ask Cloudflare
  CF_ACCOUNT_ID=$(curl -sS https://api.cloudflare.com/client/v4/accounts -H "Authorization: Bearer $CF_API_TOKEN" | python3 -c '
import sys, json
d = json.load(sys.stdin)
r = d.get("result") or []
if len(r) != 1:
    sys.exit("Set CF_ACCOUNT_ID to one of: " + json.dumps([(a["name"], a["id"]) for a in r] or d.get("errors")))
print(r[0]["id"])')
fi
HOST="${1:-cockpit.vineethnair.com}"
NAME="Cockpit Shortcuts API"
API="https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/access"

call() { # METHOD PATH [JSON body]
  local args=(-sS -X "$1" "$API/$2" -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json")
  [ $# -gt 2 ] && args+=(--data "$3")
  curl "${args[@]}"
}
result_id() { python3 -c '
import sys, json
d = json.load(sys.stdin)
if not d.get("success"):
    sys.exit("Cloudflare said: " + json.dumps(d.get("errors")))
print(d["result"]["id"])'; }

if call GET apps | python3 -c '
import sys, json
d = json.load(sys.stdin)
sys.exit(0 if any(a.get("name") == sys.argv[1] for a in d.get("result") or []) else 1)' "$NAME"; then
  echo "\"$NAME\" already exists, nothing to do."; exit 0
fi

pid=$(call POST policies '{"name":"Shortcuts bypass","decision":"bypass","include":[{"everyone":{}}]}' | result_id)
body=$(python3 -c '
import json, sys
host, pid = sys.argv[1:3]
print(json.dumps({
    "type": "self_hosted", "name": "Cockpit Shortcuts API", "session_duration": "24h",
    "app_launcher_visible": False,
    "destinations": [{"type": "public", "uri": f"{host}/{p}"} for p in ("api/capture", "api/search", "api/health")],
    "policies": [{"id": pid, "precedence": 1}]}))' "$HOST" "$pid")
call POST apps "$body" | result_id >/dev/null
echo "Created \"$NAME\" (bypass) for $HOST: api/capture, api/search, api/health."
echo "Wait a minute, then: curl -sS https://$HOST/api/health"
