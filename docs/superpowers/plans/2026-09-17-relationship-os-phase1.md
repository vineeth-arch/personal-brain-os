# Relationship OS v2.2 · Brain OS Phase 1 Core · Implementation Plan (rev 3: pre-mortem + red-team hardened)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use `- [ ]`. **Authored on Opus 5, executed on Sonnet 5.** Read "Executor protocol" before Task 0. On any red test/bug: superpowers:systematic-debugging first. Every implementer runs under ponytail (shortest diff that works).

**Goal:** Make the Brain OS cockpit (`brain-cockpit/`) run the v2.2 Relationship Doctrine day to day: every touch typed and ledgered, at most 5 payload-carrying touches a morning, quiet rule and contact floor respected, and a composer that runs the Four Reads, lints the draft, and can Hold it.

**Architecture:** Pure-logic Python modules in `pipeline/` (touch log, ledger, queue, linter, Greene helper) computed **on read** from the vault (no nightly job, no `today.json`). `api/people.py` exposes them; the React app gets a person page (`#/people/{id}`) with composer + Greene panel, a Today strip, and queue views on People. Vault is the only store (held drafts are vault files).

**Tech Stack:** Python 3.12 stdlib + FastAPI + pydantic; React 19 + Vite + TS + Tailwind 4. No new dependencies.

**Spec (read-only inputs):** `~/Downloads/files relationship[/` → `RELATIONSHIP-OS-ARCHITECTURE.md` v2.2, `relationship-os-patterns.md` v2.2, `SCHEMA-REFERENCE_1.md` v2.2 (Brain OS), `GREENE-HELPER.md` v1.0. Task 1 copies them into the repo; afterwards executors read repo copies only.

## Context

