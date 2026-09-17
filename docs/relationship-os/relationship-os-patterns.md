# Layout and IA: Relationship OS
> v2.2 · 2026-09-17 (Greene panel in the composer, per GREENE-HELPER.md §6) · v2.1 (Standing layer: Hold, presence lints, quiet state, reliability line, reputation page) · shared by Brain OS and Handshake. Implements RELATIONSHIP-OS-ARCHITECTURE.md Part A (the Doctrine). Where this doc and the Doctrine disagree, the Doctrine wins.

## Information architecture

Two audiences, two zones:

**Owner app** (authenticated, dense): Home · People · Companies · Import ·
Proposals · Scanner · Desk · **Review**. Persistent top nav. Every owner page
sits in `components/page-frame.tsx`:

- Below 1024 px: one reading column, `max-w-3xl`. Phones and tablets are
  unchanged.
- From `lg` (1024 px): the frame widens to `max-w-7xl` and data pages use
  columns: Today's queues, the People table, the person page (record left,
  working surfaces right), desk panels, proposals, integrations. Keep DOM
  order equal to the phone order; place columns with grid, not reordering.
- Prose (forms, help, import, scanner, message history, review) keeps a
  readable width (`lg:max-w-3xl` / `lg:max-w-4xl`), left-aligned. Never a
  narrow column centred in a wide page.

**Public surfaces** (no auth, visitor-facing): `/card/[slug]`,
`/c/[slug]`. Different rules entirely; see below.

## Home is a queue, not a dashboard

**Today strip** (top of Home): at most **five** touches chosen by
`morning_engine.py`. Each row: name · tier dot · queue label · the payload in
one line · one primary action. Under it, one muted line: "9 more, none
urgent". Overflow is never rendered as a backlog list on Home. The cap is a
product rule: five touches that carry something beat twenty that do not, and
an ADHD brain abandons a list it cannot finish.

Below the strip, eight views, fixed order, each a count plus a list:

1. I owe a reply
2. Promises I made
3. Ask about
4. Celebrate
5. Follow up today
6. Could help
7. Waiting on them
8. Reconnect

Order is the Doctrine's priority: what you owe comes before what you
initiate. Promises moved up from fifth because a kept promise is the
strongest reliability signal the owner controls.

No charts, no vanity metrics, **no streaks**. A view with zero items
collapses to a single muted line. It does not vanish (absence is
information) and it does not shout.

"Ask about" is what makes the owner remember, not only record: an exam, a
trip, a promise someone else made, approved as an `upcoming` or
`commitment_theirs` proposal, resurfaces here the day after, worded as a
question ("Ask how it went: …"), not a task.

**Celebrate** holds milestones and `dates` within 7 days, plus
`give_theirs` items awaiting a thank-you or a 30-day outcome report.

**Could help** holds intro-matcher cards: "Meera needs a short-run pouch
vendor · Sanjay offers short-run pouches · both in your core tier". Actions:
*Ask Meera first* (primary, opens a double opt-in draft), *Not a fit*
(ghost). The system never contacts anyone.

**Waiting on them** rows show the stalled-thread rung (1 to 4) and the date
the next rung is due. Rungs 3 and 4 appear only for people behind the
commercial gate.

**Reconnect obeys the Payload Rule.** A row shows its payload candidate. If
the engine found none, the row reads "No payload yet. Find one." and expands
to three prompts (what were they last working on, who should they meet,
what have I learned that applies to them). It offers no draft. `inner` tier
rows may offer a `presence` draft.

**Quiet and floor.** A person inside their contact floor or with
`quiet_until` in the future never appears in Reconnect, Could help or the
Today strip. On their person page a single muted line explains why:
"Quiet until 4 Nov: two messages unanswered. Nothing to do." The wording
is neutral on purpose. It is a rest, not a failure, and there is no
"message anyway" button beside it (the owner can still open a blank
composer from the header).

## Proposals are decisions about memory, not sentences

Approving a card is not "save this text". It decides what gets remembered
and, for some types, when it resurfaces. Every card states what it becomes
(`→ Ask about on 13 Mar`, `→ About them · family`, `→ Current state`,
`→ Thank today · report outcome 17 Oct`, `→ Intro check-in 1 Oct`) before the
owner decides, with inline controls (a date, a topic, a "remind me"
checkbox) where that outcome can be edited.

