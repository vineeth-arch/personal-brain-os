# **SCHEMA-REFERENCE - the canonical schema (lock before note \#1)**

**This is the single source of truth for structure.** Every note, every script, every AI routing pass reads from here. These conventions are **migrations if changed late** - they're locked at Phase 0.4, before note \#1. Features can change; this should not. Version: 2.2 · 2026-09-17 (v2.2 adds the `greene:` situation code on log lines and `_System/greene-helper.md`; v2.1 adds the Standing layer: `quiet_until`, `energy`, promise-closing log types, `reputation_signal`, interpretation prefixes; v2.0: Relationship Doctrine: tiers, touch types, ledger, current/future state, commercial gate, 7 new proposal types). App: **Brain OS / cockpit**. This file is now content-identical to the Handshake schema file below this line: v1.0 had drifted five versions behind (no `company`/`conversation` types, no `## Facts`/`## Interpretations`, no merge rules) while both apps write the same vault. One vault, one contract.

---

## **1\. THE NON-NEGOTIABLES (impossible/painful to retrofit)**

* **Fixed `id`** on every note: an immutable timestamp `YYYYMMDDHHmmss`. **All links and typed edges point to the `id`, never to a title or path.** Rename/move freely; links never break.  
* **`origin: human | ai`** on every note: provenance. You can NEVER reconstruct later what was AI-written vs yours. Set it at creation.  
* **`raw/` vs `wiki/`** folders: `raw/` is user-managed and outside the pipeline's numbered scheme - the pipeline never reads or writes it; it holds immutable sources you place there yourself, never edited. `wiki/` also sits outside the numbered scheme, but the pipeline **does** write to it - it is the real destination folder for `insight`-type notes (atomic synthesis, one idea per note). The human/agentic firewall is the **`origin: human | ai`** field (the first non-negotiable above), NOT the folder: `wiki/` can hold both human- and AI-authored insight notes, told apart by frontmatter.

## **2\. UNIVERSAL FRONTMATTER (every note, all domains)**

yaml  
\---  
id: 20260605143000        \# immutable timestamp YYYYMMDDHHmmss - links point here  
type: musing              \# see type list below  
created: 2026-06-05  
source: voice             \# voice | plaud | share | web-clipper | dex | manual | ai-compiled | handshake | gmail  
origin: human             \# human | ai  (provenance firewall)  
status: active            \# lifecycle per type (section 6\)  
categories: \[\]            \# what it IS    - as \[\[links\]\] (Kepano model), not flat tags  
subjects: \[\]              \# what it's ABOUT - as \[\[links\]\]  
tags: \[\]                  \# from \_System/vocabulary.md (controlled vocabulary)  
duration\_min:             \# audio captures only - how long the recording ran  
\---

**Types:** `musing · learning · todo · journal · project · person · resource · decision · principle · insight · reflection · company · conversation`

## **3\. NAVIGATION SUBSTRATE (decided now; Bases views built later)**

* Organize by **`categories` \+ `subjects` as link-properties**, NOT deep folders. Folders are coarse domain bins; navigation is by property \+ Bases views.  
* `categories` \= what a note *is* (e.g. `[[Recipe]]`, `[[Person]]`, `[[Decision]]`). `subjects` \= what it's *about* (e.g. `[[Branding]]`, `[[ADHD]]`, `[[Dubai market]]`).  
* Link the first mention of any meaningful entity (build the graph as you write).

## **4\. CONTROLLED VOCABULARY**

* All `tags` come from `_System/vocabulary.md`. Don't invent synonyms ("AI" vs "a.i."). Add new tags to vocabulary.md deliberately, prune in review.  
* **Capture/routing tags (10 max):** `#todo #idea #journal #learning #person #resource #decision #project #musing #conversation`. A tag present at capture routes the note for FREE (no AI classify).

## **5\. TYPED EDGES (fix the vocabulary now; auto-compile is Phase 2\)**

Use these relation types in `wiki/` notes; each edge points to a fixed `id`: `supports · contradicts · derived-from · depends-on · part-of · informed-by · introduced-by · referred-by` (the last two connect person to person) Format in body: `- supports:: [[20260601090000]] (one-line why)` - the *why* is required (a link without a reason is noise).

## **6\. STATUS LIFECYCLES (per type - the "no dead list" guarantee)**

