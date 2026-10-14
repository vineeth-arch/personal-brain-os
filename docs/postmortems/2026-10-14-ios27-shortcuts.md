# Post-mortem: iOS 27 Brain Shortcuts rebuild (2026-10-14)

## Summary

The three hand-limited capture shortcuts (Text, Share, Voice) were rebuilt
and three new ones added (Photo, Screenshot, Ask), all built by the
shortcuts-playground plugin instead of `build.py`. They now branch, show
readable results, and keep failed captures in a Files outbox that resends
safely. The backend gained `ai_note` for on-device Apple Intelligence text.
Merged to main at `e923d36`, deployed to the Mac launchd service.

**Status at close:** code shipped and verified on macOS 27. Phone use is
blocked only by the Cloudflare Access bypass on `cockpit.vineethnair.com`
(user action in the Zero Trust dashboard).

## Timeline

| Time | Event |
|---|---|
| 11:2x | Plan approved after one red-team pass (15 findings folded in) |
| 11:30 | `ai_note` backend done by a Sonnet agent, TDD, 814 tests |
| 11:35 | Preflight: custom domain returns a Cloudflare Access login, not JSON |
| 11:38 | Shortcuts library + group containers are TCC-blocked; Shortcuts app granted click-only |
| 11:40–11:46 | Self-authored probes run on the Mac; two traps found |
| 11:54 | Brain Text canary built by the plugin, clean on first validation |
| 11:55–12:00 | Text test matrix, 6/6 rows pass |
| 12:03–12:12 | Five plugin builders in parallel; Voice, Photo, Ask verified |
| 12:16 | Concurrency test: each capture delivered once |
| 12:19 | Review: heading-injection hole found and fixed |
| 12:21 | Merged, API + watcher restarted, web rebuilt |

## What went wrong, and what we did

1. **The old "If is rejected" belief was wrong.** It came from a missing
   `{"Type": "Variable"}` wrapper on `WFInput`, not from Shortcuts. With the
   wrapper, If and named variables across End If both work. The earlier
   design (no branches, no retry, no photos) was built around a bug.
2. **The plan's 10-per-run outbox cap would have broken every send.**
   *Get Items in Range 1–10* errors when the list is shorter. Found by probe,
   removed before any shortcut was built.
3. **URL Encode silently returned empty** unless its input is a text token
   string. It would have broken Photo's `ai_note` and every Brain Ask query.
   Found by probe.
4. **The plan assumed the user would hand-build Probe27** and that we could
   read it from the Shortcuts database. The database is TCC-protected and the
   Shortcuts app is click-only for automation. Replaced by self-generated
   probes, verified by `shortcuts run`, plus Apple's own gallery workflows in
   WorkflowKit (real `askllm` shapes for client 4018.0.4).
5. **Cloudflare Access fronts the live domain.** Shortcuts can't do a
   browser login. Caught at preflight, before anything depended on it. Still
   open (user action).
6. **Heading injection in AI text** (review, MEDIUM): an `ai_note` containing
   `## Insight` would have read back as the user's own words on an
   `origin: human` note. Fixed by stripping heading markers at read time,
   with a regression test. The tagged-note title no longer comes from AI text.
7. **Test artifacts:** shell-written outbox files are invisible to Shortcuts
   until iCloud indexes them (test-only), and a deliberate poison file had to
   be removed from the real iCloud outbox before phone use.
8. **Brain Screenshot's Mac run hung** on a first-run permission prompt that
   can't be clicked headless. Moved to the phone checklist.
9. **Autosync didn't push the merge**: main sat 6 commits ahead of origin.
   Pushed manually at close.

## What went right

- Preflight before building caught the Access block early.
- Probing each undocumented action in isolation turned two silent failures
  into one-line rules (PROBE27.md) before five builders used them.
- Canary first (Brain Text, full matrix) meant the parallel wave reused a
  proven send/outbox block.
- Write-ahead outbox + server-side `X-Capture-Key` dedupe held under
  server-down, no-network and concurrent-flush tests: every capture arrived
  exactly once.
- No token or tunnel URL leaked into the repo or signed files (checked).

## Open items

| Item | Owner | Where |
|---|---|---|
| Cloudflare Access Bypass app for `api/capture`, `api/search`, `api/health` | User | Zero Trust dashboard |
| Phone install + test list (Share and Screenshot untested headless) | User | final message / README |
| Delete Q27 / T* / DBG / PROBE test shortcuts from the library | User | Shortcuts app |
| `dist/` can go stale if `src/` is edited without re-signing | Deferred | check_shortcuts (g) checks presence only |
| Poison outbox files re-notify until deleted | Deferred | DEFERRED.md |
| Brain Ask synthesized answers | Deferred | DEFERRED.md |

## Lessons

- A hand-written action failing in Shortcuts is a serialization bug until a
  probe proves otherwise. Probe before designing around it.
- Undocumented actions get a 1-action probe run headless, not a guess.
- Preflight the real network path (auth layers in front of the API) first.
- Anything an AI writes into a note goes through a sanitizer at the boundary
  where it becomes markdown.
- Check `git status -sb` after a merge; don't assume autosync pushed.
