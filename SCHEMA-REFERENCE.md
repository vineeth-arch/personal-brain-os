# **SCHEMA-REFERENCE — the canonical schema (lock before note \#1)**

**This is the single source of truth for structure.** Every note, every script, every AI routing pass reads from here. These conventions are **migrations if changed late** — they're locked at Phase 0.4, before note \#1. Features can change; this should not. Version: 1.0 · 2026-06-05

---

## **1\. THE NON-NEGOTIABLES (impossible/painful to retrofit)**

* **Fixed `id`** on every note: an immutable timestamp `YYYYMMDDHHmmss`. **All links and typed edges point to the `id`, never to a title or path.** Rename/move freely; links never break.  
* **`origin: human | ai`** on every note: provenance. You can NEVER reconstruct later what was AI-written vs yours. Set it at creation.  
* **`raw/` vs `wiki/`** folders: `raw/` is user-managed and outside the pipeline's numbered scheme — the pipeline never reads or writes it; it holds immutable sources you place there yourself, never edited. `wiki/` also sits outside the numbered scheme, but the pipeline **does** write to it — it is the real destination folder for `insight`-type notes (atomic synthesis, one idea per note). The human/agentic firewall is the **`origin: human | ai`** field (the first non-negotiable above), NOT the folder: `wiki/` can hold both human- and AI-authored insight notes, told apart by frontmatter.

## **2\. UNIVERSAL FRONTMATTER (every note, all domains)**

yaml  
\---  
id: 20260605143000        \# immutable timestamp YYYYMMDDHHmmss — links point here  
type: musing              \# see type list below  
created: 2026-06-05  
source: voice             \# voice | share | web-clipper | dex | manual | ai-compiled  
origin: human             \# human | ai  (provenance firewall)  
status: active            \# lifecycle per type (section 6\)  
categories: \[\]            \# what it IS    — as \[\[links\]\] (Kepano model), not flat tags  
subjects: \[\]              \# what it's ABOUT — as \[\[links\]\]  
tags: \[\]                  \# from \_System/vocabulary.md (controlled vocabulary)  
\---

**Types:** `musing · learning · todo · journal · project · person · resource · decision · principle · insight · reflection`

## **3\. NAVIGATION SUBSTRATE (decided now; Bases views built later)**

* Organize by **`categories` \+ `subjects` as link-properties**, NOT deep folders. Folders are coarse domain bins; navigation is by property \+ Bases views.  
* `categories` \= what a note *is* (e.g. `[[Recipe]]`, `[[Person]]`, `[[Decision]]`). `subjects` \= what it's *about* (e.g. `[[Branding]]`, `[[ADHD]]`, `[[Dubai market]]`).  
* Link the first mention of any meaningful entity (build the graph as you write).

## **4\. CONTROLLED VOCABULARY**

* All `tags` come from `_System/vocabulary.md`. Don't invent synonyms ("AI" vs "a.i."). Add new tags to vocabulary.md deliberately, prune in review.  
* **Capture/routing tags (8 max):** `#todo #idea #journal #learning #person #resource #decision #project`. A tag present at capture routes the note for FREE (no AI classify).

## **5\. TYPED EDGES (fix the vocabulary now; auto-compile is Phase 2\)**

Use these relation types in `wiki/` notes; each edge points to a fixed `id`: `supports · contradicts · derived-from · depends-on · part-of · informed-by` Format in body: `- supports:: [[20260601090000]] (one-line why)` — the *why* is required (a link without a reason is noise).

## **6\. STATUS LIFECYCLES (per type — the "no dead list" guarantee)**

| Type | Lifecycle |
| ----- | ----- |
| resource | `inbox → to-consume → consumed → referenced → archived` |
| decision | `open → resolved` (+ `brier`, `process_grade` on resolve) |
| todo | `open → done` |
| project | `active → slipping → done | dropped` |
| person | `active → cold → dormant` |
| musing/learning/insight | `active → archived` |

## **7\. PER-DOMAIN SCHEMAS (extra frontmatter on top of universal)**

### **Knowledge notes (musing → `02-Musings`, learning → `03-Learnings`, insight → `wiki/`)**

Atomic (one idea), **your own words**, with a `derived-from` edge to its source. No extra required fields. All three share the same lifecycle (`active → archived`, §6) and are the three folders the daily resurfacing pick samples from.

### **Resource (`04-Resources/*`)**

yaml  
type: resource  
resource\_type: tool       \# tool | tutorial | book | movie | recipe | place | article  
title:  
cover:                    \# attachments/\<file\> — book cover / poster / screenshot / thumbnail  
source\_url:  
archive\_url:              \# private YouTube / Amazon Photos pointer (heavy media lives there)  
description:              \# LLM, one line  
insight:                  \# YOUR voice, verbatim — never overwritten by AI  
status: inbox  
rating:                   \# 1–7, after consuming  
captured:  
consumed:

Type extras — book: `author`; movie: `where_to_watch`, `runtime`; tutorial: `steps`, `tools_mentioned`, `transcript`; recipe: `ingredients`, `steps` (maps to Mom's Kitchen); place: `map_url`, `best_time`.

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

### **Person (`07-People`) — see RELATIONSHIP-OS-ARCHITECTURE.md**

yaml  
type: person  
relationship:             \# friend | client | collaborator | prospect  
company:  
channels: {whatsapp:, email:, linkedin:}  
dex\_id:  
dex\_deeplink:  
cadence\_days:  
last\_contact:  
warmth\_stage:             \# identified | researched | engaging | conversing | warm | ready  
status: active            \# active | cold | dormant

Body: `## Context` · `## Needs` · `## Interaction log` (append-only, dated) · `## Next action`.

## **8\. CAPTURE / PROCESSING RULES**

* **One recording \= one note, kept at FULL LENGTH. NO splitting into atomic notes.** The watcher keeps the transcript whole in the body; it only *additionally* extracts action items to `06-Todos/<date>.md`.  
* Processing delay is accepted; "✅ Captured" is the instant trust signal.  
* Every bulk AI write goes through a **review gate \+ git commit**. AI-generated notes carry `origin: ai`.

## **9\. NAMING CONVENTIONS**

* Note files: `YYYY-MM-DD-kebab-title.md` (the `id` in frontmatter is the durable handle; the filename is for humans).  
* Daily notes: `01-Journal/YYYY-MM-DD.md`. Todos: `06-Todos/YYYY-MM-DD.md`. Reflections: `08-Reflections/YYYY-MM-DD-weekly-reflection.md`.

**Type → folder** (mirrors `pipeline/route.py` `TYPE_FOLDER` exactly — **keep the two in sync**; any change here or there must change both in the same commit):

| Type | Folder |
| ----- | ----- |
| *(any note below the confidence threshold — parked for review)* | `00-Inbox` |
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

`raw/` is user-managed and outside this table — the pipeline never writes there.