| Type | Lifecycle |
| ----- | ----- |
| resource | `inbox → to-consume → consumed → referenced → archived` |
| decision | `open → resolved` (+ `brier`, `process_grade` on resolve) |
| todo | `open → done` |
| project | `active → slipping → done | dropped` |
| person | `active → cold → dormant` |
| musing/learning/insight | `active → archived` |
| company | `active → archived` |
| conversation | `active → archived` |

## **7\. PER-DOMAIN SCHEMAS (extra frontmatter on top of universal)**

### **Knowledge notes (musing → `02-Musings`, learning → `03-Learnings`, insight → `wiki/`)**

Atomic (one idea), **your own words**, with a `derived-from` edge to its source. No extra required fields. All three share the same lifecycle (`active → archived`, §6) and are the three folders the daily resurfacing pick samples from.

### **Resource (`04-Resources/*`)**

yaml  
type: resource  
resource\_type: tool       \# tool | tutorial | book | movie | recipe | place | article  
title:  
cover:                    \# attachments/\<file\> - book cover / poster / screenshot / thumbnail  
source\_url:  
archive\_url:              \# private YouTube / Amazon Photos pointer (heavy media lives there)  
description:              \# LLM, one line  
insight:                  \# YOUR voice, verbatim - never overwritten by AI  
status: inbox  
rating:                   \# 1–7, after consuming  
captured:                 \# photo notes: EXIF date the photo was taken, only when it differs from `created`  
consumed:
platform:                 \# written by enrichment: youtube / instagram / web / photo  
enriched:                 \# true once enrichment succeeded  
enrich\_attempts:         \# retry counter, capped at 4  
enrich\_last:             \# ISO timestamp of the last attempt