New card types and their outcome lines:

| Type | Outcome line |
|---|---|
| problem | `→ Current state` (impact shown if they stated one) |
| goal | `→ Future state` |
| offer | `→ Can help with` (feeds the intro matcher) |
| intro | `→ Log · check in on {date + 14}` |
| give_mine | `→ Log · counts as a give` |
| give_theirs | `→ Log · thank today · report outcome {date + 30}` |
| important_date | `→ Dates · remind yearly` |
| reputation_signal | `→ Reputation page · log` |

Cards touching `health`, family difficulty or money carry a quiet
"sensitive" tag. Approving stores them; they stay out of drafts unless the
owner taps "include" on a specific touch.

Bulk actions are reject-only, and never blind: a scoped view (unknown
numbers never replied to, saved contacts never replied to, AI-tagged
"likely junk") lets the owner glance through and spare anyone before
"Reject the rest". Never a single button that fires on a count alone. Every
bulk reject previews first and can be undone. AI may tag a card (a type, a
topic, "junk"). It never decides a card's fate.

## Person page order

**Header** · name · relationship chips · **tier** · status · **ledger line**
(`gives 90d 4 · asks 0 · received 1`) · **reliability line** (`their
promises: 5 kept · 1 late · 2 dropped`, facts only, muted) · last contact ·
preferred channel and language. `energy` shows as a small owner-only glyph,
never as a word, and only at desk width.

**Known for** (one line, editable): what this person should associate the
owner with, and the `recall_trigger` beside it in muted text.

**Before you talk** (collapsed by default, one tap to open): the four reads
pre-filled for a call rather than a message, then current state
in two lines · last promise by either side · the open Ask about · how they
communicate · one pending kind truth if the owner has noted one. Built for
a thirty-second read before a call.

Then: profile · **I promised** · **They promised** · **Open follow-ups** ·
**Ask them about** · **What's going on for them** (Current state) ·
**Where they're headed** (Future state) · **They're looking for** (Needs,
with any *Could help* match inline) · **They can help with** · **How they
communicate** (heading suffix: "my read") · **About them** (facts grouped by
topic: family, health, home, interests, preference, favour; family first) ·
**Remember** (untopiced facts) · **Interpretations** (kept apart, heading
says so) · **Introductions and referrals** (made, received, outcome) ·
**Recent messages**.

Behind the commercial gate only, a **Working together** block sits under
the header: conversation stage · buyer role · fit. If the only known
contact at a company is not the economic buyer, the block says so in one
plain line. For people whose relationship is only family or friend this
block does not render at all.

## Draft composer

**Greene panel** sits above the draft field, collapsed to one strip by
default (GREENE-HELPER.md §6.1). Expanded it is one column at desk width
and a bottom sheet on mobile.

- The strip shows the four reads with the person's data already in them:
  `Calm?` · `Pride: {pride: line, or blank}` · `Record: {reliability read}`
  · `Outcome:` (one text field, the only thing the owner types).
- **Hold** lives in this panel, not the toolbar, because reading yourself
  comes first. Tapping it saves the draft until 9 am tomorrow and pauses
  the reply-owed flag.
- **Situation picker:** the 18 rows, searchable, preset from `touch_type`
  and queue where the mapping is obvious. A chosen row shows the trap, the
  move and the line, with "Use this line" dropping a skeleton into the
  composer. Never auto-fills, never sends.
- **Anti-Seducer check** runs on the composed text and renders only the
  chips that fire (windbag, moraliser, tightwad, reactor, bumbler, pushy),
  in the same quiet underline style as the linter.
- The panel reads the person note and writes nothing. On send, the log line
  carries the situation code (`greene:3.4`) and nothing else.
- Blank `pride:` or an empty reliability read renders as a muted dash, not
  a prompt to go fill it in. The panel never nags.

- The composer opens with a **payload chip** (`remember · Aarav's boards`)
  that links to the source line. No chip, no AI draft (the owner may still
  type freely).
- **Touch type** selector, preset from the queue. It is written to the log
  on send.
- **Linter, inline and quiet.** Blocked softeners are underlined with the
  fix beside them: "no rush → say the date and why". An ask or an open next
  step with no date shows "Add a date". Em dashes and emojis are flagged.
  The linter warns; it never blocks the owner's own words.
