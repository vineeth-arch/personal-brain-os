# Deploying Brain Cockpit

Two supported paths, same app:

- **Path A — Docker** (the future Linux box: Pi 5, spare laptop, NAS).
- **Path B — Mac interim** (no Docker: two launchd agents on the Mac you
  already use).

Then one **tunnel + security** section (shared by both paths) and a
**moving day** section for when the Mac hands over to the Linux box.

Steps marked **[you]** are actions only you can take — logins, DNS, phone
taps. Everything else is copy-paste.

---

## Path A — Docker on any Linux box

Prereqs on the box: Docker with the compose plugin, git.

1. Clone the repo and enter it:

   ```sh
   git clone <your-repo-url> brain-cockpit && cd brain-cockpit
   ```

2. Make the data folders and config (the API's startup self-check refuses to
   boot if any configured folder is missing — create them all now):

   ```sh
   mkdir -p data/archive data/failed data/models vault CaptureInbox
   cp config.example.json data/config.json
   ```

   Edit `data/config.json`. **Paths are container paths**, so use:
   - `vault_path`: `/vault`
   - `inbox_path`: `/inbox`
   - `archive_path`: `/data/archive`
   - `failed_path`: `/data/failed`
   - `transcription.whispercpp.binary_path`: `/usr/local/bin/whisper-cli`
     (built into the image)
   - `transcription.whispercpp.model_path`: `/data/models/ggml-small.en.bin`
   - set `api.auth_token` to a long random string — this is your login.

3. Download a whisper model into the data folder:

   ```sh
   curl -L -o data/models/ggml-small.en.bin \
     https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
   ```

4. Keys and host paths:

   ```sh
   cp .env.example .env
   ```

   Edit `.env`: fill in the model keys you use, and set `VAULT_DIR` /
   `INBOX_DIR` to where the vault and capture inbox live on this box
   (e.g. the Syncthing folders).

5. Build and start (first build compiles whisper.cpp from source — expect
   10–30 minutes on a Pi):

   ```sh
   docker compose up -d --build
   curl http://localhost:8000/api/health   # → {"ok":true}
   ```

6. Optional services:

   ```sh
   docker compose --profile sync up -d     # syncthing sharing the vault volume
   docker compose --profile tunnel up -d   # cloudflared (token in .env — see below)
   ```

The container runs **both** processes (API + watcher loop) under a tiny
supervisor; if either dies the container restarts. Health check is
`GET /api/health`. All state is in `./data`, the vault, and the inbox —
`docker compose down` loses nothing.

---

## Path B — Mac interim (launchd, no Docker)

1. One-time setup in the repo:

   ```sh
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cd web && npm ci && npm run build && cd ..
   cp config.example.json config.json   # then fill it in (Mac paths)
   ```

   Every folder named in config.json (vault, inbox, archive, failed) must
   exist — the API's startup self-check refuses to boot otherwise, printing
   exactly what's missing.

2. **[you]** Export your API keys in the shell you'll install from — the
   installer copies them into the agents (launchd doesn't read your
   `~/.zshrc`):

   ```sh
   export ANTHROPIC_API_KEY=... GEMINI_API_KEY=...   # the ones you use
   ```

3. Install both agents:

   ```sh
   bash deploy/launchd/install.sh
   ```

   This fills in the repo path, loads `com.innsaeit.cockpit-api` and
   `com.innsaeit.cockpit-watcher` (KeepAlive + start at login), logs to
   `~/Library/Logs/cockpit-*.log`, and prints the status commands.

4. **[you] Lid-closed note:** a sleeping Mac stops the watcher (the watchdog
   will push "the watcher looks stopped"). Keep the Mac on power and either
   leave `caffeinate -s` running, or disable sleep on AC:
   `sudo pmset -c sleep 0`.

Re-running `install.sh` after a key change or repo move is safe — it
replaces and reloads both agents.

---

## Tunnel + security (both paths)

The cockpit should never be a bare port on the internet. The layers:

1. **Cloudflare Tunnel** — outbound-only connection, no port forwarding.
2. **Cloudflare Access** — a login page in front of the hostname.
3. **The bearer token** (`api.auth_token`) — the app's own second factor;
   every API call needs it even after Access lets a browser through.

Steps:

1. **[you]** In the Cloudflare dashboard (Zero Trust → Networks → Tunnels)
   create a tunnel, name it (e.g. `brain-cockpit`), and copy the **tunnel
   token**. (CLI alternative: `cloudflared tunnel login` +
   `cloudflared tunnel create brain-cockpit`.)

2. **[you]** Add a public hostname to the tunnel, e.g.
   `cockpit.yourdomain.com` → service `http://localhost:8000` (Docker path:
   `http://app:8000`). Cloudflare creates the DNS route for you.

3. Start the connector:
   - Docker: put the token in `.env` as `TUNNEL_TOKEN=...`, then
     `docker compose --profile tunnel up -d`.
   - Mac: `brew install cloudflared`, then
     `cloudflared tunnel run --token <token>` (or install it as a service:
     `sudo cloudflared service install <token>`).

4. **[you]** In Zero Trust → Access → Applications, add an application for
   `cockpit.yourdomain.com` with a policy that allows **only your email**
   (one-time PIN is fine). Now the hostname shows a Cloudflare login before
   anything reaches the cockpit.

5. **[you] Phone (PWA install):** open `https://cockpit.yourdomain.com` in
   the phone browser, pass the Access login, paste the server address and
   your `api.auth_token` on the connect screen, then use the browser's
   **Add to Home Screen**. The cockpit now opens like an app.

6. Verify from outside your network (phone on mobile data):
   `https://cockpit.yourdomain.com/api/health` → Access login → `{"ok":true}`.
   Then fill in `deploy.public_url` (and `deploy.tunnel_hostname`) in
   config.json so the Build screen's deployment checklist can tick
   "reachable from the internet" — that milestone is a **manual check by
   design**: only a device outside your LAN can prove it.

---

## Moving day (Mac → Linux box, later)

Nothing is rebuilt; knowledge lives in the vault, config is one file.

1. On the Mac: stop the agents
   (`launchctl bootout gui/$(id -u)/com.innsaeit.cockpit-api`, same for
   `-watcher`).
2. Make sure the vault (and capture inbox) are on the new box — Syncthing
   already does this if you run it; otherwise copy the folders.
3. Copy `config.json` to the new box's `data/` and adjust the paths to the
   container paths from Path A. Keys go into `.env`, not the config.
4. `docker compose up -d --build` on the new box (Path A steps 3–5).
5. **[you]** In the Cloudflare dashboard, point the tunnel at the new box:
   run the connector there (same token), stop it on the Mac. DNS doesn't
   change; your phone notices nothing.
6. `events.db`, the heartbeat, and `backups/` are **disposable by design**
   (CLAUDE.md §1) — copy them if you want the history, skip them if you
   don't. No knowledge is lost either way.