Type extras - book: `author`; movie: `where_to_watch`, `runtime`; tutorial: `steps`, `tools_mentioned`, `transcript`; recipe: `ingredients`, `steps` (maps to Mom's Kitchen); place: `map_url`, `best_time`.

### **Decision (`09-Decisions`)**

yaml  
type: decision  
claim:  
outside\_view:             \# base rate FIRST, before inside-view specifics  
probability:              \# 0-100  
confidence:  
reasoning:                \# Fermi-ized: the sub-questions that must be true  
disconfirmers:            \# "I am wrong if ..."  
resolves: 2026-07-01  
status: open  
brier:                    \# set on resolution  
process\_grade:            \# graded on PROCESS, not outcome

### **Principle (`10-Principles`)**

yaml  
type: principle  
statement:                \# the rule / algorithm  
version: 1  
derived\_from: \[\]          \# ids of decisions that generated/tested it

### **Person (`07-People`) - see RELATIONSHIP-OS-ARCHITECTURE.md**

yaml  
type: person  
relationship: \[\]          \# Union list: family | friend | client | past\_client | prospect | referrer | peer | collaborator | mentor | vendor  
company:  
channels: {whatsapp:, email:, linkedin:}  
preferred\_channel:        \# whatsapp | email | linkedin | call  
language:                 \# en | hinglish | hi | mr (drafts are written in this)  
tier:                     \# inner (cap 15, 14d) | core (cap 35, 30d) | active (cap 100, 90d) | wide (no cadence, event-driven)  
cadence\_days:            \# overrides the tier default  
last\_contact:  
last\_give:               \# Forward-only, set by ledger from give-type log lines  
last\_ask:                \# Forward-only, set by ledger from ask log lines  
quiet\_until:             \# Forward-only date, set by ledger after two consecutive unanswered outbound touches; readers surface nothing unprompted before it  
energy:                   \# Owner-only: gives | neutral | drains (never rendered as a word, never public)  
known\_for:               \# what THIS person should associate me with (one line)  
recall\_trigger:          \# the situation in their life that should make them think of me  
dates: {birthday:, anniversary:}  
referred\_by:             \# \[\[person-id\]\], Set-once  
list\_of\_20: false       \# Port's List of 20, max 20 true at a time, runs through warm-up  
warmth\_stage:             \# identified | researched | engaging | conversing | warm | ready  
conversation\_stage:      \# COMMERCIAL GATE: none | probative | qualifying | value | closing | delivering | past  
buyer\_role:              \# COMMERCIAL GATE: economic | influencer | user | gatekeeper | unknown  
fit:                      \# COMMERCIAL GATE: ideal | good | poor | unknown  
dex\_id:  
dex\_deeplink:  
handshake\_id:  
outreach\_id:  
status: active            \# active | cold | dormant (cold = 1.5x cadence overdue, dormant = 3x)

Body, in this order: `## Context` · `## Current state` (append-only, dated: what is going on for them, problems they named, the impact and cause as THEY stated it) · `## Future state` (append-only, dated: where they want to get to, in their words) · `## Needs` · `## Can help with` (append-only, dated: what they are good at, sell, or offered; feeds the intro matcher) · `## How they communicate` (owner-written or approved reading, heading rendered with "my read") · `## Facts` (append-only, dated, one fact per line, citing its source with `derived-from::` when it comes from a conversation note) · `## Interpretations` (append-only, dated: readings, not statements; kept apart from `## Facts` so a guess never reads as a fact) · `## Interaction log` (append-only, dated) · `## Next action` · `## Updates` (append-only, dated: proposed field changes from another app that would overwrite an already-filled value; never applied automatically, see the merge-rules table).

**Interaction log line format (v2.0):** `- {date} · out|in · {channel} · {touch_type} · {one-line summary}` followed by the usual `derived-from::` and idempotency marker. `touch_type` is one of: `remember · give_know · give_who · celebrate · keep_promise · kind_truth · thank · invite · ask · presence` for `out` lines (a promise delivered after its date is logged `keep_promise_late`), and `reply · give_theirs · ask_theirs · promise_kept · promise_late · promise_dropped · other` for `in` lines (the three `promise_*` types are written when the owner closes a `commitment_theirs` item and are the only input to the reliability read). A helper-assisted reply appends its situation code as a final segment (`· greene:3.4`, GREENE-HELPER.md §3); it is optional, ignored by the ledger, and read only by the quarterly review. The ledger (`ledger.py`) reads ONLY the `touch_type` field to compute gives, asks and received, so it is required on every new line.

**The commercial gate.** `conversation_stage`, `buyer_role`, `fit`, system-proposed `ask` touches and stalled-thread drafts are inert when `relationship` contains only `family` and/or `friend`. Writers must leave those fields blank for such notes and readers must ignore them if present. Doctrine: RELATIONSHIP-OS-ARCHITECTURE.md A3.

**Payload Rule (reader contract).** A person due by cadence may be surfaced for reconnect only with a payload drawn from: an open `## Next action` line, a `## Needs`/`## Future state` line matched to someone's `## Can help with`, a `dates` entry within 7 days, or an unthanked `give_theirs`. `tier: inner` alone may surface with `touch_type: presence`.

**Contact floor (reader contract).** Minimum days between unprompted outbound touches: `inner` none · `core` 10 · `active` 30 · `wide` event-driven only. Exempt: replies, `keep_promise`, `thank`, `celebrate`, anything they requested, any person created under 90 days ago, and `list_of_20: true`. Doctrine: RELATIONSHIP-OS-ARCHITECTURE.md A11.2.

**Interpretation prefixes.** Lines in `## Interpretations` may start with a controlled prefix: `style:` (how they communicate) · `character:` (a pattern seen over time, never a first impression) · `pride:` (what they value in themselves; guides tone, never quoted) · `caution:` (a defensive-radar pattern, A11.4). Prefixed or not, an interpretation is never used as a fact in a draft and never rendered on a public surface.

**Sensitive lines.** Any appended line about health, family difficulty or money ends with `#sensitive` before its marker. Draft generators must exclude `#sensitive` lines unless the owner opts in for that one touch. Public surfaces never read person notes at all.

#### Handshake proposal types → sections

Handshake (reading WhatsApp) and the cockpit pipeline (reading voice and text
captures, `pipeline/proposals.py`) both *propose*; a human approves every
proposal before it is written. Each lands in the sections below, subject to the
merge-rules table (a proposal never overwrites a Filled value - it appends
under `## Updates` instead). `pipeline/proposals.py` `SECTIONS` mirrors this
table exactly - **keep the two in sync** (a test parses this table).

| Proposal | Means | Lands in |
| ----- | ----- | ----- |
| fact | something stated | `## Facts` |
| interpretation | a reading, not a statement | `## Interpretations` |
| commitment\_mine | a promise the owner made | `## Interaction log` · `## Next action` |
| commitment\_theirs | a promise they made (check in the day after its date) | `## Interaction log` · `## Next action` |
| follow\_up | something the owner must do | `## Next action` |
| personal\_detail | family, health, home, interests, preference, favour (topic-prefixed) | `## Context` |
| upcoming | an event in their life → "Ask how it went" dated the day after | `## Next action` |
| milestone | new job, baby, move, award → congratulate, dated today | `## Next action` |
| need | what they're looking for | `## Needs` |
| company\_knowledge | durable org facts | company note `## Facts` |
| person\_update | a durable attribute (`company` goes through Fill, `relationship` through Union) | `## Context` |
| problem | a problem they named, with its impact if they stated one | `## Current state` |
| goal | where they are trying to get to, in their words | `## Future state` |
| offer | what they are good at, sell, or offered to help with | `## Can help with` |
| intro | an introduction made or promised (check in 14 days after) | `## Interaction log` · `## Next action` |
| give\_mine | something the owner gave: intro, resource, help, referral | `## Interaction log` |
| give\_theirs | something they gave the owner (thank today, report outcome in 30 days) | `## Interaction log` · `## Next action` |
| important\_date | birthday, anniversary, launch date (`dates` goes through Fill) | `## Next action` |
| reputation\_signal | something they said about the owner, or repeated from others | `\_System/reputation.md` · `## Interaction log` |

`## Next action` lines lead with their due date (`- 2026-10-13 · Ask how it
went: …`) so the People screen and morning digest surface them on that day; an
undated promise of theirs is written `- open · …`. Every approved line ends
with `derived-from:: [[capture-id]] (ai, approved)` and a `<!-- bc:… -->` marker.

### **Company (`11-Companies`) - written and updated by handshake**

yaml  
type: company  
name:  
domain:  
handshake\_id:  
outreach\_id:  
fit:                      \# ideal | good | poor | unknown (Red Velvet Rope; poor is excluded from warm-up and proposed asks)  
status: active            \# active | archived

Body: `## About` · `## People` (one line per person, `- [[person-slug]] — role (from date)` (the em dash here is a data format Handshake writes and parses; do not change it)) · `## Facts` (append-only, dated) · `## Current state` (append-only, dated: the business's situation and named problems) · `## Future state` (append-only, dated: where they want the business to go) · `## Buying map` (one line per person: `- [[person-slug]] · buyer_role · note`) · `## Projects`. Person notes back-link via frontmatter `company: "[[company-slug]]"` (already `PATCHABLE` on the person side).

### Cross-app merge rules (multiple writers, one note)

Person and company notes can be enriched by more than one system (the cockpit pipeline, Handshake, the outreach cockpit). No writer ever overwrites another's or the owner's data. Every field/section falls into one of six kinds (all remaining person fields, including `preferred_channel, language, cadence_days, dates, warmth_stage, company`, are Fill):

| Kind | Rule |
| ----- | ----- |
| Set-once | written at creation, never changed (`id, type, created, source, origin, referred_by`) |
| Fill | set only if blank; if already set to a different value, the new value is appended as a dated suggestion under `## Updates` instead of applied |
| Union | merges without removing - a second value for the same field becomes a list (`relationship, channels, tags, dex_id, handshake_id, outreach_id, …`) |
| Owner-only | written only by the owner, in Obsidian or an app's edit form; an AI writer may suggest a value under `## Updates` but never sets it (`tier, energy, known_for, recall_trigger, conversation_stage, buyer_role, fit, list_of_20, ## How they communicate`) |
| Forward-only | only moves forward in time (`last_contact, last_give, last_ask, quiet_until`) |
| Append | add dated lines, never edit or delete existing ones (`## Context, ## Current state, ## Future state, ## Needs, ## Can help with, ## Facts, ## Interpretations, ## Interaction log, ## Updates`) |

The one thing that DOES replace a Filled value: the owner editing it directly (in Obsidian or the cockpit). That always applies, and appends `- {date} · {field}: {old} → {new} (owner)` under `## Updates` so the history is never lost.

Appended lines end with an idempotency marker (`<!-- bc:… -->` for cockpit, `<!-- vq:… -->` for Handshake) so re-applying the same fact twice is a no-op.

### **Conversation (`12-Conversations`) - a recording with more than one voice**

yaml  
type: conversation  
attendees: \[\]             \# \[\[person-id\]\] - CONFIRMED in triage, never written by the pipeline  
speakers: \[\]              \# raw labels exactly as the device wrote them  
transcript\_source:         \# plaud | whisper - which engine produced the body  
duration\_min:

Body: the speaker-labelled transcript **verbatim and whole** (§8), plus `## Summary` when the capture device produced one, marked `<!-- origin: ai · plaud -->` so the provenance firewall (§1) still holds inside the note.

A conversation is recognised either by an explicit `#conversation` capture tag or, absent one, by its transcript carrying two or more speakers - the speaker-count check is a fallback, not the only way in. `attendees` is the one field the pipeline may **suggest** but never write: it is filled when a human confirms the note in triage, which is also what appends the dated line to each attendee's `## Interaction log` (§7 Person). That keeps CLAUDE.md §3 intact - no AI bulk-write reaches a person note unreviewed.

## **8\. CAPTURE / PROCESSING RULES**

* **One recording \= one note, kept at FULL LENGTH. NO splitting into atomic notes.** The watcher keeps the transcript whole in the body; it only *additionally* extracts action items to `06-Todos/<date>.md`.  
* A long recording is **transcribed in segments and stitched back together** (10-minute chunks past 15 min / 20 MB, with `[hh:mm]` markers between them). That is a request-size workaround, not a split: the result is still ONE note at full length. A segment that can't be transcribed leaves `[N minutes unintelligible - audio archived]` in place, so one bad stretch never costs the rest of a meeting.  
* Hindi speech is transcribed in **Devanagari**, then transliterated to **Roman Hindi / Hinglish**, which becomes the note body; the Devanagari original is kept in the same note under `## Original (Devanagari)`. Classification and action-item extraction read the Hinglish text. If no transliteration engine is configured or reachable, the note keeps the Devanagari transcript - a capture is never lost to a formatting step.  
* Processing delay is accepted; "✅ Captured" is the instant trust signal.  
* Every bulk AI write goes through a **review gate \+ git commit**. AI-generated notes carry `origin: ai`.

## **9\. NAMING CONVENTIONS**

* Note files: `YYYY-MM-DD-kebab-title.md` (the `id` in frontmatter is the durable handle; the filename is for humans).  
* Daily notes: `01-Journal/YYYY-MM-DD.md`. Todos: `06-Todos/YYYY-MM-DD.md`. Reflections: `08-Reflections/YYYY-MM-DD-weekly-reflection.md`.
* **Exception:** `07-People/` and `11-Companies/` notes written by **handshake** use a stable entity slug (`kebab-name.md`, no date prefix) instead - handshake's own sync stores the resolved path once and reuses it, and a predictable slug is the fallback if that ever has to be recomputed. This is a handshake-side convention, not a requirement on the folder itself: the cockpit's own person-note writer (`pipeline/relationships.py new_person_note`) still uses the standard `YYYY-MM-DD-kebab-name.md` form, and resolves people by frontmatter `id`, never by filename - so both conventions can coexist in `07-People/` without conflict. Expect the folder to hold both filename shapes.
* The vault's own `.gitignore` excludes `.obsidian/workspace*.json`, `.obsidian/cache`, `.trash/`, and `.DS_Store` - these churn on every Obsidian focus/close and would conflict on nearly every sync between two machines.

**Type → folder** (mirrors `pipeline/route.py` `TYPE_FOLDER` exactly - **keep the two in sync**; any change here or there must change both in the same commit):

| Type | Folder |
| ----- | ----- |
| *(any note below the confidence threshold - parked for review)* | `00-Inbox` |
| journal | `01-Journal` |
| musing | `02-Musings` |
| learning | `03-Learnings` |
| resource | `04-Resources` |
| project | `05-Projects` |
| todo | `06-Todos` |
| person | `07-People` |
| reflection | `08-Reflections` |
| decision | `09-Decisions` |
| principle | `10-Principles` |
| insight | `wiki/` |
| company | `11-Companies` |
| conversation | `12-Conversations` |

`raw/` is user-managed and outside this table - the pipeline never writes there.

