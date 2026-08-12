# GO LIVE — from zero to your cockpit on a public URL

The single happy path: a cheap VPS, Docker, a Cloudflare Tunnel (no domain
purchase, no open ports beyond SSH), vault synced to your Mac and phone,
captures flowing in from anywhere, and Gmail/Calendar connected read-only
(plus draft-only — sending always happens in Gmail, by you).

Steps marked **[YOU]** are things only you can do — an account login, a
button in someone else's dashboard, a phone tap. Everything else is
copy-paste on the server. Budget ~45 minutes the first time, then updates
are one command.

Why a VPS and not Vercel/Supabase: the cockpit's whole design is that your
Obsidian vault is a folder of plain files watched by a long-running process.
Serverless platforms have no persistent disk and no long-running processes,
and the constitution forbids moving note content into a hosted database. A
VPS is the smallest thing that runs the real app.

---

## 1. Provision the server [YOU]

**Try Oracle Cloud Always Free first — it costs nothing to attempt, and if it
works you get better specs than any paid tier:**

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) (needs a card for
   verification; the Always Free shapes are never billed).
2. Create a compute instance: shape **VM.Standard.A1.Flex** (ARM, Always Free
   — up to 4 OCPU / 24 GB RAM, absurdly generous), image **Ubuntu 24.04**.
3. If you hit "Out of host capacity" — this is common on Always Free ARM
   shapes — **don't fight it**. Try a different Availability Domain once or
   twice; if it still fails, move on to step 4. It's a known Oracle quirk,
   not something wrong on your end.

**Fallback (genuinely the real recommendation, not a consolation prize):**

- **Hetzner Cloud CAX11** — ARM64, 2 vCPU / 4 GB / 40 GB, **~€3.79/mo**. Better
  performance per euro than the x86 tiers, and this repo's Docker image
  already builds multi-arch (`linux/amd64,linux/arm64`), so ARM costs you
  nothing in compatibility.
- Prefer to never think about ARM at all? **Hetzner CX23** (Intel, ~€5.49/mo)
  is the same setup for about $2/month more.

Either way: **Ubuntu 24.04**, add your SSH key at creation, note the server's
public IP.

## 2. Install Docker + lock the firewall (5 min)

SSH in (`ssh root@<server-ip>` — Hetzner; Oracle defaults to `ubuntu@<ip>`
with sudo), then:

```bash
curl -fsSL https://get.docker.com | sh
ufw allow 22          # SSH
ufw allow 22000        # Syncthing sync protocol (§5)
ufw --force enable
```

Notice **80, 443, and 8000 stay closed**. The Cloudflare Tunnel below is
outbound-only — nothing needs to accept inbound web traffic, which is the
whole point of this approach.

## 3. Start the cockpit (10–20 min, mostly unattended)

```bash
git clone https://github.com/vineeth-arch/personal-brain-os.git
cd personal-brain-os
cp .env.example .env
nano .env        # set ANTHROPIC_API_KEY and OPENAI_API_KEY (see note below)
docker compose --profile sync up -d --build
```

**On transcription**: this build defaults to **OpenAI cloud transcription**
when `OPENAI_API_KEY` is set — noticeably faster than whisper.cpp on a small
VPS's 1–2 shared vCPUs, and only a few dollars a month at personal-capture
volume. Set the key and you're done; no model download needed. (Prefer fully
local/free transcription instead? Leave `OPENAI_API_KEY` blank and download
the whisper model — `curl -L -o data/models/ggml-small.en.bin
https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin`
— it'll just run slower per memo. Either engine is a one-click toggle later
in Integrations.)

The first build compiles whisper.cpp from source regardless (it's always
bundled as a fallback) — expect 10–20 minutes. Then:

```bash
docker compose logs app | grep -A3 "ACCESS TOKEN"
```

**First boot generates everything automatically**: `data/config.json` with
container paths, the four required folders, git initialized in the vault,
and your access token — printed once in that log line (also readable later
at `data/config.json` → `api.auth_token`).

## 4. Cloudflare Tunnel + Access [YOU] (10 min)

This is the whole "public URL" part, and it needs a free Cloudflare account
(no domain purchase required — Cloudflare can give you a subdomain, or use
one you already have).

