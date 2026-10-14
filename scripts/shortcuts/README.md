# The Brain Shortcuts (iOS 27)

Six iOS Shortcuts that send captures to the cockpit and search it:

| Shortcut | Where it lives | What it does |
|---|---|---|
| **Brain Text** | Home Screen / Siri / Spotlight | A typed or dictated thought (+ tag) → `POST /api/capture` |
| **Brain Voice** | **Action Button** | Records, no prompts → `POST /api/capture/audio` |
| **Brain Share** | Share sheet (links, text) | Shared item + a thought + tag → `POST /api/capture` |
| **Brain Photo** | Share sheet (photos), or pick from Photos | Resize → JPEG → optional Apple Intelligence description → `POST /api/capture/image?ai_note=` |
| **Brain Screenshot** | iOS 27 **Screenshot** automation | Latest screenshot, same as Brain Photo |
| **Brain Ask** | Siri / Home Screen | `GET /api/search`, reads back the top five matches |

`src/*.xml` are the readable, diffable shortcuts (no secrets). `dist/*.shortcut`
are the signed files you install.

## Installing them

1. **Delete any older shortcut with the same name** on the phone first. iOS
   silently skips an import whose name already exists.
2. AirDrop the files in `dist/` to the phone (or put them in iCloud Drive and
   tap them in Files) and tap **Add Shortcut**.
3. Each asks on import:
   - *Your Brain Cockpit address*, e.g. `https://cockpit.example.com`.
     **No trailing slash.** A slash at the end makes the shortcut stop with a
     message telling you to re-import.
   - *Your access token*, the `api.auth_token` value from `config.json`.
   - Photo and Screenshot also ask *Describe with Apple Intelligence? yes/no*.
4. Brain Voice: **Settings → Action Button → Shortcut → Brain Voice**.
5. Brain Screenshot: **Shortcuts → Automation → + → Screenshot → Run Brain
   Screenshot**, with *Ask Before Running* on so not every screenshot is sent.
6. If the address is behind Cloudflare Access, add a **Bypass** Access
   application for `/api/capture`, `/api/search` and `/api/health`. Shortcuts
   cannot complete a browser login.

The committed files hold no secret. Once installed, the token sits in plain
text inside the shortcut on the device: don't re-export or AirDrop an
*installed* copy, and if you suspect it leaked, rotate `api.auth_token` and
re-import.

## Offline-safe: the Brain Outbox

Text, Share and Voice first save the capture to **Files › Shortcuts › Brain
Outbox** (`<key>.json` or `<key>.m4a`), then send everything in that folder,
and delete each file only when the server answers with an `id`. No signal, a
server outage or a wrong token leaves the file there, and the next run of any
of the three sends it. The file name is the `X-Capture-Key`, and the server
never expires those keys, so a resend can't create a duplicate note.

A capture the server rejects for good (for example an empty thought, a 400)
stays in the outbox and shows the server's what/todo notification on every
run. Delete that file in Files › Shortcuts › Brain Outbox.

Photo and Screenshot are not queued: the original stays in your library, so
just share it again.

## Apple Intelligence (Photo, Screenshot)

With *yes* on import, the shortcut asks the system model to describe the photo
(under 400 characters, including visible text) and sends it as `ai_note`. The
server stores it under `## On-device AI description`, never as your insight,
and still runs its own vision too. If Apple Intelligence is off or refuses,
the run stops with an error: delete the shortcut and re-import it with *no*.

## How they're built

Every shortcut is built by the **shortcuts-playground** plugin
(`/shortcuts-playground:build`, edits via `/shortcuts-playground:remix` on the
file in `src/`), which validates and signs it. Actions its knowledge base
doesn't document were verified on macOS 27 first; see `PROBE27.md`
(`probe27.py` regenerates those probes). After building or remixing:

```bash
cp ~/Documents/"Shortcuts Playground"/drafts/"Brain Text.xml" scripts/shortcuts/src/
cp ~/Documents/"Shortcuts Playground"/"Brain Text.shortcut" scripts/shortcuts/dist/
python3 scripts/shortcuts/check_shortcuts.py
```

`check_shortcuts.py` fails a shortcut that bakes in an address or token,
points an import question at the wrong action, sends before saving to the
outbox, POSTs without an `X-Capture-Key`, or fails the plugin validator.

Things that break a generated shortcut, learned the hard way:

- **URL Encode returns empty** unless its input is a text token string.
- **Get Items in Range errors** when the list is shorter than the range.
- **Replace Text and Format Date hang the runner.**
- An **If** needs its input wrapped as `{"Type": "Variable", ...}`. The old
  hand-written If failed for that reason. With the wrapper, a named variable
  set in each branch is readable after End If.

## Testing a change

`shortcuts run` drives a shortcut, but Shortcuts can't reach `127.0.0.1`, so
run a throwaway cockpit (`BRAIN_COCKPIT_ROOT=<temp root>`, test vault, test
token) behind `cloudflared tunnel --url http://127.0.0.1:8765`. Build a test
copy with the URL and token baked into the import-question Text actions,
import it under a different name, and pass input as a **file path**
(`shortcuts run "Name" -i /tmp/x.txt`). Never commit a test copy: it holds a
token. Files you create in Brain Outbox from the shell aren't seen by
Shortcuts until iCloud indexes them; test the outbox with files the shortcut
itself writes.
