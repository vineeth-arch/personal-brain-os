# The three capture Shortcuts

Three iOS Shortcuts that send captures straight into the cockpit:

| Shortcut | Where it lives | What it sends |
|---|---|---|
| **Brain Text** | Home Screen / Siri / Spotlight | A typed or dictated thought → `POST /api/capture` (JSON) |
| **Brain Voice** | The **Action Button** | A recording → `POST /api/capture/audio` (raw m4a) |
| **Brain Share** | The **share sheet** | A photo → `POST /api/capture/image` (raw JPEG); a link or text → `POST /api/capture` |

Run `python3 scripts/shortcuts/build.py` to regenerate `dist/*.shortcut` from
`build.py`. The readable plists land in `src/` — those are what you diff when
a build changes; the signed `.shortcut` files are opaque and carry a fresh
signature each time, so they are never byte-reproducible and there is no
`--check` mode.

## Installing them

1. AirDrop the three files in `dist/` to the phone (or put them in iCloud
   Drive and tap them in Files).
2. Each one asks **two questions on import**:
   - *Your Brain Cockpit address* — e.g. `https://cockpit.example.com`, no
     trailing slash. A trailing slash or stray whitespace is stripped anyway.
   - *Your access token* — the `api.auth_token` value from `config.json`.
3. For the voice one: **Settings → Action Button → Shortcut → Brain Voice**.
4. For the share one: it registers itself in the share sheet automatically.
   If it doesn't appear, tap **Edit Actions…** at the bottom of the share
   sheet and switch it on.

**The committed files contain no secret.** The address and token only exist
on the phone, as answers to those import questions. But once installed, the
token sits in plain text inside the shortcut on the device — don't re-export
or AirDrop an *installed* copy to anyone, and if you suspect it leaked,
rotate `api.auth_token` and re-import.

## Why they're built by a script

The `.shortcut` format is an undocumented plist of nested "token strings" and
action-output references. It is also silently lenient: a wrong key does not
fail the import, it produces an action with a blank field that only fails
later, on the phone. Generating it from one Python file keeps the three
shortcuts consistent and makes a fix a one-line change instead of thirty
taps.

Two things learned the hard way, both encoded in `build.py`:

- **Named variables (Set Variable / `{"Type": "Variable"}`) resolve to empty**
  in a generated shortcut. Every reference here is a direct action-output
  reference (`{"Type": "ActionOutput", "OutputUUID": …}`), which is also what
  Apple's own gallery workflows use.
- **The Text action is `is.workflow.actions.gettext`**, not `…actions.text`.
  A wrong identifier fails the whole run with "an action could not be found".

## Testing a change

`shortcuts run` on the Mac drives a shortcut end to end, but **Shortcuts
cannot reach `127.0.0.1`** — a request to loopback hangs forever waiting on a
local-network permission that never arrives. So point a test build at a
public URL instead:

```bash
cloudflared tunnel --url http://127.0.0.1:8765          # prints an https URL
python3 scripts/shortcuts/build.py --test "<that url>" <token> --out /tmp/t
```

`--test` bakes the address and token in and replaces every prompt (Ask for
Input, Choose from List, Record Audio) with a fixed value, so the shortcut
runs unattended. Open each file to import it, click **Add Shortcut**, then
`shortcuts run "Brain Text"`. Never commit a `--test` build: it holds a token.

Importing still needs one GUI click per file — there is no `shortcuts add`
subcommand, and the screen must be unlocked.

## If a shortcut misbehaves on the phone

Each POST is tried twice, two seconds apart, with the same `X-Capture-Key`
header — so a retry after a first attempt that silently landed returns the
same note instead of writing a duplicate. If both fail, the notification
shows the server's own "what to do next" sentence.

A genuine network failure (no signal, server down) aborts the shortcut with
iOS's own alert rather than that notification: Shortcuts has no try/catch and
no way to read an HTTP status code. The cockpit's own PWA has a real offline
outbox; the Shortcut deliberately does not try to reimplement one.

## Building them by hand instead

If you'd rather tap them out yourself, the contract is in `GO-LIVE.md` §7 —
method, headers, and query parameters per route.