1. [dash.cloudflare.com](https://dash.cloudflare.com) → **Zero Trust** →
   **Networks → Tunnels** → **Create a tunnel** → name it `brain-cockpit` →
   copy the **tunnel token** shown.
2. Same screen, **Public Hostname** tab → add one: hostname of your choice
   (e.g. `cockpit.yourdomain.com`, or a subdomain of a domain Cloudflare
   already manages for you) → service `http://app:8000` (the Docker service
   name — traffic goes container-to-container, never touching a public port).
3. Back on the server:

   ```bash
   echo "TUNNEL_TOKEN=<paste the token>" >> .env
   docker compose --profile sync --profile tunnel up -d
   ```

4. **Zero Trust → Access → Applications** → add an application for your
   hostname → policy: allow **only your email** (one-time PIN login is
   fine). Now the hostname shows a Cloudflare login screen before anything
   reaches the cockpit — a second layer in front of the app's own token.

5. Verify: visit `https://cockpit.yourdomain.com/api/health` from your
   **phone on mobile data** (not the same WiFi as the server) — Access login,
   then `{"ok":true}`. That's the "reachable from the internet" proof; only a
   device outside your network can actually confirm it, which is why this
   step is yours to run.

## 5. Sync the vault to your Mac + phone (15 min)

Syncthing is already running (you started it with `--profile sync` above).

```bash
ssh -L 8384:localhost:8384 root@<server-ip>   # tunnel the admin UI, don't expose it
```

Open `http://localhost:8384` in your local browser:

- Set a GUI password immediately (Settings → GUI).
- Under Folders, the vault and inbox are already shared — grab this
  device's ID and share it out to your other devices.
- **Mac**: install [Syncthing](https://syncthing.net), pair by device ID,
  accept both folders. Point Obsidian desktop at the synced vault folder.
- **iPhone**: install **Möbius Sync** (a Syncthing client), pair, accept the
  folders. Open the vault from that folder in Obsidian mobile.
- **Android**: the official Syncthing app; same pairing steps.

## 6. Connect Google — Gmail + Calendar [YOU] (10 min)

Read-only Gmail (recent unread) and Calendar (next 7 days), plus the ability
to save a Gmail **draft** — never send. Sending stays a human act in Gmail,
by design (the constitution forbids this app auto-sending anything, and
there is no send code path in the cockpit at all).

1. [console.cloud.google.com](https://console.cloud.google.com) → new
   project → **APIs & Services → Library** → enable **Gmail API** and
   **Google Calendar API**.
2. **OAuth consent screen**: User type "External", add your own Google
   account as a test user (test-mode apps work fine for personal use, no
   Google review needed).
3. **Credentials → Create credentials → OAuth client ID** → application type
   **Web application** → Authorized redirect URI:
   `https://cockpit.yourdomain.com/api/google/callback` (your tunnel
   hostname from §4).
4. Copy the client ID and secret into `.env` on the server
   (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`), then:

   ```bash
   docker compose --profile sync --profile tunnel up -d
   ```

5. Open the cockpit → **Integrations** → Gmail card → **Connect Google** →
   sign in and approve. The refresh token is stored in `data/config.json`;
   the Gmail and Calendar cards go live with real data.

## 7. Capture from your phone (5 min)

The pipeline picks up text, links, and voice files dropped in the synced
**inbox** folder — including `.m4a` voice memos and WhatsApp-style `.opus`
forwards.

- **Quick text/link, any device**: the cockpit's own capture box — goes
  straight to the server over the API, no sync needed.
- **iOS voice memos**: Shortcuts app → new shortcut → "Receive input from
  Share Sheet" → "Save File" → the synced inbox folder. Record → Share →
  one tap → a transcribed note lands in your vault within a minute or two.
- **Android**: any recorder set to save into the synced inbox folder works
  the same way.

## 8. Verify (2 min)

Integrations screen: whisper/OpenAI engine, model key, ntfy (optional),
vault git, watcher heartbeat, Gmail, Calendar — all green or deliberately
grey (unconfigured items say so plainly, never a false red). Send yourself a
test capture from your phone while off your home WiFi; once it appears in
the vault, mark `deploy-tunnel` and `deploy-reachable` done in `checks.json`
— that's the finish line for "live."

---

## Updating later

```bash
cd personal-brain-os && git pull && docker compose --profile sync --profile tunnel up -d --build
```

No CI, no image registry — the server builds what it runs, on your say-so.
Roll back with `git checkout <old-sha>` and the same command.

## Backups

Everything that matters is files: the vault (git-committed on every AI
write, synced to your other devices by Syncthing — those are live offsite
copies) and `data/config.json` (holds your tokens — copy it somewhere safe
once). `events.db` is disposable pipeline logs; losing it loses no knowledge
(constitution rule 1).

## Alternative: your own domain with Caddy, no Cloudflare

If you'd rather terminate HTTPS yourself instead of using a tunnel: point an
A record at the server's IP, open ports 80/443 in `ufw` instead of leaving
them closed, put `DOMAIN=cockpit.yourdomain.com` in `.env`, and run
`docker compose --profile sync --profile proxy up -d --build`. Caddy
(`deploy/Caddyfile`) fetches and renews a Let's Encrypt certificate on its
own. Skip §4 entirely in this variant — there's no Cloudflare layer, so the
app's own bearer token is your only front door; treat the access token with
that in mind.
