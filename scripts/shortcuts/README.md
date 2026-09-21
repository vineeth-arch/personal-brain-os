# The three capture Shortcuts

Three iOS Shortcuts that send captures straight into the cockpit:

| Shortcut | Where it lives | What it sends | Verified |
|---|---|---|---|
| **Brain Text** | Home Screen / Siri / Spotlight | A typed or dictated thought → `POST /api/capture` | ✅ end to end |
| **Brain Share** | The **share sheet** | A link, Safari page or selected text → `POST /api/capture` | ✅ end to end |
| **Brain Voice** | The **Action Button** | A recording → `POST /api/capture/audio` (raw m4a) | ⚠️ see below |

Run `python3 scripts/shortcuts/build.py` to regenerate `dist/*.shortcut`.
The readable plists land in `src/` — those are what you diff. The signed
files carry a fresh signature each time, so they are never byte-reproducible
and there is no `--check` mode.

## Installing them

1. AirDrop the three files in `dist/` to the phone (or put them in iCloud
   Drive and tap them in Files).
2. Each asks **two questions on import**:
   - *Your Brain Cockpit address* — e.g. `https://cockpit.example.com`.
     **No trailing slash**, no stray spaces: the answer is used exactly as
     typed (see "Replace Text" below for why nothing tidies it up).
   - *Your access token* — the `api.auth_token` value from `config.json`.
3. For the voice one: **Settings → Action Button → Shortcut → Brain Voice**.
4. The share one registers itself in the share sheet. If it doesn't appear,
   tap **Edit Actions…** at the bottom of the share sheet and switch it on.

**The committed files contain no secret** — the address and token exist only
as answers on your phone. Once installed, though, the token sits in plain
text inside the shortcut on the device: don't re-export or AirDrop an
*installed* copy, and if you suspect it leaked, rotate `api.auth_token` and
re-import.

## What is verified, and what isn't

Brain Text and Brain Share were run end to end against a real server: the
note landed in the inbox with the right body and the right tag, and sending
the same `X-Capture-Key` twice returned the same note id and wrote one file.

**Brain Voice is not verified.** Its upload puts the recording in the request
body (`WFHTTPBodyType: File`), and that path could not be exercised off the
phone — Record Audio needs a microphone and a tap, and feeding a file in via
`shortcuts run -i` never reached the server. Everything around it (the URL,
the headers, the notification) is shared with the two that do work. **Check
it once on the phone**: press the Action Button, speak, and confirm a note
appears in Triage. If nothing arrives, say so — the fix is in `post()`.

## Why they're built by a script

The `.shortcut` format is an undocumented plist of nested token strings and
action-output references, and it is silently lenient: a wrong key doesn't
fail the import, it produces an action with a blank field that only fails
later, on the phone. Generating all three from one file keeps them
consistent and makes a fix a one-line change instead of thirty taps.

Five things learned the hard way here, each of which cost a debugging round
and is now encoded in `build.py`:

- **The Text action is `is.workflow.actions.gettext`**, not `…actions.text`.
  A wrong identifier fails the run with "an action could not be found".
- **Named variables resolve to empty.** Set Variable plus
  `{"Type": "Variable"}` reads back as nothing, so every reference is a
  direct action-output reference — which is also what Apple's own gallery
  workflows use.
- **Replace Text wedges the runner.** Any `is.workflow.actions.text.replace`
  in the chain makes the run hang forever with no error and no request. That
  is why nothing trims the address or the token.
- **Format Date behaves the same way**, so the idempotency key is two random
  numbers rather than a timestamp.
- **A hand-written If is rejected**: it renders as "Please choose a value for
  each parameter in this action" and stops the run. So there are no branches
  at all — no retry, and the share shortcut handles links only.

## No retry, by design

Each capture is a single POST. Shortcuts has no try/catch, no readable HTTP
status code, and no If that survives hand-serialisation, so there is nothing
sound to branch on. Instead the notification shows the server's own reply:
a success reads `{"id": …, "status": "captured"}` and a failure carries the
three-part what/cause/todo sentences. A genuine network failure aborts with
iOS's own alert.

Every request still sends an `X-Capture-Key`, so if you re-run a capture the
server can recognise a duplicate. The cockpit's PWA has a real offline
outbox; the shortcuts deliberately don't reimplement one.

## Photos

Not here. A photo needs the raw-file upload plus an If to tell it from a
link, and neither survives. Use the cockpit's own camera button in the PWA,
which already resizes and uploads. The share shortcut's declared input
classes leave images out, so iOS won't offer it for a photo.

## Testing a change

`shortcuts run` drives a shortcut end to end, but **Shortcuts cannot reach
`127.0.0.1`**, so point a test build at a public URL:

```bash
cloudflared tunnel --url http://127.0.0.1:8765          # prints an https URL
python3 scripts/shortcuts/build.py --test "<that url>" <token> --out /tmp/t
```

`--test` bakes the address and token in and replaces every prompt with a
fixed value, so the shortcut runs unattended. Open each file to import it,
click **Add Shortcut**, then `shortcuts run "Brain Text"`. Never commit a
`--test` build: it holds a token.

Importing needs one GUI click per file — there is no `shortcuts add`
subcommand, and the screen must be unlocked. Run only one `cloudflared` at a
time; a second one silently takes the hostname and health checks fail.