The v2.2 docs exist only in Downloads. The cockpit is at ~v1.5: 11 proposal types, warmth-stage cadence, flat interaction log (`- date (channel) — note`), a `DraftDrawer` with no linter; no tiers, ledger, payload rule, quiet rule, Hold, Greene panel, person page, or people section on Today. User decision: **Brain OS only; Phase 1 core now; everything else logged in DEFERRED.md** (Handshake handled elsewhere). Rev 3 folds in a 28-item pre-mortem (P#) and a 32-item red-team review against real code (R#).

---

## Executor protocol (Sonnet) — follow literally

1. **Work only in the worktree** created in Task 0 (`/Users/vineethnair/Vibe Code/personal-brain-os/brain-cockpit-ro`, branch `feat/relationship-os-phase1`). Never edit, commit, pull or push in `brain-cockpit/` (main checkout): a LaunchAgent (`com.personalbrainos.sync`, `scripts/sync.sh`) auto-commits and pushes `main` every 15 minutes (R1). Never unload that agent yourself.
2. **Do not improvise scope.** Anything not in this plan → one line in `DEFERRED.md` (Task 13 heading) and continue.
3. **Anchors, not line numbers.** Line numbers are hints from 2026-09-17; `grep -n "<anchor>"` before editing.
4. **TDD as written:** write listed tests → run → confirm FAIL for the stated reason → implement → PASS → regression gate → commit. A test that passes before implementation is broken: fix the test.
5. **Regression gate per task:** `PY=/Users/vineethnair/Vibe\ Code/personal-brain-os/brain-cockpit/.venv/bin/python; $PY -m pytest -q` from the worktree root: no NEW failures vs `docs/superpowers/baseline-2026-09-17.txt`. Frontend tasks also `cd web && npm run build`.
6. **Stop and report (do not guess) when:** (a) a test stays red after 2 systematic-debugging attempts; (b) a Global Constraint would break; (c) a plan interface conflicts with real code in a way P#/R# does not cover; (d) a test outside the task's list fails for a non-obvious reason. Report: what failed, exact output, hypothesis.
7. **Protected tests** — never edit: `api/tests/test_no_send.py`, `pipeline/tests/test_route_conversation.py`. **Existing tests you may edit ONLY where a task names them under "Authorized test edits"**, and only as described. Never weaken any other assertion.
8. **Copy code blocks verbatim** (constants, formats, regexes, UI labels).
9. **Commits:** one per task, message given, in the worktree. Never commit `.env`, `config.json`, `data/`, `graphify-out/`.
10. **No real vault.** Tests use tmp vaults; preview uses `web/mock-api.py`.
11. Reports may be terse (caveman); code, comments, commits, UI copy are normal English.

## Global Constraints

- Dependencies locked (CLAUDE.md §7): backend fastapi, uvicorn, anthropic, pydantic, stdlib; frontend react, vite, typescript, tailwind.
- Nothing auto-sends (CLAUDE.md §4). No send URL in `api/`, `pipeline/`, `scripts/`. Deep links built in the browser; link labels "Open in WhatsApp" / "Open in mail"; the Gmail API button stays "Save Gmail draft". Never "Send".
- Vault = only source of truth; SQLite = events only. `git_commit_vault(vault, msg)` (in `api/notes.py`) after every API write; before any batch write, commit first.
- Append-only person sections; appended lines end with `<!-- bc:… -->`; repeats are no-ops.
- User-facing errors: raise the existing `Envelope(status, what, cause, todo)` pattern in `api/main.py` (grep `Envelope(`). The global `RequestValidationError` handler returns **400** and must not change (R4).
- Frontend follows `DESIGNSYSTEM.md`; design plugins refine within it.
- Interpretations never feed a draft as known; `#sensitive` lines excluded unless owner opts in per touch; `energy` never rendered as a word.
- "Today" in API/pipeline entry points = `datetime.now(config.tzinfo).date()`; pure functions take `today: date`. No `date.today()` in new code.
- Extrapolated numbers are named constants with `# extrapolation (spec §)`.
- Tier names `inner | core | active | wide`. No new streaks, no charts on Home (existing `StreakDots` on Today is left alone and logged, R32).

## Plugin usage

| When | Plugin |
|---|---|
| Task 0 | `superpowers:using-git-worktrees`; `/graphify` on the worktree → `graphify-out/` orientation |
| All tasks | `ponytail`; `caveman` for reports only |
| Waves A and B | `superpowers:dispatching-parallel-agents`, one sub-worktree per agent |
| Tasks 11–12 | build with `impeccable` + `emil-design-eng` + `frontend-design`; Task 12 People restructure with `redesign-existing-projects`; final audit `design-taste-frontend` |
| Red test / bug | `superpowers:systematic-debugging` |
| Between tasks | two-stage review (spec compliance, code quality); reviewer may run on Opus |
| End | `superpowers:finishing-a-development-branch` (merge decision is the user's) |

---

## Risk register (all fixes are already written into the tasks)

| # | Failure if built naively | Fixed in |
|---|---|---|
| R1 | LaunchAgent auto-commits/pushes `main` every 15 min → half-done work pushed, autostash mangles files | Protocol 1, T0 worktree |
| P1 | `Person.sections` strips `bc:` markers → closed actions never close | T2 `raw_sections` |
| R13 | close marker lives in Interaction log, not Next action | T2 `closed` scans all raw sections |
| P2 | ` · ` inside summaries / cite tails break parsing | T3 `format_line`, `_TAIL` |
| R2 | `_V2` regex fails on empty channel (`out · · give_know`) | T3 regex ` ?·` + round-trip test |
| P3 | legacy lines crash parser or count as touches | T3 parse rules, T4 ledger ignores legacy |
| R25 | Triage meeting approval still writes legacy lines → meetings invisible | T9 switches `api/notes.py` attendee logging to `record_touch` |
| P4/R8 | `relationship` list breaks string callers; union writes `['a', 'b']` | T2 keep `relationship: str`, list write `[a, b]` |
| P5/P6 | `wide` cadence None crashes math; untiered notes | T2 `has_cadence`; untiered = legacy behaviour |
| R14 | first heartbeat marks untiered people dormant forever | T4 heartbeat skips untiered; T3 revives cold/dormant |
| R7 | `owner` merge kind spams Updates and reverts cross-device edits during vaultsync | T2 equality + human-origin rule |
| R26 | `new_person_note` drops id/source/origin | T2 key order |
| P14/R9 | reputation_signal table parse; double log write | T1 |
| R10 | reputation_signal `in·other` puts person in owe_reply | T5 owe_reply types `{reply, ask_theirs}` |
| R11 | owner promise close pollutes their reliability read | T3 `close_promise(side=)`, T11 buttons |
| R12 | promise hidden by any later contact (doctrine: never hidden) | T5 promises rule |
| R22 | undated Next action lines vanish | T2/T5 undated → follow_up |
| R23 | Today strip includes Waiting on them | T5 `strip` skips it |
| R24 | Hold only surfaces for existing reply items; flag not paused before 9 am | T5/T9 held handling |
| P7/R17 | static `/api/people/<word>` captured by `{person_id}` (API and mock) | T8, T10 route order |
| P8 | Docker image lacks `docs/` → seeding 500s in prod | T1 `pipeline/seeds/` |
| R28 | `ensure` can't signal it wrote → uncommitted seed | T7 returns `(text, wrote)` |
| R29 | Greene parser steals next situation's line | T7 section-bounded parse |
| P9 | UTC server shifts 9 am Hold / due dates | Global constraint, T9 |
| R3/R4 | contact touch_type vs existing test + global 400 handler | T9 authorized edit, Envelope 422 |
| R5 | new prompt signature breaks `test_people.py` prompt tests | T10 keeps phrases, `rules=""` |
| R6 | digest rewrite breaks 6 existing tests | T13 authorized edits |
| R15 | drawer removal kills PushRow, Gmail draft, stage prompt, e2e steps 9–10 | T11 composer takes Gmail + stage; T12 slim `ProfileDrawer`; e2e 8–10 |
| R16 | client lacks `person(id,{desk})`; `matchMedia` truthy; `personDraft` signature | T10 client, T11 `.matches` |
| R18 | mock can't import `pipeline` | T10 sys.path insert |
| R19 | parallel agents trip each other's gates / git index lock | sub-worktrees per wave agent |
| R20 | e2e targets unnamed UI | T11 fixed accessible names |
| R21 | link labels contradict constraint + e2e | T11 labels, T12 e2e |
| R30 | `test_mcp.py` skipped (no `mcp` in venv) | T10 note in report; update tool-set assertion |
| P10–P28 | remaining pre-mortem items | inline in tasks |

---

## File map (exhaustive)

| File | Task |
|---|---|
| worktree + `docs/superpowers/baseline-2026-09-17.txt` | 0 |
| `SCHEMA-REFERENCE.md` (overwrite), `docs/relationship-os/{RELATIONSHIP-OS-ARCHITECTURE,relationship-os-patterns,GREENE-HELPER}.md`, `pipeline/seeds/{greene-helper,draft-rules}.md`, `pipeline/proposals.py`, `api/notes.py` (`apply_person_proposal`), `web/src/api/types.ts` (`PersonProposalType`), `web/src/screens/Triage.tsx` (labels/outcome) | 1 |
| `pipeline/merge.py`, `pipeline/relationships.py`, `pipeline/proposals.py` (relationship union write) | 2 |
| `pipeline/touchlog.py` (new), `pipeline/proposals.py` (use `format_line`) | 3 |
| `pipeline/ledger.py` (new) | 4 |
| `pipeline/queue.py` (new) | 5 |
| `pipeline/draftlint.py` (new) | 6 |
| `pipeline/greene.py` (new) | 7 |
| `api/people.py`, `api/main.py` | 8 |
| `api/people.py`, `api/held.py` (new), `api/main.py`, `api/notes.py` (attendee logging), `web/src/api/client.ts` + one caller in `People.tsx` | 9 |
| `api/people.py`, `api/main.py`, `scripts/cockpit_mcp.py`, `web/API-CONTRACT.md`, `web/mock-api.py`, `api/tests/test_mock_parity.py` (docstring), `web/src/api/{types,client}.ts`, `web/src/screens/People.tsx` (one call-site compile fix) | 10 |
| `web/src/App.tsx`, `web/src/components/Layout.tsx`, `web/src/screens/Person.tsx` (new), `web/src/components/{Composer,GreenePanel,LintList}.tsx` (new), `web/src/lib/channels.ts` (new) | 11 |
| `web/src/screens/{Today,People,Triage}.tsx`, `web/e2e/run-e2e.mjs` | 12 |
| `pipeline/morning.py`, `DEFERRED.md`, `CLAUDE.md`, `pipeline/README.md`, `api/README.md`, `docs/superpowers/plans/2026-09-17-relationship-os-phase1.md` | 13 |

---

### Task 0: Worktree, baseline, orientation

- [ ] Use `superpowers:using-git-worktrees`: `git -C "/Users/vineethnair/Vibe Code/personal-brain-os/brain-cockpit" worktree add ../brain-cockpit-ro -b feat/relationship-os-phase1`. All later paths are relative to `/Users/vineethnair/Vibe Code/personal-brain-os/brain-cockpit-ro`.
- [ ] `cd web && npm ci` (worktree has no `node_modules`).
- [ ] Baseline: `$PY -m pytest -q 2>&1 | tail -40 > docs/superpowers/baseline-2026-09-17.txt`; append `npm run build` result (pass/fail + first error line). Note in the file that `api/tests/test_mcp.py` is skipped (no `mcp` package, R30).
- [ ] `/graphify` on the worktree excluding `web/node_modules`, `web/dist`, `.venv`, `graphify-out`; add `graphify-out/` to `.gitignore` if absent.
- [ ] Commit `chore: relationship os phase 1 baseline`.

### Task 1: Spec into repo + 8 new proposal types

**Produces (`pipeline/proposals.py`):**
```python
SECTIONS = {  # existing 11 unchanged, then:
    "problem": ("Current state",),
    "goal": ("Future state",),
    "offer": ("Can help with",),
    "intro": ("Interaction log", "Next action"),
    "give_mine": ("Interaction log",),
    "give_theirs": ("Interaction log", "Next action"),
    "important_date": ("Next action",),
    "reputation_signal": ("Interaction log",),  # plus _System/reputation.md, written by api/notes.py
}
REPUTATION_FILE = "_System/reputation.md"
LOG_TOUCH = {  # type -> (direction, touch_type, summary prefix) for the v2 log line
    "intro": ("out", "give_who", ""),
    "give_mine": ("out", "give_know", ""),
    "give_theirs": ("in", "give_theirs", ""),
    "reputation_signal": ("in", "other", "They said about me: "),
}
```
`outcome(p, today)` additions:
| type | due | `line` | `section` |
|---|---|---|---|
| problem / goal / offer | None | `text` | as SECTIONS |
| intro | `date+14` if date else `today+14` | `Intro check-in: {text}` | Next action |
| give_mine | None | v2 log line (below) | Interaction log |
| give_theirs | today | `Thank: {text}` | Next action |
| important_date | `max(today, date-7)` | `Remember date: {text} ({dd Mon})` | Next action |
| reputation_signal | None | v2 log line (below) | Interaction log |

`apply()` rules (R9):
- For types in `LOG_TOUCH` whose final section is **Next action** (intro, give_theirs): append the v2 log line to Interaction log with marker `<!-- {marker_base}:log -->`, then the generic Next action line as today.
- For types whose final section is **Interaction log** (give_mine, reputation_signal): the generic write **is** the v2 log line (no second line), marker `<!-- {marker_base} -->`.
- give_theirs also appends `- {today+30} · Report outcome: {text} {cite}` to Next action with marker `<!-- {marker_base}:outcome -->`.
- v2 log line text in this task (Task 3 swaps to `touchlog.format_line`, identical output): `- {today} · {direction} · · {touch_type} · {prefix}{text} {cite}`.
- Commitment log lines unchanged (legacy, not touches).

`api/notes.apply_person_proposal`: after writing, if type is `reputation_signal`, append `- {today} · [[{person_id}]] {person_name} · {text} <!-- bc:{note_id}:{index}:rep -->` to `REPUTATION_FILE` (create with `---\norigin: human\n---\n\n# Reputation\n\nWhat people actually say I am known for. Read monthly against positioning.\n\n` if missing; skip if marker present). Same vault commit.

`_prompt` additions (one bullet each): problem "a problem they named; include the impact if they stated one"; goal "where they are trying to get to, in their words"; offer "what they are good at, sell, or offered to help with"; intro "an introduction made or promised; date if one was said"; give_mine "something the OWNER gave them: intro, resource, help, referral"; give_theirs "something THEY gave the owner"; important_date "birthday, anniversary or launch date; needs date"; reputation_signal "something they said about the OWNER, or repeated from others". `clean()`: `important_date` without date → dropped (intro without date is kept).

Seeds: `pipeline/seeds/greene-helper.md` = byte copy of GREENE-HELPER.md. `pipeline/seeds/draft-rules.md` = `---\norigin: human\n---\n\n# Draft rules\n\n` + the text of ARCHITECTURE §A9 from the line after `## A9.` up to (not including) `### Reference scripts`.

- [ ] Step 1: copy the 3 docs to `docs/relationship-os/`, overwrite `SCHEMA-REFERENCE.md` with `SCHEMA-REFERENCE_1.md`, create seeds. Run `$PY -m pytest pipeline/tests/test_proposals.py::test_sections_mirror_the_schema_table pipeline/tests/test_route_conversation.py -q` → proposals FAILS (8 missing keys); route_conversation PASSES (if not → stop, P15).
- [ ] Step 2: tests — `pipeline/tests/test_proposals.py`: `test_give_theirs_logs_in_touch_thanks_today_reports_in_30`, `test_intro_logs_give_who_and_checks_in_after_14_days`, `test_intro_without_date_uses_today_plus_14`, `test_important_date_reminds_a_week_before`, `test_important_date_without_date_is_dropped`, `test_problem_lands_in_current_state`, `test_give_mine_writes_exactly_one_log_line`, `test_reputation_signal_writes_exactly_one_log_line`; `pipeline/tests/test_seeds.py::test_greene_seed_matches_docs_copy`; `api/tests/test_person_proposals.py::test_reputation_signal_appends_to_reputation_file_once`. Run → FAIL.
- [ ] Step 3: implement → PASS → regression gate.
- [ ] Step 4: TS — `PersonProposalType` += 8; `PROPOSAL_LABEL`: problem "Problem", goal "Goal", offer "Can help with", intro "Intro", give_mine "I gave", give_theirs "They gave", important_date "Date", reputation_signal "What they say about me"; `DATED` += `"intro", "important_date"`; `outcomeSentence`: problem `→ Current state`, goal `→ Future state`, offer `→ Can help with`, intro `→ Log · check in on {date+14 or today+14}`, give_mine `→ Log · counts as a give`, give_theirs `→ Log · thank today · report outcome {today+30}`, important_date `→ Remind {date-7}`, reputation_signal `→ Reputation page · log`. `cd web && npm run build` green.
- [ ] Commit `feat(proposals): v2.2 proposal types, schema v2.2 and seeds into repo`.

### Task 2: Person v2 + merge kinds

**Produces (`pipeline/relationships.py`):**
```python
TIERS = ("inner", "core", "active", "wide")
TIER_CAP = {"inner": 15, "core": 35, "active": 100}          # extrapolation (A4)
TIER_CADENCE = {"inner": 14, "core": 30, "active": 90}        # wide: no cadence (A4)
FAMILY_FRIEND = frozenset({"family", "friend"})
OWNER_ONLY = ("tier", "energy", "known_for", "recall_trigger", "conversation_stage",
              "buyer_role", "fit", "list_of_20")
_ACTION = re.compile(r"^- (\d{4}-\d{2}-\d{2}|open) · (.+)$")

@dataclass
class NextAction:
    due: date | None      # None for "- open · …" and for undated lines
    text: str             # readable: cite, marker and the "date · " prefix removed
    key: str              # marker body (e.g. "20260917101500:3") if the raw line has <!-- bc:X -->, else sha1(stripped raw line)[:10]
    closed: bool
    dated_format: bool    # True when the line matched _ACTION

# Person gains (defaults blank/False/None/{}/[]):
tier: str; relationships: list[str]; preferred_channel_field: str; language: str
last_give: date|None; last_ask: date|None; quiet_until: date|None; energy: str
known_for: str; recall_trigger: str; dates: dict[str, str]; referred_by: str
list_of_20: bool; conversation_stage: str; buyer_role: str; fit: str; created: date|None
raw_sections: dict[str, str]     # same split as sections, NOT passed through _readable (P1)

# existing field relationship: str is set to ", ".join(relationships) (P4)
effective_cadence -> int         # cadence_days > TIER_CADENCE[tier] > STAGE_CADENCE_DAYS[stage] > DEFAULT_CADENCE_DAYS
has_cadence -> bool              # False only when tier == "wide" and not cadence_days
commercial -> bool               # not (relationships and set(relationships) <= FAMILY_FRIEND)  (P27)
def parse_list(raw: str) -> list[str]         # "[a, b]" | "a, b" | "a" | "" | "[]" → lowercase, stripped, non-empty
def format_list(items: list[str]) -> str      # "[a, b]" ; [] → "[]"
def next_actions(person) -> list[NextAction]
```
`next_actions`: iterate non-empty lines of `raw_sections["Next action"]` that start with `- `; `_ACTION` match → due/`open`; otherwise `due=None, dated_format=False` (R22). `closed = f"<!-- bc:close:{key} -->" in "\n".join(person.raw_sections.values())` (R13).
`parse_person`: `relationships = parse_list(fm["relationship"])`; `dates` via `parse_channels` (keys lowercased); `list_of_20` iff `true`; dates via `_parse_date`. `going_cold`: first line `if not self.has_cadence: return False`.
`new_person_note` frontmatter order (R26): `id, type, created, source, origin`, then `relationship: [], company, channels, preferred_channel, language, tier, cadence_days, last_contact, last_give, last_ask, quiet_until, energy, known_for, recall_trigger, dates: {birthday:, anniversary:}, referred_by, list_of_20: false, warmth_stage: identified, conversation_stage, buyer_role, fit, dex_id, dex_deeplink, handshake_id, outreach_id, status: active`, then `categories: [], subjects: [], tags: []`. Body sections: Context, Current state, Future state, Needs, Can help with, How they communicate, Facts, Interpretations, Interaction log, Next action, Updates.

**`pipeline/merge.py`:** `FIELD_KINDS` add `"relationship": "union", "last_give": "forward", "last_ask": "forward", "quiet_until": "forward", "referred_by": "set_once"`, and each OWNER_ONLY key → `"owner"` (literal list, no import). Branch (R7):
```python
if kind == "owner":
    if str(current or "") == str(new_value or ""):
        return fm, None                      # no-op, no Updates spam (vaultsync)
    if origin == "human":
        fm[key] = new_value                  # the owner's own edit arriving via sync
        return fm, None
    return fm, f"- {today} · {key}: {current or '(blank)'} → {new_value}? ({source}, {origin})"
```
**`pipeline/proposals.py` (R8):** in the `person_update` branch, when `p["field"] == "relationship"`: `merged = relationships.parse_list(current) + [v for v in relationships.parse_list(p["value"]) if v not in current_list]`; write `_set_front(head, "relationship", relationships.format_list(merged))`; no Updates suggestion.

- [ ] Tests (`pipeline/tests/test_relationships.py`, `test_merge.py`, `test_proposals.py`): `test_tier_cadence_beats_stage_default`, `test_explicit_cadence_beats_tier`, `test_wide_tier_never_goes_cold`, `test_blank_tier_keeps_legacy_stage_cadence`, `test_relationship_list_parses_shapes_and_joins`, `test_family_only_is_not_commercial`, `test_empty_relationship_is_commercial`, `test_raw_sections_keep_markers`, `test_next_actions_dated_open_and_undated`, `test_next_action_key_prefers_marker`, `test_next_action_closed_by_marker_in_interaction_log`, `test_dates_inline_map_parses`, `test_new_note_frontmatter_order_and_sections`, `test_owner_only_ai_suggests_never_sets`, `test_owner_only_equal_value_no_suggestion`, `test_owner_only_human_origin_applies`, `test_quiet_until_is_forward_only`, `test_referred_by_set_once`, `test_relationship_update_writes_bracket_list`. FAIL → implement → PASS.
- [ ] Regression gate — watch `test_merge.py`, `test_vaultsync.py`, `test_dex_pull.py`, `api/tests/test_people.py::test_quick_add_writes_a_schema_correct_person_note` (must stay green unchanged).
- [ ] Commit `feat(people): v2.2 person fields, tier cadence, merge kinds`.

### Task 3: Touch log v2

**Produces (`pipeline/touchlog.py`, new):**
```python
OUT_TYPES = ("remember", "give_know", "give_who", "celebrate", "keep_promise", "keep_promise_late",
             "kind_truth", "thank", "invite", "ask", "presence")
IN_TYPES = ("reply", "give_theirs", "ask_theirs", "promise_kept", "promise_late", "promise_dropped", "other")
GIVE_TYPES = frozenset({"remember", "give_know", "give_who", "celebrate", "keep_promise",
                        "keep_promise_late", "kind_truth", "thank"})
FLOOR_EXEMPT = frozenset({"keep_promise", "keep_promise_late", "thank", "celebrate"})
PROMISE_IN = frozenset({"promise_kept", "promise_late", "promise_dropped"})
QUIET_MULTIPLIER = 2          # extrapolation (A11.2)
QUIET_FALLBACK_DAYS = 30

@dataclass
class Touch:
    day: date; direction: str; channel: str; touch_type: str; summary: str
    greene: str; requested: bool; legacy: bool

_V2 = re.compile(r"^- (\d{4}-\d{2}-\d{2}) · (out|in) · ([a-z]*) ?· ([a-z_]+) · (.*)$")
_LEGACY = re.compile(r"^- (\d{4}-\d{2}-\d{2})(?: \(([a-z]+)\))? — (.*)$")
_TAIL = re.compile(r"[ \t]*(?:· derived-from:: \[\[[^\]]*\]\] \([^)]*\))?[ \t]*(?:<!-- bc:[^>]*-->)?[ \t]*$")
_GREENE = re.compile(r"^greene:3\.\d{1,2}$")

def parse_log(raw_section: str) -> list[Touch]
def format_line(day, direction, channel, touch_type, summary, greene="", requested=False) -> str
def record_touch(text, person, *, day, direction, channel, touch_type, summary,
                 greene="", requested=False, close_key="") -> str
def close_promise(text, person, *, key, result, side, day) -> str
```
`format_line`: `channel.lower().strip()`; summary: newlines→space, ` · `→`, `, strip, cut to 160 chars; output `f"- {day.isoformat()} · {direction} · {channel} · {touch_type} · {summary}"` + (`f" · greene:{greene}"` if greene) + (`" · requested"` if requested). With empty channel this yields `out ·  · type` (two spaces) — `_V2` accepts both one and two spaces (R2).
`parse_log`: for each line: strip `_TAIL`; `_V2` match → split group 5 on ` · `, pop trailing segments that are `requested` or match `_GREENE` (strip `greene:`), rejoin rest with ` · ` → Touch(legacy=False). Else `_LEGACY` match and note does not start with `I promised`/`They promised` → Touch(direction="out", touch_type="other", legacy=True, channel=group2 or ""). Else ignore. Never raises.
`record_touch`:
1. Validate type ∈ OUT_TYPES if out else IN_TYPES; greene ∈ `""` or `^3\.\d{1,2}$` → else `ValueError`.
2. `line = format_line(...)`; `text = relationships.append_marked(text, "Interaction log", line, relationships._marker(person.id, "touch", line))`. If marker already present, return text unchanged (idempotent).
3. Frontmatter (via `merge.apply_field` forward kind + `relationships._replace_field`): `last_contact = day`; give type → `last_give = day`; `ask` → `last_ask = day`; if current status in {cold, dormant} → `status: active` (R14).
4. Quiet rule: if `direction == "out"` and type ∉ FLOOR_EXEMPT and not requested: parse the new log, keep `not legacy and touch_type not in PROMISE_IN`; if the last two are both `out`, type ∉ FLOOR_EXEMPT, not requested → `quiet_until = day + timedelta(QUIET_MULTIPLIER * (person.effective_cadence if person.has_cadence else QUIET_FALLBACK_DAYS))` via forward-only.
`close_promise(side, result)` (R11):
- `side="theirs"`, result ∈ kept|late|dropped → append `format_line(day, "in", "", f"promise_{result}", action_text)` to Interaction log with marker `<!-- bc:close:{key} -->`.
- `side="mine"`, result ∈ kept|late → `record_touch(..., direction="out", touch_type="keep_promise" if kept else "keep_promise_late", summary=action_text, close_key=key)`. `record_touch` gains optional `close_key: str = ""`; when set, the touch line's marker is `<!-- bc:close:{close_key} -->` instead of the hash marker, so one line both logs the touch and closes the action.
- `side="mine"`, result `dropped` → append `- {day} · dropped: {action_text} <!-- bc:close:{key} -->` to Next action (not a touch).
- unknown key → `ValueError`. Idempotent via marker.
Leave `relationships.log_contact` unchanged. Swap Task 1's inline log strings in `proposals.apply` to `touchlog.format_line` (import inside the function).

- [ ] Tests (`pipeline/tests/test_touchlog.py`): `test_round_trip_with_empty_channel`, `test_round_trip_summary_with_separator`, `test_tags_parse_greene_and_requested`, `test_cite_and_marker_tail_stripped`, `test_legacy_contact_line_is_legacy_touch`, `test_legacy_promise_line_ignored`, `test_handshake_heading_block_ignored`, `test_task1_proposal_lines_parse` (feed a note produced by `proposals.apply` for give_mine/give_theirs/intro/reputation_signal), `test_give_sets_last_give_and_contact`, `test_ask_sets_last_ask`, `test_touch_revives_cold_and_dormant`, `test_second_unprompted_out_sets_quiet_2x_cadence`, `test_wide_uses_fallback_window`, `test_reply_between_resets_quiet_count`, `test_thank_and_requested_do_not_count`, `test_promise_close_in_lines_do_not_break_quiet_count`, `test_legacy_lines_never_trigger_quiet`, `test_in_type_on_out_raises`, `test_bad_greene_code_raises`, `test_close_theirs_writes_promise_in_and_closes`, `test_close_mine_kept_writes_keep_promise_touch_and_closes`, `test_close_mine_dropped_is_not_a_touch`, `test_record_touch_idempotent`. FAIL → implement → PASS → gate.
- [ ] Commit `feat(touchlog): v2 interaction log, give/ask dates, quiet rule, promise close`.

### Task 4: Ledger

**Produces (`pipeline/ledger.py`, new):**
```python
COLD_X, DORMANT_X = 1.5, 3.0                                    # A4
FLOOR_DAYS = {"inner": 0, "core": 10, "active": 30, "": 0}      # extrapolation (A11.2); "" untiered (P6)
NEW_RELATIONSHIP_DAYS = 90
ASK_MIN_GIVES, ASK_MIN_DAYS = 3, 90                             # extrapolation (A6)
ONE_WAY_GIVES = 8

def counts(touches, today) -> dict          # gives90 asks90 received90 gives180 asks180 received180; legacy ignored; received = in·give_theirs
def reliability(touches) -> dict            # {"kept","late","dropped"} from in·promise_* only
def reliability_line(r) -> str              # "their promises: 5 kept · 1 late · 2 dropped"; "" when all zero
def status_for(person, today) -> str
def inside_floor(person, touches, today) -> bool
def is_quiet(person, touches, today) -> bool
def ask_allowed(person, touches, today) -> bool
def flags(touches, today) -> list[str]
def advance_statuses(people, today) -> list[tuple[Path, str]]
```
- `status_for`: `dormant` stays; `not has_cadence` or no last_contact → current status; ratio = days_since / effective_cadence; ≥3 dormant, ≥1.5 cold, else active.
- `inside_floor`: False if `created` within 90 days of today, or `list_of_20`, or tier ∈ {inner, ""}; tier `wide` → True; else True iff the latest out touch (legacy included) that is not FLOOR_EXEMPT and not requested is within `FLOOR_DAYS[tier]` days.
- `is_quiet`: `quiet_until and quiet_until > today` and the latest touch with `not legacy and touch_type not in PROMISE_IN` has direction `out`.
- `ask_allowed`: `person.commercial` and gives (non-legacy) since last ask ≥ 3 and (`last_ask is None` or ≥ 90 days).
- `flags`: `"i_only_take"` when asks180 > 0 and asks180 ≥ gives180; `"one_way_street"` when gives180 ≥ 8 and no `in` touch (non-legacy, not PROMISE_IN) in 180 days.
- `advance_statuses` (R14): skip people with `tier == ""` or `sample`; return `(path, new_text)` only where `status_for != status`.
- [ ] Tests (`pipeline/tests/test_ledger.py`): one per function plus `test_legacy_never_counts_as_give`, `test_untiered_has_no_floor`, `test_new_relationship_exempt_from_floor`, `test_list_of_20_exempt`, `test_wide_always_inside_floor`, `test_family_never_ask_allowed`, `test_quiet_lifts_when_they_reply`, `test_promise_close_does_not_lift_quiet`, `test_dormant_at_3x`, `test_heartbeat_skips_untiered`. FAIL → implement → PASS → gate.
- [ ] Commit `feat(ledger): gives/asks, reliability read, floor, quiet, status`.

### Task 5: Queue engine + payload rule

**Produces (`pipeline/queue.py`, new):**
```python
VIEWS = ("owe_reply", "promises", "ask_about", "celebrate", "follow_up", "waiting_on_them", "reconnect")
LABELS = {"owe_reply": "I owe a reply", "promises": "Promises I made", "ask_about": "Ask about",
          "celebrate": "Celebrate", "follow_up": "Follow up today", "waiting_on_them": "Waiting on them",
          "reconnect": "Reconnect"}
DAILY_CAP = 5                  # extrapolation (A4)
REPLY_FLAG_DAYS = 2            # 48 h at date resolution
CELEBRATE_WINDOW_DAYS = 7
OWE_TYPES = frozenset({"reply", "ask_theirs"})                # R10
BYPASS_QUIET = frozenset({"owe_reply", "promises", "celebrate"})
STRIP_SKIP = frozenset({"waiting_on_them"})                   # R23
TIER_ORDER = {"inner": 0, "core": 1, "active": 2, "": 3, "wide": 4}

@dataclass
class Item:
    person_id: str; name: str; tier: str; queue: str; touch_type: str
    payload: str; source_key: str; due: str | None; channel: str
    flagged: bool = False; held: bool = False

@dataclass
class Held:
    person_id: str; touch_type: str; ready: bool

def payload_for(person, touches, today) -> tuple[str, str, str] | None
def build(people, today, held: list[Held] = ()) -> dict[str, list[Item]]
def strip(queues) -> tuple[list[Item], int]
def to_dict(item: Item) -> dict
```
`settled(action, person)` = `action.due is not None and person.last_contact is not None and person.last_contact >= action.due`.
Next action classification (open = `not action.closed`; prefixes are case-sensitive, applied to `action.text`):
| prefix | view | touch_type | included when |
|---|---|---|---|
| `I promised:` | promises | keep_promise | open and (due is None or due ≤ today) — **no last_contact test** (R12) |
| `Ask how it went:` | ask_about | remember | open, due ≤ today, not settled |
| `Check in — they promised:` | ask_about | remember | open, due ≤ today, not settled |
| `Check in — they promised:` | waiting_on_them | remember | open and (due is None or due > today) |
| `Congratulate:` / `Remember date:` | celebrate | celebrate | open, due ≤ today, not settled |
| `Thank:` / `Report outcome:` | celebrate | thank | open, due ≤ today, not settled |
| `Intro check-in:` | follow_up | give_who | open, due ≤ today, not settled |
| any other dated line | follow_up | remember | open, due ≤ today, not settled |
| undated non-`open` line (R22) | follow_up | remember | open and `relationships.commitment_due(person, today)` |
Other sources:
- **owe_reply:** latest touch with `not legacy and touch_type not in PROMISE_IN` is `in` with type ∈ OWE_TYPES → Item payload = that touch summary; `flagged = (today - day).days >= REPLY_FLAG_DAYS` unless the person has any Held (R24).
- **held (R24):** every `Held` with `ready=True` → owe_reply Item `payload="Held draft: still want to send this?"`, `touch_type=held.touch_type`, `held=True`, `flagged=False`, placed first; if an owe_reply item for that person exists, replace it.
- **celebrate from dates:** each `dates` value parsed as `YYYY-MM-DD` or `MM-DD` (else ignored, P19); next occurrence within 0–7 days → payload `Birthday on {d Mon}` / `Anniversary on {d Mon}` / `{key} on {d Mon}`, touch_type celebrate, source_key `date:{key}`.
- **reconnect:** `person.has_cadence and ledger.status_for(...) != "dormant" and person.going_cold(today)` → `payload_for` in order: (1) oldest open next action (any prefix) → its view's touch_type (2) *matcher — deferred, skip* (3) date within 7 days → celebrate (4) latest `in·give_theirs` touch with no later `out·thank` → thank; none and tier inner → `("presence", "", "")`; none otherwise → Item with `payload=""`, touch_type `""`.
- **Drop rule:** for a person with `ledger.is_quiet or ledger.inside_floor`, remove their items from views ∉ BYPASS_QUIET.
- **Sort within a view:** held first, flagged desc, due asc (None last), TIER_ORDER, name.
- **`strip`:** iterate VIEWS in order skipping STRIP_SKIP; skip reconnect items whose payload is `""` and touch_type is not `presence`; one item per person (first wins); stop at DAILY_CAP; `overflow` = number of distinct remaining persons that would otherwise have qualified.
- [ ] Tests (`pipeline/tests/test_queue.py`): `test_strip_caps_at_five_and_counts_overflow`, `test_one_row_per_person_in_strip`, `test_strip_never_includes_waiting_on_them`, `test_owe_reply_outranks_reconnect`, `test_reputation_signal_does_not_create_owe_reply`, `test_reply_older_than_two_days_flagged`, `test_ready_hold_creates_owe_reply_first_for_non_reply_draft`, `test_unready_hold_pauses_flag_only`, `test_promise_stays_after_later_contact_until_closed`, `test_undated_action_follow_up_when_commitment_due`, `test_reconnect_without_payload_listed_not_stripped`, `test_inner_without_payload_gets_presence`, `test_quiet_hidden_from_reconnect_but_promise_shows`, `test_floor_hides_core_touched_5_days_ago`, `test_closed_theirs_promise_leaves_queue`, `test_birthday_mm_dd_within_7_days`, `test_bad_birthday_format_ignored`, `test_future_their_promise_is_waiting`, `test_wide_never_in_reconnect`. FAIL → implement → PASS → gate.
- [ ] Commit `feat(queue): seven views, payload rule, cap of five`.

### Task 6: Draft linter + Anti-Seducer — **Wave A (sub-worktree), parallel with Task 7**

**Produces (`pipeline/draftlint.py`, new, stdlib only):**
```python
@dataclass
class Lint:
    code: str; start: int; end: int; snippet: str; message: str

SOFTENERS = ("no rush", "no pressure", "whenever you get a chance", "whenever works", "just checking in",
             "touching base", "circling back", "hope this finds you well", "sorry to bother", "if you get time")
WA_MAX_LINES, EMAIL_MAX_WORDS = 4, 120        # A9.13
SEDUCER_ORDER = ("windbag", "moraliser", "tightwad", "reactor", "bumbler", "pushy")
def lint(text: str, *, channel: str = "whatsapp", tier: str = "", touch_type: str = "",
         quiet: bool = False) -> dict   # {"lints": [asdict(Lint)], "seducer": [str]}; never raises; blank → both empty
```
Rules (all `re.I`, word-bounded where words; heuristics commented `# extrapolation`):
| code | trigger | message |
|---|---|---|
| softener | any SOFTENERS | `say the date and why` |
| no_date | (touch_type ∈ {ask, invite} or `let me know\|get back to me\|when you can`) and none of: `\b(mon\|tue\|wed\|thu\|fri\|sat\|sun)[a-z]*\b`, `\b(jan\|feb\|mar\|apr\|may\|jun\|jul\|aug\|sep\|oct\|nov\|dec)[a-z]*\b`, `\b\d{1,2}(st\|nd\|rd\|th)\b`, `\b\d{1,2}[/-]\d{1,2}\b`, `\b(today\|tomorrow\|tonight\|this week\|next week)\b`, `\bby \d` | `Add a date` (span 0..len) |
| em_dash | `—` or ` – ` | `no em dashes` |
| emoji | `[\U0001F300-\U0001FAFF☀-➿]` | `no emojis` |
| length | whatsapp: > 4 non-empty lines; email: > 120 words excluding a first line starting `Subject:` | `say less` |
| justify | ≥ 3 consecutive sentences (split `(?<=[.!?])\s+`) each containing `because\|since\|the reason\|that's why\|which is why\|to be fair` | `state it once, then offer a choice` |
| complaint | tier != "inner" and `exhausted\|swamped\|frustrat\w*\|nightmare\|fed up\|so busy\|annoying\|can't deal` | `keep this for the inner circle` |
| own_win | `we launched\|i closed\|we won\|just signed\|proud to announce\|excited to share\|finally got` and none of `thanks to\|because of you\|\byour\b\|months\|rejected\|struggle\|\bafter\b` | `name the struggle or credit someone` |
| favours | `after everything\|i've always helped\|you owe me\|i did for you\|return the favou?r\|remember when i helped` | `appeal to what they gain` |
| self_opinion | touch_type == "kind_truth" and none of `you know\|your call\|up to you\|correct me\|you're right that\|i know you meant` | `leave the decision with them` |
Seducer chips (in SEDUCER_ORDER, dedup): windbag ← length; moraliser ← `you should have\|you need to\|you ought to\|you must`; tightwad ← own_win; reactor ← `text.count("!") >= 2` or `\b[A-Z]{4,}\b` (case-sensitive); bumbler ← ≥ 2 total matches of `sorry\|apologi\w*\|just wanted\|maybe\|hopefully\|i think perhaps`; pushy ← quiet or (touch_type == "ask" and none of `if not\|no problem\|your call\|if nobody`).
- [ ] Tests (`pipeline/tests/test_draftlint.py`): one positive + one negative per rule and per chip; `test_blank_text_empty_result`; `test_reference_remember_script_is_clean` (`"Rohan, Aarav's board results were due this week. How did he do?"`, touch_type remember, tier core); `test_reference_kind_truth_script_is_clean` (A11.5 kind-truth script text, touch_type kind_truth); `test_spans_point_at_snippet`. FAIL → implement → PASS → gate (in sub-worktree).
- [ ] Commit `feat(draftlint): A9 linter, presence lints, anti-seducer chips`.

### Task 7: Greene helper — **Wave A (sub-worktree), parallel with Task 6**

**Produces (`pipeline/greene.py`, new):**
```python
HELPER_FILE = "_System/greene-helper.md"
RULES_FILE = "_System/draft-rules.md"
SEEDS = Path(__file__).parent / "seeds"
PRESETS = {"kind_truth": ["3.4", "3.15"], "ask": ["3.11"], "keep_promise_late": ["3.9"],
           "celebrate": ["3.5"], "thank": ["3.6"]}

@dataclass
class Situation:
    code: str; title: str; happening: str; trap: str; move: str; line: str

def parse(md: str) -> list[Situation]
def ensure(vault_path: Path, name: str = HELPER_FILE) -> tuple[str, bool]   # (text, wrote) — R28
def presets(touch_type: str, relationships: list[str]) -> list[str]
def reads(person, touches) -> dict   # {"pride": str, "record": str}
```
- `parse` (R29): split md into blocks at lines matching `^### (3\.\d{1,2}) (.+)$`; each block ends at the next `^#{2,3} ` line; inside a block take `^\*\*Actually happening:\*\* (.+)$`, `^\*\*Trap:\*\* (.+)$`, `^\*\*Move:\*\* (.+)$`, first `^> (.+)$`; missing → `""`. Never raises.
- `ensure`: vault file missing or blank → copy `SEEDS / Path(name).name`, return `(text, True)`; else `(text, False)`. Never overwrites non-blank. Does not commit (callers do).
- `presets`: `list(PRESETS.get(touch_type, []))` + `["3.12"]` if `"mentor" in relationships` and not already present.
- `reads`: pride = first line of `person.sections["Interpretations"]` whose text (after `- ` and an optional `YYYY-MM-DD · ` prefix) starts with `pride:`, prefix removed, stripped; record = `ledger.reliability_line(ledger.reliability(touches))`.
- [ ] Tests (`pipeline/tests/test_greene.py`, fixture `pipeline/seeds/greene-helper.md`): `test_parses_eighteen_situations_in_order`, `test_price_pushback_fields`, `test_situation_3_13_line_is_its_own`, `test_missing_line_does_not_steal_next`, `test_presets_ask_and_mentor`, `test_pride_from_interpretations_only`, `test_ensure_seeds_when_missing_reports_wrote`, `test_ensure_never_overwrites_owner_edit`. FAIL → implement → PASS → gate.
- [ ] Commit `feat(greene): situation lookup, presets, four reads`.
- [ ] **Wave A merge:** merge both sub-worktree branches into `feat/relationship-os-phase1`, remove sub-worktrees, run full gate.

### Task 8: API read endpoints

**Files:** `api/people.py`, `api/main.py` (anchor `# ---- people (Relationship OS)`), new `api/tests/test_relationship_os.py` (reuse `Server`, `env`, `TOKEN` from `api/tests/test_api.py`; local `_person_v2(folder, name, id, **fields)` fixture writing v2 frontmatter + sections).

Register directly **after** `@app.get("/api/people/voice")` / `@app.post("/api/people/voice")` and **before** `@app.get("/api/people/{person_id}")` (P7):
| Route | Response |
|---|---|
| `GET /api/people/today` | `{strip: Item[], overflow: int, queues: {view: Item[]}, labels: LABELS, tiers: {"inner": {"count", "cap"}, "core": {...}, "active": {...}}, untiered: int}` (held list from `api/held.py` once T9 lands; in T8 pass `[]`) |
| `GET /api/people/greene` | `{situations: Situation[]}`; `ensure` → if wrote, `git_commit_vault(vault, "api: seeded greene-helper.md")` |
| `POST /api/people/lint` | body `LintBody{text: str, channel: str = "whatsapp", person_id: str = "", touch_type: str = ""}` → `draftlint.lint` with person's tier and `is_quiet` when `person_id` resolves |

`people.summary()` adds `tier, relationships, known_for, status_computed, quiet_until, list_of_20` (keep all existing keys, `relationship` stays a string).
`people.detail(vault_path, person_id, today=None, desk=False)` adds:
```
known_for, recall_trigger, language, preferred_channel, commercial, dates,
working_together: {conversation_stage, buyer_role, fit, no_economic_buyer} | None   # None when not commercial; no_economic_buyer = buyer_role not in ("", "economic")
ledger (counts), reliability, reliability_line, flags, inside_floor,
quiet: {"until": iso, "line": f"Quiet until {d} {Mon}: two messages unanswered. Nothing to do."} | None,
next_actions: [{due, text, key, closed, view}], touches: last 20 (newest first, dicts),
current_state, future_state, can_help, how_they_communicate, updates,
reads, presets (greene.presets("", relationships)), energy (only when desk)
```
`person_detail` route gains `desk: int = 0` query param passed as `desk=bool(desk)` through `_person_or_404` (R16). `detail()` writes nothing.
- [ ] Tests: `test_static_people_routes_not_captured_by_id`, `test_today_strip_capped_and_labelled`, `test_detail_has_v2_blocks`, `test_detail_hides_energy_without_desk`, `test_family_detail_has_no_working_together`, `test_lint_uses_person_tier_for_complaint`, `test_greene_seeds_vault_once_and_commits`, `test_detail_is_read_only` (file bytes unchanged). FAIL → implement → PASS → gate.
- [ ] Commit `feat(api): relationship os read endpoints`.

### Task 9: API write endpoints

**Files:** `api/people.py`, `api/held.py` (new), `api/main.py`, `api/notes.py` (attendee logging), `web/src/api/client.ts` (`logContact` only), `web/src/screens/People.tsx` (the one `logContact` call only).

**Authorized test edits:** `api/tests/test_people.py::test_logging_contact_resets_the_counter_and_commits` — add `"touch_type": "remember"` to the request body; nothing else (R3). If `api/tests/test_approve_attendees.py` asserts the legacy line shape, update only that expected line to the v2 shape `· in ·  · other ·` (R25).

| Route | Body → response | Behaviour |
|---|---|---|
| `POST /api/people/{id}/contact` | `ContactBody{note: str = "", channel: str = "", direction: str = "out", touch_type: str = "", greene: str = "", requested: bool = False}` → `{**summary, suggest_stage}` | blank touch_type or `ValueError` from `record_touch` → `Envelope(422, "That touch wasn't logged.", "The touch type is missing or isn't one the log knows.", "Pick a touch type above the draft, then log it again.")` (R4). Summary text: `note or "Reached out."`. Keep `suggest_stage = relationships.next_stage(warmth_stage)` for `out`, `None` for `in`. Commit message `f"api: logged contact with {name}"` (keeps "logged contact", R3). |
| `POST /api/people/{id}/promise` | `{key: str, result: str, side: str}` → detail | `touchlog.close_promise`; ValueError → `Envelope(404, "That promise isn't open anymore.", "It was closed already or the note changed.", "Reload the page to see the current promises.")`; commit |
| `POST /api/people/{id}/owner` | `{field: str, value: str}` → `{**detail, warning}` | field ∈ `OWNER_ONLY ∪ {"dates", "how_they_communicate"}` else 422 Envelope. Validation: tier ∈ TIERS∪{""}; energy ∈ {gives, neutral, drains, ""}; fit ∈ {ideal, good, poor, unknown, ""}; buyer_role ∈ {economic, influencer, user, gatekeeper, unknown, ""}; conversation_stage ∈ {none, probative, qualifying, value, closing, delivering, past, ""}; list_of_20 ∈ {"true","false"}; dates = JSON object with keys birthday/anniversary, values `MM-DD`/`YYYY-MM-DD`/"" → written as `{birthday: 03-14, anniversary: }`. Frontmatter: `merge.owner_change` → `_replace_field` + append history line to `## Updates` with marker. `how_they_communicate`: append `- {today} · {value}` with hash marker. Warnings: tier over TIER_CAP → `f"{tier} is {n} of {cap}. Who moves down a tier?"`; list_of_20 count > 20 → `f"List of 20 has {n}. Who comes off?"`. Commit |
| `POST /api/people/{id}/hold` | `{text, channel, touch_type}` → `{until}` | `api/held.py::hold(vault, person_id, text, channel, touch_type, now)`; `person_id` must match `^\d{14}$` else 404 Envelope (P22). File `_System/held/{id}.md`: `---\norigin: human\nperson_id: {id}\nheld_until: {(now + 1 day) at 09:00 in config.tzinfo, isoformat}\nchannel: {channel}\ntouch_type: {touch_type}\n---\n\n{text}\n`; overwrite allowed; commit |
| `DELETE /api/people/{id}/hold` | → `{ok: true}` | delete if exists; commit |
| `GET /api/people/held` (static block, P7) | → `{items: [{person_id, text, channel, touch_type, held_until, ready}]}` | `ready = now >= held_until`; unreadable file skipped + logged |
`GET /api/people/today` now passes `[queue.Held(i.person_id, i.touch_type, i.ready) for i in held.list_items(...)]`.
`api/notes.py` attendee approval (grep `relationships.log_contact`): replace with `touchlog.record_touch(text, person, day=today, direction="in", channel="", touch_type="other", summary=note_line)`, same write/commit flow (R25).
Client: `logContact(id, body: {note: string; channel: string; direction: "out" | "in"; touch_type: string; greene?: string; requested?: boolean})`; People.tsx caller passes `direction: "out", touch_type: "presence"` with comment `// ponytail: compile fix only, caller removed in Task 12`.
- [ ] Tests: `test_contact_blank_touch_type_three_part_422`, `test_contact_writes_v2_line_and_last_give`, `test_contact_keeps_suggest_stage_and_commit_message`, `test_contact_in_reply_lifts_quiet`, `test_promise_close_theirs_updates_reliability_line`, `test_promise_close_mine_logs_keep_promise`, `test_owner_tier_edit_records_history`, `test_owner_rejects_non_owner_field`, `test_owner_dates_written_as_inline_map`, `test_tier_cap_warning`, `test_hold_writes_vault_file_and_commits`, `test_hold_rejects_bad_id`, `test_held_ready_after_nine_tomorrow_in_config_tz` (inject `now`), `test_ready_hold_listed_first_in_today`, `test_attendee_approval_writes_in_touch`. FAIL → implement → PASS → gate (incl. `test_no_send.py`, `test_approve_attendees.py`, `test_people.py`). `npm run build` green.
- [ ] Commit `feat(api): typed touches, promise close, owner edits, hold`.

### Task 10: Draft v2, reply, MCP, contract mirror

**Files:** see file map. **Authorized test edits:** `api/tests/test_mcp.py` exact tool-set assertion (grep `people_draft`) — add `"people_reply"`; `api/tests/test_mock_parity.py` module docstring only (mock now imports pipeline, R18).

`PersonDraftBody` adds `touch_type: str = ""`, `payload: str = ""`, `outcome: str = ""`, `situation: str = ""`, `include_sensitive: bool = False`.
`build_draft_prompt(person, voice, channel, *, rules: str = "", touch_type: str = "", payload: str = "", outcome: str = "", situation: Situation | None = None, include_sensitive: bool = False)` (R5 — positional call `build_draft_prompt(person, "voice", "whatsapp")` must still work). Output parts in order:
1. Existing voice sentence + `{voice}` (unchanged wording).
2. If rules: `---\nRules for every draft:\n{rules}\n\n`.
3. Existing line, unchanged: `---\nI want to reconnect with {name} ({relationship} at {company}), over {channel}.` followed by `Language: {language or "en"}.`
4. Sections, each only when non-empty after filtering (drop lines containing `#sensitive` unless include_sensitive): `What I know about them:` Context · `What is going on for them:` Current state · `Where they want to get to:` Future state · `What they need:` Needs · `Things they told me:` Facts · `Open next steps:` Next action. **Never** Interpretations.
5. Existing history block unchanged, including the literal fallback `Our history: nothing logged yet — we have not spoken since I made this note.` (R5).
6. If touch_type: `This message is a {touch_type} touch.` + (if payload) ` It carries exactly one payload: {payload}`.
7. If `person.commercial` and `conversation_stage`: one line — probative `Share perspective only, no pitch.`; qualifying `Establish fit both ways, including budget and who decides.`; value `Agree what it is worth before any proposal.`; closing `Offer options and a date.`; delivering/past `Report outcomes and say the kind truth if one is due.`
8. If not commercial: `This person is family or a friend. Never ask for business, referrals, introductions or favours.`
9. If outcome: `The outcome I want: {outcome}`. If situation: `Situation: {title}. Move: {move}. Adapt this line, do not copy it: {line}`.
10. Channel: whatsapp `Write 2 to 4 short lines.`; email `Start with a line "Subject: …", then the body, under 120 words.`
11. Existing leash text verbatim, then `No em dashes, no emojis, no exclamation marks, no opener that praises them, no closing summary.`
12. Existing final instruction line (keep its wording; for email replace "no subject line" with "subject line first").
`draft()` loads `rules_text, wrote = greene.ensure(vault, greene.RULES_FILE)` (commit if wrote), resolves `situation` code via `greene.parse(greene.ensure(vault)[0])` (commit if wrote), and returns existing keys plus `subject` (email: first line starting `Subject:` removed from `text` and returned separately; else `""`) and `lints` (`draftlint.lint(text, channel=…, tier=…, touch_type=…, quiet=…)`).

`POST /api/people/{id}/reply` body `{message: str}` → `{reads, situation, draft, lints, seducer}`:
- situation via `llm.complete_json(prompt, config, validate)`; prompt: `Pick the one situation code that best fits this incoming message, or null.\n{"\n".join(f"{s.code} {s.title}")}\n\nMESSAGE:\n{message}\n\nReturn ONLY JSON {"code": "3.x"} or {"code": null}.`; code not in parsed codes → `None` (P25).
- draft touch_type: `kind_truth` for 3.4/3.15, `ask` for 3.11, else `remember`; payload `Reply to: {message[:200]}`. This mapping is the only one; do not use `presets` here.
- missing voice → same LookupError refusal as `/draft`.
- Tests stub LLM exactly as existing draft tests do (grep `monkeypatch` / `router` in `api/tests/test_people.py` and copy that approach for both `complete_text` and `complete_json`).

MCP `scripts/cockpit_mcp.py`: `people_reply(person_id: str, message: str)` → `call("POST", f"/api/people/{quote(person_id)}/reply", {"message": message})`, description: "Four reads, the matching Greene situation, a draft in the owner's voice, and the lints that fired. Text only; this server cannot send messages."

Contract mirror:
- `web/API-CONTRACT.md`: one section per T8–T10 endpoint with request/response JSON examples.
- `web/mock-api.py` (R17, R18): at top `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` then `from pipeline import draftlint, greene`. Register handlers for `/api/people/today`, `/api/people/greene`, `/api/people/held`, `/api/people/lint` **above** the `path.startswith("/api/people/")` id branch (GET and POST blocks). Fixtures: 3 people — core client (open `I promised:` due today, birthday in 3 days, reliability 2 kept · 1 dropped, pride line), inner family (quiet until +20 days), untiered prospect (no payload). Mock `detail` returns the T8 shape; `/contact`, `/promise`, `/owner`, `/hold`, `/reply` return plausible static shapes.
- TS types: `QueueItem`, `TodayResponse`, `Situation`, `LintItem`, `LintResult`, `PersonDetailV2 extends PersonDetail`, `HeldItem`, `ReplyResult`, `OUT_TYPES` const. Client (R16): `person(id: string, opts: {desk?: boolean} = {})` → `?desk=1` when desk; `personDraft(id: string, body: {channel?: string; touch_type?: string; payload?: string; outcome?: string; situation?: string; include_sensitive?: boolean} = {})`; `peopleToday()`, `greeneSituations()`, `lintDraft(body)`, `closePromise(id, body)`, `ownerEdit(id, body)`, `hold(id, body)`, `unhold(id)`, `held()`, `reply(id, message)`. Existing drawer call `api.personDraft(person.id)` must still compile.
- [ ] Tests: existing `test_people.py` prompt tests pass **unchanged**; new `test_prompt_excludes_interpretations`, `test_prompt_excludes_sensitive_unless_opted_in`, `test_prompt_includes_rules_when_given`, `test_family_prompt_forbids_asks`, `test_probative_stage_line`, `test_email_draft_splits_subject`, `test_draft_returns_lints`, `test_draft_seeds_rules_and_commits_once`, `test_reply_invalid_code_gives_null_situation`, `test_reply_refuses_without_voice`. FAIL → implement → PASS → gate. `test_mcp.py` stays skipped locally (R30) — state that in the task report.
- [ ] Mock check: `$PY web/mock-api.py &` then `curl -s localhost:8000/api/people/today` (add the auth header the mock expects — grep `Authorization` in mock-api.py) returns a strip; kill the mock. `npm run build` green.
- [ ] Commit `feat(api): draft v2 with doctrine and greene, reply, contract mirror`.

### Task 11: Person page + composer + Greene panel — **Wave B (sub-worktree), parallel with Task 12**

Load `impeccable`, `emil-design-eng`, `frontend-design`; read `DESIGNSYSTEM.md` first. **Do not edit `People.tsx`, `Today.tsx`, `Triage.tsx`, `run-e2e.mjs` (P12).**

**Files:** `web/src/App.tsx` (Route union += `"person"`; `parseRoute`: if hash (before `?`) matches `^people/\d{14}$` return `"person"`; render `<Person />`), `web/src/components/Layout.tsx` (People nav active when route is `people` or `person`), `web/src/lib/channels.ts` (new: copy `channelLink` from People.tsx; new `channelLabel` returning `"Open in WhatsApp"` / `"Open in mail"` / `"Open in LinkedIn"`, R21), `web/src/screens/Person.tsx`, `web/src/components/Composer.tsx`, `GreenePanel.tsx`, `LintList.tsx`.

**Fixed accessible names (R20) — use exactly:** textarea `aria-label="Draft message"`; `<select aria-label="Touch type">`; buttons `Draft with AI`, `Copy`, `Save Gmail draft`, `I sent it`, `They replied`, `Hold until tomorrow 9 am`, `Discard held draft`, `Use this line`, `Edit record`, `Kept`, `Late`, `Dropped`, `Kept late`; checkbox label `They asked for this`; checkbox label `Include sensitive details`; links `Open in WhatsApp` / `Open in mail`; search `aria-label="Find a situation"`; outcome input `aria-label="Outcome"`; panel toggle `aria-label="Greene panel"` with `aria-expanded`.

**Person.tsx** — `id` from hash; fetch `api.person(id, {desk: window.matchMedia("(min-width: 1024px)").matches})` (R16), `api.held()`. Layout: one column below `lg`; `lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,28rem)] lg:gap-8`; DOM order record then composer. Order (patterns doc):
1. Header: eyebrow `{tier || "untiered"} · {status_computed}`; name (display font); relationship chips; ledger line `gives 90d {g} · asks {a} · received {r}`; reliability line (muted, hidden when empty); last contact; preferred channel + language; energy glyph at `lg` only (`aria-label="energy"`, no word).
2. Known for (+ recall_trigger muted) with inline edit → `ownerEdit`.
3. Quiet line when `quiet` (muted text only, no button).
4. `<details>` **Before you talk** (closed): `Calm?` · `Pride: {reads.pride || "–"}` · `Record: {reads.record || "–"}`; current state first 2 lines; latest open I promised and They promised; first open ask_about; how they communicate; first open action whose text starts `Kind truth:` if any.
5. Working together only when non-null; `no_economic_buyer` → "You have not met the economic buyer."
6. Sections: I promised (each open item: `Kept` / `Kept late` / `Dropped` → `closePromise(side "mine")`) · They promised (`Kept` / `Late` / `Dropped` → side "theirs") (R11) · Open follow-ups · Ask them about · What's going on for them · Where they're headed · They're looking for · They can help with · How they communicate (my read) · About them · Remember · Interpretations (heading "My interpretations, not things they said") · Interaction log (touches). Empty sections: one muted line from the patterns doc empty-state style.
7. `Edit record` sheet: tier, energy, list_of_20, commercial fields (only when commercial), birthday/anniversary inputs; shows returned `warning`.
8. Push profile: plain link "Push profile" to `#/people` (PushRow stays in People's slim drawer, T12).

**Composer.tsx** props `{detail: PersonDetailV2; queue?: string; sourceKey?: string; held?: HeldItem}`:
- Payload chip: action with `key === sourceKey`, else first open action; chip text `{touch_type} · {text}`; no chip → no `Draft with AI` button (typing still allowed).
- Touch type select over `OUT_TYPES`; preset `{owe_reply: "remember", promises: "keep_promise", ask_about: "remember", celebrate: "celebrate", follow_up: "remember", reconnect: item touch_type}` else `remember`.
- `Include sensitive details` checkbox → `include_sensitive` on draft.
- **GreenePanel** above textarea: collapsed one-line strip `Calm? · Pride: … · Record: … · Outcome [input]`; toggle; expanded: `Hold until tomorrow 9 am` ghost button (calls `hold`, toast shows until), situation search (presets first; card shows trap, move, line; `Use this line` inserts at cursor, records chosen code), Anti-Seducer chips (only fired). Below `lg`, expanded panel is a bottom sheet: fixed, `max-h-[80dvh]` scroll, Esc closes, focus moves into sheet and returns to toggle.
- Textarea + **LintList** below: each `"{snippet}" → {message}` in `text-subtle` (never accent). No overlay underline in this task (P20: list is the contract; overlay logged to DEFERRED).
- Lint requests debounced 300 ms with a request counter; ignore stale responses (P21). Draft response `lints` shown immediately.
- Actions: `Draft with AI` (primary, only with payload) · `Copy` · email: `Save Gmail draft` (move `saveGmailDraft` logic from People.tsx drawer by copying it; uses `api.googleDraft`, subject from draft) · link `Open in WhatsApp` / `Open in mail` via `lib/channels.ts` · `I sent it` → `logContact({direction: "out", touch_type, greene: chosenCode || "", note: text.slice(0, 120), channel, requested})`; if `touch_type` is keep_promise/keep_promise_late and the payload action starts `I promised:` also `closePromise({key, result: touch_type === "keep_promise" ? "kept" : "late", side: "mine"})`; show `suggest_stage` prompt like the old drawer (Yes/Not yet → `setWarmth`) · `They replied` → `logContact({direction: "in", touch_type: "reply", note: "", channel})`.
- Held: if `held` prop for this person, preload text, show muted `Still want to send this?` and `Discard held draft` (→ `unhold`).
- Under textarea, muted `This is your read, not something they said.` whenever the pride read was inserted into the text by the panel.
- [ ] `npm run build` green. Preview (`preview_start` "mock-api" and "web" from `personal-brain-os/.claude/launch.json`; if the launch config points at the main checkout paths, add worktree-specific entries named "mock-api-ro"/"web-ro" pointing at `brain-cockpit-ro`). Check at 390×812 and 1440×900, dark and light: section order; typing `Hope this finds you well — no rush!!` lists softener + em_dash lints and reactor chip; Hold returns until; `Use this line` inserts; Esc closes sheet; no console errors. Screenshot both widths.
- [ ] Commit `feat(web): person page, composer, greene panel, lints, hold`.

### Task 12: Today strip, People queues, slim drawer, Triage, e2e — **Wave B (sub-worktree), parallel with Task 11**

Load `redesign-existing-projects` + `impeccable`. **Do not edit `App.tsx`, `Layout.tsx`, `Person.tsx`, `Composer.tsx`, `GreenePanel.tsx`, `LintList.tsx`, `lib/channels.ts` (P12).** Links to `#/people/{id}` resolve once T11 merges.

- **Today.tsx:** `PeopleToday` rendered as the **first child** of the Today screen content, above `HeroCard` (patterns: strip first on screen). Eyebrow `People today`; ≤5 rows `name · tier dot · LABELS[queue] · payload (1 line truncate)` + one primary link `#/people/{id}?queue={queue}&key={source_key}` labelled `Reply` / `Keep promise` / `Ask` / `Celebrate` / `Follow up` / `Reconnect`; muted `{overflow} more, none urgent`, or `{overflow} more` when any flagged item exists outside the strip; empty → muted `Nobody needs you today.`; error → existing ErrorState three-part pattern. Leave `StreakDots` untouched (R32).
- **People.tsx (R15):** replace `DraftDrawer` with a slim `ProfileDrawer` (dialog `aria-label="Profile for {name}"`) containing only `PushRow` and the warmth stage chips (reuse `StageRow`), opened by a card button `Profile` and by `PushQueue onOpen`. Card button `Draft a message` becomes `<a href="#/people/{id}">Draft a message</a>`. Remove the drawer's draft/Gmail/log code and the `logContact` caller. Keep `channelLink`/`channelLabel` only if still used; otherwise delete. Add a queue section above filters (data `api.peopleToday()`): 7 views fixed order, `{LABEL} ({count})` + rows linking to the person page; zero → muted `None right now.`; reconnect row with empty payload → `No payload yet. Find one.` as `<details>` with the three A5 prompts, no draft link. Tier line `inner {n}/15 · core {n}/35 · active {n}/100 · untiered {n}`.
- **Triage.tsx:** muted `sensitive` chip when `topic === "health"` or text includes `#sensitive`.
- **e2e `web/e2e/run-e2e.mjs` (R11, R15, R20, R21):**
  - Seed: the e2e person note gains a `## Next action` line `- {today} · Ask how it went: the studio visit`.
  - Step 8: goto `#/people/{id}`; click `Draft with AI`; assert `Drafts need your own voice on file first.`; write voice (as today); stub `**/api/people/*/draft` as today; click `Draft with AI`; fill `Draft message` with `just checking in — no rush`; wait for text `say the date and why`; select `Touch type` = `remember`; click `I sent it`; assert vault git log matches `/logged contact/` and the note contains `· out · whatsapp · remember ·`; assert link name `Open in WhatsApp` (replaces `Open WhatsApp`).
  - Steps 9–10: goto `#/people`; replace "Draft a message" clicks with the card's `Profile` button; "Not yet" click removed (stage prompt now lives in the composer); Escape closes `Profile for …` dialog.
  - New step: goto `#/`; `People today` visible; row count ≤ 5.
- [ ] `npm run build`; preview verify 390/1440 dark+light; `design-taste-frontend` audit over Today strip + People queues (and Person/Composer after merge) — fix findings that don't conflict with DESIGNSYSTEM.md.
- [ ] Commit `feat(web): today strip, relationship queues, slim profile drawer, e2e`.
- [ ] **Wave B merge:** merge T11 and T12 branches; `cd web && npm run build && cd .. && PATH="/Users/vineethnair/Vibe Code/personal-brain-os/brain-cockpit/.venv/bin:$PATH" node web/e2e/run-e2e.mjs` → exit 0. If Playwright is not installed globally, stop and ask the user before installing it (machine tool, not a project dependency).

### Task 13: Digest, heartbeat, DEFERRED, docs

**Authorized test edits (R6):** in `pipeline/tests/test_relationships.py` the people-digest tests (grep `people_section`) and in `pipeline/tests/test_todos.py` the digest tests that assert people lines (grep `days quiet`) — update expectations to the new line format below; keep their intent (a going-cold person still appears; overflow still mentioned). For `test_a_person_going_cold_is_reason_enough_to_push`, the digest must still push (see rule below) — do not change that test's fixture.

- [ ] `pipeline/morning.py`:
  - `TOP_N` stays as an alias `TOP_N = queue.DAILY_CAP`.
  - `people_section(config, today, top_n=TOP_N)`: `people = load_people(vault)`; `pairs = ledger.advance_statuses(people, today)`; if pairs: commit vault (`"morning: before status heartbeat"`), write files, commit (`f"morning: status heartbeat ({len(pairs)})"`) using the commit helper the pipeline already uses (grep `def commit` / `git` in `pipeline/vaultsync.py` and `pipeline/todos.py`; reuse it; if none exists in pipeline, import `git_commit_vault` lazily inside the function from `api.notes`), then reload people.
  - `queues = queue.build(people, today, held=[])`; `items, overflow = queue.strip(queues)`.
  - **Digest rule:** if `items` is empty but `queues["reconnect"]` is non-empty, use the top `top_n` reconnect items (payload may be blank) so a going-cold person still pushes.
  - Lines: `People:` then per item `f"• {name} ({when}) · {LABELS[queue]} · {payload or 'no payload yet'}"` where `when` reuses the existing `_line` wording (`never contacted` / `spoke today` / `1 day quiet` / `{n} days quiet`); then `f"• …and {overflow} more on the People screen"` when overflow.
- [ ] Tests `pipeline/tests/test_morning.py` (new): `test_digest_lists_at_most_five`, `test_digest_falls_back_to_reconnect_without_payload`, `test_status_heartbeat_commits_before_and_after` (tmp git vault), `test_no_people_no_lines`. Gate.
- [ ] `DEFERRED.md`: append `## Relationship OS v2.2 — Phase 2 (logged 2026-09-17)`, one line each:
  - intro matcher `match.py` + Could help view + payload rule clause 2
  - warm-up engine v2 (stage moves, List of 20 cap enforcement, 50/50 rule, fit:poor exclusion) and "warm-up action due" in the morning priority
  - weekly Review page (three fears, softener audit, ledger flags, tier moves, List of 20, Anti-Seducer audit, self-possession, in person) → `08-Reflections`
  - monthly Reputation page (`positioning.md` known_for vs `reputation.md`)
  - windowed metrics on Review
  - stalled-thread ladder rungs 1–4 on Waiting on them (commercial)
  - engine-proposed `ask` touches using `ledger.ask_allowed`
  - after-call prompt on `#person` captures
  - two draft variants (best + shorter/assertive)
  - quarterly `greene:` situation-code recurrence readout
  - Dex enriched summary recall aids (tier · last promise · next Ask about)
  - `positioning.md` editor + public card positioning line
  - Obsidian Bases views (today, promises, going cold, List of 20)
  - company notes v2.2 (fit, current/future state, Buying map) + economic buyer from company Buying map
  - `important_date` proposals auto-Fill `dates` and remind yearly (today: owner edits dates; reminder a week before once)
  - inbound WhatsApp/Gmail capture feeding `in` touches automatically
  - lint underline overlay in the composer textarea (list ships today)
  - PushRow and warmth chips on the person page (slim Profile drawer ships today)
  - payload chip linking to its source line in the note
  - "Introductions and referrals" section on the person page
  - the eight queue views on Home under the strip (they live on People today)
  - StreakDots on Today conflicts with the doctrine's no-streaks rule — decide keep/remove
  - Handshake parity: tier names `inner5/key15/circle50/wider150` → `inner/core/active/wide`; Handshake auto-send lane conflicts with Doctrine Rule 1; port touchlog/ledger/linter/Greene panel
- [ ] `CLAUDE.md` §8: add `- **Pass RO** — Relationship OS v2.2 Phase 1 (pipeline touchlog/ledger/queue/draftlint/greene, people API v2, person page + composer).` `pipeline/README.md` and `api/README.md`: one paragraph each naming new modules/endpoints.
- [ ] Final gate: `$PY -m pytest -q` (no new failures vs baseline) · `cd web && npm run build` · e2e exit 0.
- [ ] Copy this plan to `docs/superpowers/plans/2026-09-17-relationship-os-phase1.md`. Commit `docs: relationship os phase 1 pass, phase 2 deferred log`.
- [ ] `superpowers:finishing-a-development-branch`: present merge options to the user. Do not merge into `main` or push without the user's explicit choice (the sync agent pushes `main`).

---

## Execution order

1. Task 0 (worktree).
2. Sequential: 1 → 2 → 3 → 4 → 5.
3. **Wave A:** sub-worktrees `ro-t6`, `ro-t7` from the feature branch; 6 ‖ 7; merge; full gate.
4. Sequential: 8 → 9 → 10.
5. **Wave B:** sub-worktrees `ro-t11`, `ro-t12`; 11 ‖ 12; merge; build + e2e.
6. Task 13, then finishing-a-development-branch.
Two-stage review after each task. After each wave, one reviewer checks names used across tasks against the **Produces** blocks.

## Verification (end-to-end)

- `$PY -m pytest -q` in the worktree: no failures beyond baseline; includes schema mirrors, `test_no_send.py`, the authorized-edit tests.
- `cd web && npm run build`; e2e exit 0.
- Preview on mock at 390 px and 1440 px, dark and light: Today shows `People today` first with ≤5 rows and an overflow line; person page sections in patterns order; lints and Anti-Seducer chips fire on `Hope this finds you well — no rush!!`; Hold → held draft appears first in I owe a reply with "Still want to send this?"; quiet family person shows the neutral quiet line, is absent from Reconnect, and has no Working together block. Screenshots in the final report.
- MCP `people_reply` via `scripts/cockpit_mcp.py` (manually, since the test is skipped locally): returns reads, situation or null, draft, lints; no send path.
- Owner acceptance after merge (real vault, doctrine build-order warning): tier 10 real people, send one week of real touches through the composer; vault git log shows each write; logs carry `out · channel · touch_type · summary` lines.

## Self-review

- Spec coverage: A1 causes (known_for/recall_trigger; Standing via floor/quiet/Hold/lints) · A2/A3 gate (T2 `commercial`, T10 prompt) · A4 tiers/cap (T2, T5, T9 warning) · A5 taxonomy + payload rule (T3, T5; clause 2 deferred) · A6 ledger + thank loop (T1, T4) · A7 sections (T2, T8, T11) · A8 stage drafting (T10) · A9 rules 1–19 (T6, T10, T11) · A11 floor/quiet/reliability/reputation_signal/energy (T1, T3, T4, T9, T11) · GREENE-HELPER §6.1–6.3 (T7, T10, T11) · patterns Home strip / views / person order / composer (T11, T12). Everything else is a DEFERRED line in T13.
- No placeholders; every heuristic has exact triggers; every UI name used by e2e is fixed in T11.
- Name consistency: `record_touch`, `close_promise(side=)`, `NextAction.key/closed/dated_format`, `ledger.is_quiet/inside_floor/status_for/reliability_line/advance_statuses`, `queue.build/strip/LABELS/DAILY_CAP/Held`, `greene.ensure→(text, wrote)/parse/presets/reads`, `draftlint.lint`, `people.detail(desk=)`.
- After approval (main session, before dispatching Task 0): save a feedback memory that the user wants their listed plugins used in prompts wherever they add value.