- **Presence lints** (A9.13 to 18), same quiet underline style: "say
  less" past the length budget · "state it once, then offer a choice" on a
  justification chain · "keep this for the inner circle" on complaint ·
  "name the struggle or credit someone" on an own-win message · "appeal to
  what they gain" when a draft invokes past favours · "leave the decision
  with them" on a kind truth that lacks it.
- **Hold** (ghost button, left of the primary action): "I'm irritated. Hold
  until tomorrow 9 am." The draft is saved, the reply-owed flag pauses, and
  tomorrow it returns at the top of *I owe a reply* with the text intact
  and one line above it: "Still want to send this?"
- Closing a **They promised** item asks one question with three ghost
  buttons: kept · late · dropped. No comment field.
- Any sentence that relies on an `## Interpretations` line is marked
  "this is your read, not something they said".
- Two variants on request: best version, and a shorter or more assertive
  one. Email drafts carry a subject line.
- Draft actions are links that open the owner's own app. Label them by
  destination ("Open in WhatsApp"), never "Send". The system does not send,
  and the wording must not imply otherwise.
- After the owner confirms they sent it: one tap logs
  `{date} · out · {channel} · {touch_type} · {summary}` and resets
  `last_contact`.

## Review (weekly, 20 minutes)

One prose-width page, six short sections in fixed order: three fears audit
(three one-line text fields) · softener audit (sent messages with open-ended
next steps, each with "set a date") · ledger flags · tier moves (caps shown
as `inner 14/15`) · List of 20 (stage per target) · unmade *Could help*
matches · Anti-Seducer audit (six chips: windbag, moraliser, tightwad,
reactor, bumbler, pushy; tap any that applied, add one name and one repair)
· self-possession (one text field, plus any held drafts) · in person this
week (yes/no). Saving writes a reflection note. Skipping a week shows nothing
punitive the next week: no red, no "you missed".

**Reputation** (monthly, a sub-page of Review): two columns at desk width.
Left: `known_for` from `_System/positioning.md`. Right: approved
`reputation_signal` lines, newest first, each with who said it and when.
No score, no sentiment analysis. The owner reads and judges the gap.

**Metrics live here, not on Home**, as windowed counts ("payload in 11 of
the last 13 touches", "promises kept 9 of 10 in 30 days"). Never a streak,
never a chart on Home.

## Page skeleton

```
<h1>            page title
<p muted>       one line of orienting context
<section>       repeated: xs uppercase muted heading + content
```

Sections separate with vertical rhythm (`mt-8`), not rules. Use
`<Separator />` only where a boundary is genuinely ambiguous.

## Evidence and provenance are visible, quiet

Every AI-derived claim shows its source and stays in
`text-muted-foreground`. Facts and interpretations never share a list:
separate sections, and the interpretation heading says so in words. This
is a product rule, not a style choice: the system must never let a guess
read as a fact. The same rule now governs drafts: a remembered detail in a
message must trace to a stated line.

## Actions

One primary action per view (`Button` default). Everything else is
`outline` or `ghost`. Destructive actions are `destructive` variant and
always confirm.

## Empty states

Say what is missing and give the one action that fills it. "Queue is
empty. Import a chat and run extraction to fill it." "No current state yet.
After your next call, record what is going on for them." Never a bare "No
data".

## Public card surfaces

Different constraints: one thumb, poor light, expo 4G, a stranger who
installs nothing. Single column, large tap targets (min 44px), the save
action above the fold, no navigation chrome, no auth. Owner brand may
apply here; the internal app stays neutral.

The card carries **one line of positioning** under the name, taken from
`_System/positioning.md`: the problem the owner solves and for whom. A
stranger who saves the contact should be able to say later what the owner
is the person for. Nothing from any person note, tier, ledger or
interpretation ever renders on a public surface.

## Responsive

Two widths matter: **390px** (the owner's phone, and every visitor) and
**1440px** (the owner's desk). Lists stack; the desk table scrolls inside
its own container rather than the page scrolling sideways. At 1440 px data
pages use columns and prose stays at a reading width; at 390 px nothing
changes. The Today strip is the first thing on screen at both widths.
