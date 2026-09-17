# Relationship OS: Architecture & Build Spec
### The relationship engagement & record system (shared by Brain OS and Handshake)
> Canonical doc. Consistent with EXECUTION-PLAN.md (Phase 4), IDEAS-BACKLOG.md, ARCHITECTURE-MAP.md. Companion docs: GREENE-HELPER.md (the reply-time model), LIFE-OS-ARCHITECTURE.md, RESOURCE-OS-ARCHITECTURE.md, relationship-os-patterns.md, SCHEMA-REFERENCE.md (Handshake), SCHEMA-REFERENCE_1.md (Brain OS).
> Version 2.2 · Last aligned: 2026-09-17 (GREENE-HELPER.md added as the fifth shared doc: the Four Reads model and the 18-situation lookup, surfaced as the Greene panel in both composers; A11.6).
> v2.1 · 2026-09-17 (A11 Standing layer from Robert Greene: fifth cause, contact floor, quiet rule, hold, presence lints, reliability read, reputation signals, three-pile filter).
> v2.0 · 2026-09-17 (Relationship Doctrine added: tiers, touch taxonomy, payload rule, give/ask ledger, current/future state, commercial module, draft rules, reviews).
> Previously: 2026-09-17 v1.x (Handshake proposal-type → section mapping) · 2026-06-05 (shared audio-first capture membrane, person-note schema, OneDrive/Syncthing sync, Phase 0.4 schema lock-in, feeds the unified graph).

---

## ONE-LINE MODEL
**Dex** is the engagement engine (integrations, reminders, capture). **Obsidian `07-People`** is the owned, durable record. Data flows one way into the record (append-only). A proactive engine pushes a short list each morning of **who to touch and what to bring them**. A warm-up engine nurtures new high-value relationships. **Nothing ever auto-sends.** You always press send.

The v2.0 change in one sentence: the system no longer asks "who have I not spoken to?" It asks **"who can I be useful to today, and with what?"** Cadence is only the safety net underneath that question.

## WHAT CHANGED IN v2.0
1. A **Doctrine** (Part A) that defines what a good touch is, grounded in the ten-book library. Every engine and every draft obeys it.
2. **Tiers** with default cadences sized for 50 to 150 actively kept relationships, and a **daily cap of 5 surfaced touches**.
3. A **touch taxonomy** (10 types) and the **Payload Rule**: a reconnect never surfaces empty.
4. A **give/ask ledger** computed from the interaction log. The system will not propose an ask to someone you have not given to.
5. Person notes gain **Current state, Future state, Can help with, How they communicate** and the fields `tier`, `known_for`, `recall_trigger`, `fit`, `conversation_stage`, `buyer_role`, `list_of_20`, `referred_by`, `dates`, `language`, `preferred_channel`, `last_give`, `last_ask`.
6. Seven new proposal types: `problem`, `goal`, `offer`, `intro`, `give_mine`, `give_theirs`, `important_date`.
7. An **intro matcher** (their need × someone else's offer) that runs nightly.
8. **Draft rules** with a linter (banned softeners, date-anchored asks, no fabricated memory).
9. A **weekly review** and **windowed metrics**. No streaks (RESEARCH.md §1).

### Added in v2.1 (the Standing layer, A11)
10. A fifth cause of the goal: **Standing** (people take you seriously). Warmth without standing reads as eagerness.
11. A **contact floor** per tier and a **quiet rule** after two unanswered messages. Cadence was a maximum gap; there is now a minimum too (48 Laws, Law 16, including its Reversal).
12. A **Hold** state on any reply written while irritated (Laws of Human Nature: increase your reaction time).
13. **Presence lints** in the draft linter: length budget, justification chains, complaint, boasting, invoking past favours.
14. A fact-based **reliability read** per person (their promises kept vs dropped) and an owner-only `energy` field (Law 10).
15. A new proposal type `reputation_signal`: what people say you are known for, collected in `_System/reputation.md` (Law 5).
16. A three-pile filter over Greene's four books: adopted, adapted, excluded, with reasons.

## DESIGN STANCE
- **Two-tool split, clear jobs.** Dex = engagement layer (syncs Gmail/LinkedIn/WhatsApp/Calendar, reminders, mobile capture). Obsidian = system of record (notes, context, history, your voice). Drop Dex anytime and the markdown archive is complete. Ownership preserved.
- **One-way, append-only into the record.** Dex → Obsidian enriches. The record is never silently overwritten (provenance via `origin`).
- **MCP-first.** Claude operates through MCP servers (Dex, Obsidian, Gmail, Calendar), not brittle scripts where a tool exists.
- **Proactive, not passive.** The system tells you who to touch and drafts the touch. You do not have to remember or open it.
- **Nothing auto-sends.** Every message is drafted in your voice and queued for one-tap manual send. Non-negotiable (trust and platform compliance).
- **Payload over frequency (new).** A touch with nothing in it costs goodwill. The system would rather surface 3 touches that carry something than 12 that say "just checking in".
- **Memory is the product (new).** What makes people smile when your name appears is that you remembered and you delivered. The record exists to make both reliable.
- **One playbook, one gate (new).** The same doctrine governs family, friends, clients and prospects. One gate separates them: commercial instruments (gap sizing, asks, stalled-deal language) only activate where a commercial context exists. See A3.

---

# PART A: THE DOCTRINE

## A1. The goal, restated as a mechanism
Stated goal: be top of mind, remembered with a smile, the person people reach out to first.

That is an outcome. The system can only build its four causes:

| Cause | What it means | Built by | Source |
|---|---|---|---|
| **Association** | When they think of a specific problem, your name is attached to it | `known_for` on every note; the same claim said the same way everywhere | Baker (positioning makes you less exchangeable); Enns (claim of expertise) |
| **Trigger** | A situation in their life that fires the association | `recall_trigger` on the note; touches timed to that situation | D'Souza (the brain responds to problem and trigger before solution) |
| **Warmth** | Hearing from you feels good because it is about them | Ask about, celebrate, thank, remember | Port (share how you feel; the giver's stance); Lencioni (make everything about the client) |
| **Reliability** | What you say will happen, happens, on the date you said | Promises I made, date-anchored messages, kept-on-time metric | Enns (the sale is the sample: how you behave before the work is how they expect you to behave in it); Lencioni |
| **Standing** | They take you seriously: your time has a price, your words are few, your work speaks | contact floor, quiet rule, Hold, presence lints, gives that are never free deliverables, visible mastery | Greene: 48 Laws (4, 5, 9, 16, 34, 40), Mastery (speak through your work); Enns (expert, not vendor) |

If a feature does not strengthen one of these five, it does not belong in this system.

**Respect and adoration, mapped.** Respect is Standing plus Reliability. Being adored is Warmth: specifically, people feel better about *themselves* after dealing with you (Greene's Charmer; Laws of Human Nature ch. 7). Neither can be requested or performed. Both are side effects of conduct repeated over years, which is exactly what a system is good at.

**Pragmatic note on net worth.** Referral revenue and career opportunity are downstream of these four causes, and they arrive with a lag you cannot schedule. Port's position is relationships first, business second. The system therefore tracks revenue attribution (`referred_by`, A8) as a lagging readout and never as a targeting input. If the engine ever ranks people by what they might be worth to you, the messages start to read that way, and the four causes collapse.

## A2. The seven laws
1. **Giver's stance first.** Share who you know, what you know, how you feel. (Port, networking strategy.)
2. **No empty touches.** Every surfaced touch carries a payload (A5).
3. **Promises are sacred.** A promise you made outranks everything in the morning push except a reply you owe. A missed promise is surfaced as overdue, never hidden.
4. **Understand before you advise.** Know their current state and where they want to go before offering anything. (Keenan.)
5. **Tell the kind truth.** When you see something that will hurt them, say it, with dignity. Do not sugarcoat, do not bludgeon. (Lencioni.)
6. **Expert posture, warm tone.** Warmth is tone, not deference. No vendor softeners. Every ask carries a date and a reason. (Enns.)
7. **Facts are facts, readings are readings.** A guess never reads as a fact, in the record or in a draft.
8. **Self-possession.** Say less than necessary. Never argue, justify, complain or chase. Reply to provocation tomorrow, not today. (Greene: 48 Laws 4 and 9; the Charmer's calm in adversity; Laws of Human Nature ch. 1.)

## A3. One playbook, one gate
Laws 1 to 7 apply to everyone. Lencioni himself extends naked service beyond clients to "the people we live and work with", so a single doctrine is defensible.

The gate: the following instruments are **disabled** for any person whose `relationship` list contains only `family` and/or `friend`:
- system-proposed `ask` touches
- `conversation_stage`, `buyer_role`, `fit`, gap/impact prompts
- stalled-thread language (the no-oriented question, the close-the-loop message)
- any Voss technique beyond listening mechanics (labels, mirrors, calibrated questions)

The owner can still write anything by hand. The system simply never drafts a sales instrument at a friend.

*Why the gate exists (kind truth):* the ten books are commercial. Keenan's gap sizing and Voss's loss framing work on a buying decision. Pointed at your mother or a school friend they are manipulation, and people feel it. This gate is extrapolation beyond the books; it is the price of running one playbook safely.

## A4. Tiers and cadence
Sized for 50 to 150 actively kept people. **The tier sizes and day counts are extrapolation** (the books give no numbers; the shape borrows from Dunbar-style layering). Port's List of 20 is the one grounded number.

| `tier` | Cap | Default `cadence_days` | Who | Bare "thinking of you" allowed? |
|---|---|---|---|---|
| `inner` | 15 | 14 | family, closest friends, the 3 to 5 professional relationships that matter most | Yes |
| `core` | 35 | 30 | active clients, best referrers, close peers, mentors | No, needs payload |
| `active` | 100 | 90 | past clients, warm prospects, wider peers | No, needs payload |
| `wide` | no cap | none | everyone else | Event-driven only (milestone, need match, reply) |

- `cadence_days` on the note overrides the tier default.
- **Load check:** 15×26 + 35×12 + 100×4 ≈ 1,210 cadence touches a year ≈ 3.3 a day. Event touches (Ask about, celebrate) reset the clock, so real cadence load runs lower. Hence the **daily cap of 5**.
- Caps are enforced softly: adding a 16th `inner` person prompts "who moves to core?" This is the Red Velvet Rope applied to attention (Port).
- `list_of_20: true` marks Port's List of 20: people you do not yet know well who can put you in front of ideal clients. They run through the warm-up engine (layer 5), max 20.

Status lifecycle is unchanged: `active → cold → dormant`, advanced by the heartbeat from `last_contact` vs effective cadence. `cold` = 1.5× cadence overdue. `dormant` = 3× overdue.

## A5. Touch taxonomy and the Payload Rule
Every outbound interaction-log line carries one `touch_type`.

| `touch_type` | Payload | Counts as | Source |
|---|---|---|---|
| `remember` | A question about something they told you (exam, trip, launch, surgery) | give | Port (compassion); Keenan (know their world) |
| `give_know` | An article, idea, reference, observation chosen for their stated problem or goal | give | Port (what you know) |
| `give_who` | An introduction, double opt-in | give | Port (who you know); Lencioni (give away the business) |
| `celebrate` | Milestone acknowledged with a specific detail | give | Port (how you feel) |
| `keep_promise` | The thing you said you would send or do | give | Enns (the sale is the sample) |
| `kind_truth` | An honest observation they need, offered with permission | give | Lencioni |
| `thank` | Specific credit for what they did, with the outcome | give | Lencioni (honor their work); Port (referral strategy) |
| `invite` | Something of yours they can join at no risk | neutral | Port (always have something to invite people to) |
| `ask` | A request: referral, advice, business, favour | ask | gated (A3, A6) |
| `presence` | No payload, only contact | neutral | `inner` tier only |

**The Payload Rule.** A person who is due by cadence is surfaced in *Reconnect* only when the engine can attach a payload candidate, in this order:
1. an open `## Next action` line (Ask about, promise, intro check-in)
2. a `## Needs` or `## Future state` line that matches another person's `## Can help with` or a resource in Resource OS (→ `give_who` / `give_know`)
3. a date in `dates` within 7 days (→ `celebrate`)
4. an unthanked `give_theirs` (→ `thank`)

If none exists the card reads **"No payload yet. Find one."** with three prompts: *What did they last say they were working on? Who do I know that they should know? What have I learned this month that applies to them?* It offers no draft. For `inner` tier it may offer a `presence` draft.

## A6. The give/ask ledger
- Computed nightly from interaction-log lines. `out` lines with a give-type add to gives; `out` + `ask` adds to asks; `in` lines of type `give_theirs` add to received.
- Stored on the note only as `last_give` and `last_ask` (Forward-only dates). Counts live in `today.json` and the person page header: `gives 90d · asks 90d · received 90d`.
- **Ask gate:** the system proposes an `ask` only if there is commercial context (A3), at least 3 gives since the last ask, and at least 90 days since `last_ask`. *The numbers are extrapolation; the direction is Port's giver's stance.*
- **Two imbalance flags in the weekly review:** "I only take" (asks ≥ gives over 180 days) and "one-way street" (8+ gives, zero replies or received over 180 days → tier review, not resentment).
- **The thank loop:** every `give_theirs` creates a Next action to thank now and a second one 30 days later to **report the outcome**. Telling a referrer what happened to their referral is the strongest cause of the next referral. (Port's referral strategy grounds the thank; the 30-day outcome report is extrapolation.)

## A7. Knowing the person
Keenan's five elements of current state, adapted from a buyer to a human being:

| Section | Holds | Proposal type |
|---|---|---|
| `## Current state` | what is literally going on in their work or life; problems they have named; the impact they described; the cause as *they* see it; how they said it feels | `problem` (+ existing `fact`) |
| `## Future state` | where they are trying to get to, in their words | `goal` |
| `## Needs` | what they are actively looking for right now | `need` |
| `## Can help with` | what they are good at, sell, or have offered | `offer` |
| `## How they communicate` | channel, language, reply rhythm, what lands, what to avoid | owner-written, or an approved `interpretation` prefixed `style:` |

Rules:
- Root cause and emotion are recorded **only when stated by them**. Your reading of either is an `interpretation` and lives in `## Interpretations`.
- Voss's three negotiator styles (Analyst, Accommodator, Assertive) may be noted in *How they communicate* as **my read**. Analyst: give data and time, no surprises. Accommodator: relationship first, then pin down the concrete next step because agreement is cheap. Assertive: be brief, let them feel heard first, then state your view plainly. Never shown as fact, never on a public surface.
- `known_for` and `recall_trigger` (A1) are per person because the association you want differs: for a D2C founder it may be "the packaging person who thinks about shelf and unit economics", for a fellow designer "the one who knows how to price".

**The "Before you talk" card** (patterns doc) compiles: their current state in 2 lines · the last promise either side made · the Ask about · how they communicate · one pending kind truth, if any. Thirty seconds of reading before any call or meeting. This is Keenan's discovery prep and Voss's one sheet reduced to what a relationship needs.

## A8. The commercial module (active only behind the gate)
| Field | Values | Use | Source |
|---|---|---|---|
| `conversation_stage` | `none · probative · qualifying · value · closing · delivering · past` | tells drafts what the next conversation is for; the engine never drafts a closing message to someone at `probative` | Enns, The Four Conversations |
| `buyer_role` | `economic · influencer · user · gatekeeper · unknown` | if your only contact is not `economic`, the person page says so: "You have not met the economic buyer" | Weiss (economic buyer is his term; the other labels are convenience) |
| `fit` | `ideal · good · poor · unknown` | `poor` is excluded from warm-up and from proposed asks; stays in the record | Port, Red Velvet Rope |
| `referred_by` | `[[person-id]]` | set once on the referred person; powers the thank loop and the lead-source readout | Port (track lead sources) |
| `list_of_20` | bool | warm-up engine target | Port |

Stage-aware drafting:
- `probative`: share perspective, no pitch. Goal is to be seen as the expert before any sale exists.
- `qualifying`: establish fit both ways, including budget and decision process. You are vetting them too.
- `value`: what they want, what it is worth, what they would pay. No proposal until this is agreed (Weiss: conceptual agreement first).
- `closing`: options and a date. Stalled-thread ladder (A9) applies.
- `delivering` / `past`: Lencioni territory. Kind truth, do the unglamorous work, report outcomes, stay in touch.

Company notes gain `fit`, `## Current state`, `## Future state` and `## Buying map` (who plays which `buyer_role`).

## A9. Draft rules (enforced by a linter in both apps)
Drafts are written from `_System/my-voice.md` plus these rules. Split them into `_System/draft-rules.md` if the apps load rules as a separate file.

1. **Open with them.** The first sentence is about their world, not yours.
2. **One payload per message.** Two ideas become two messages on two days.
3. **Specific beats warm.** "How did Aarav's boards go?" beats "Hope the family is well."
4. **Every remembered detail must cite a line in `## Facts`, `## Context`, `## Current state` or `## Next action`.** A draft never uses an `## Interpretations` line as if it were known. No fabricated memory, ever.
5. **Sensitive topics** (`health`, family difficulty, money) are excluded from drafts unless the owner taps "include" on that touch.
6. **No vendor softeners.** Blocked: "no rush", "no pressure", "whenever you get a chance", "whenever works", "just checking in", "touching base", "circling back", "hope this finds you well", "sorry to bother", "if you get time". The linter offers the fix: a date and the reason for it.
7. **Every ask and every open next step carries a date tied to a real reason.** If they leave a step open, the reply converts it to a date.
8. **No flattery.** Honour their work by naming the specific thing (Lencioni), or say nothing.
9. **Match channel and language.** WhatsApp: 2 to 4 lines, `language` from the note (English, Hinglish, Hindi, Marathi). Email: subject line, half professional, half spartan.
10. **Humanizer rules apply to every draft:** no em dashes, no stock AI vocabulary, no groups of three for rhythm, no bold inline headers, no emojis, no sycophantic opener, no summary closer, active voice, uneven sentence length.
11. **Listening mechanics are always allowed** (Voss): a label ("Sounds like the relaunch has been heavier than you planned"), a mirror, a calibrated what/how question. The aim is that they reply "that's right".
12. **Intros are double opt-in.** Ask the person with the need, then the person who can help, then connect them in one message that says why each should care. *(Etiquette extrapolation; not in the books.)*

**Presence lints (v2.1, from A11). Warnings, never blocks:**

13. **Length budget.** WhatsApp over 4 lines, or email over 120 words, is flagged "say less". (Law 4: the more you say, the more common you appear and the likelier you say something foolish.)
14. **No justification chains.** More than two consecutive sentences defending a price, a decision or yourself is flagged "state it once, then offer a choice". (Law 9: win through actions, never through argument.)
15. **No complaint outside the inner tier.** Venting about clients, workload, money or other people is flagged. (The Charmer: be a source of pleasure; never whine, never complain, never try to justify yourself.)
16. **Own wins are shared envy-safe.** A message announcing your success must name the struggle or credit someone, and never compares. To anyone senior (mentor, client, elder) it credits their part first. (Law 46: never appear too perfect. Law 1: never outshine the master. Mastery: envy.)
17. **An ask appeals to their interest, never to your past gives.** Any phrase like "after everything", "I've always helped you", "you owe me" is flagged. The ledger decides whether you may ask; it is never mentioned in the ask. (Law 13.)
18. **Leave their self-opinion intact.** Before any `kind_truth` or disagreement the draft must do one of: acknowledge their expertise, leave the decision explicitly with them, or credit their intent. Never imply they were foolish, pushed or careless. (Laws of Human Nature ch. 7: autonomous, intelligent, good.)
19. **Hold.** If the owner marks a reply "irritated", the composer saves it as held until 9 am next day and the *I owe a reply* 48 h flag pauses. (Laws of Human Nature ch. 1: increase your reaction time.)

### Reference scripts (in your context; edit into your voice file)
`remember`
> Rohan, Aarav's board results were due this week. How did he do?

`give_know`
> Meera, you said quick commerce returns are eating your margin because pouches burst in transit. This teardown shows how one snack brand changed the seal and gusset to fix it. Page 4 is the part that applies to you.

`give_who` (step one of the double opt-in)
> You mentioned you need a food-grade pouch vendor who will run under 5,000 units. I know one in Vasai who does that. Want me to connect you? I'll check with him first and come back to you by Friday.

`celebrate`
> Saw the funding news. Two years ago you were packing orders in your living room. You earned this one.

`keep_promise`
> I said I'd send the printer's contact by Wednesday. Here it is: [name, number]. He knows your call is coming.

`kind_truth`
> Can I tell you something I noticed on the new site, as someone who wants it to work? The first line talks about your process, and a new visitor can't tell what you sell in five seconds. I can show you what I mean in ten minutes. I'm free Thursday after 4.

`thank` (outcome report)
> The intro to Kiran became a packaging project that starts on the 1st. That came from you. Thank you. I'll tell you how it lands.

`invite`
> I'm running a 45-minute session on shelf impact for D2C founders on the 26th at 5 pm. You told me your redesign is planned for Q4, so the timing fits. Want a seat? I close the list on the 22nd.

`ask` (referral; only behind the gate)
> I'm taking on two packaging projects for October. The best fit is a food or wellness brand that has outgrown its first pack. If someone comes to mind, I'd value an intro. I finalise the October slate on the 25th.

`presence` (inner tier)
> Walked past that vada pav place near Ruia today and thought of you. Call on Sunday?

**Stalled-thread ladder** (commercial, *Waiting on them* queue):
1. Day 0: the ask, with a date and the reason for it.
2. Date + 1 working day: one line restating the decision needed and the new date.
3. Date + 5: the no-oriented question (Voss): "Have you given up on the rebrand for this year?"
4. Date + 10: close the loop (Enns, willingness to walk): "I haven't heard back since the 3rd, so I'm releasing the October slot I was holding for you this Friday. If the project comes back to life, message me and we'll look at the next opening."
Then the person drops to cadence. No fifth message.

## A10. Where the authors disagree, and the call
- **Liked vs useful.** Port and Lencioni: people buy from those they like and trust. Keenan: people buy from those who understand their problem and can change it. *Call:* the system builds warmth through usefulness. `remember` and `give_*` touches are both at once. Pure sociability (`presence`) is reserved for the inner tier.
- **Leverage vs relationship.** Voss's deadline, anchoring and loss-aversion tactics are excluded from the system entirely. Only his listening mechanics and the one no-oriented question survive, and the latter only behind the gate. For relationships meant to last decades the relational end of the spectrum is the right default. Trade-off accepted: you will occasionally leave some negotiating leverage unused.
- **Automation vs sincerity.** Port says a keep-in-touch strategy that is not automated does not exist. He means one-to-many broadcast to people who opted in. One-to-one messages here are drafted, never sent, and must pass the Payload Rule. A newsletter (permission-based, Port) is a separate leveraged channel and out of scope for this doc.
- **Generosity vs focus.** Port's giving has no stated limit; Baker and Enns demand focus. *Call:* tier caps. Generosity is unlimited in spirit and budgeted in attention.
- **Greene vs Lencioni on criticism.** The Charmer never criticises overtly; Lencioni says tell the kind truth even at the cost of the account. *Call:* the truth stays (doctrine law 5 is not negotiable), and Greene governs the delivery: in private, one point, self-opinion confirmed first (A9.18). Flattery tailored to insecurities, which Greene recommends, is excluded.
- **Greene vs Port on availability.** Law 16 says withdraw; Port says keep in touch or be forgotten. Greene's own Reversal settles it: absence only works after presence is established, and early on you should be everywhere. *Call:* new relationships and the List of 20 get presence; established ones get the contact floor.
- **Greene vs Lencioni on dependence.** Law 11 says keep people dependent on you; Lencioni says give away the business and Baker says earn the premium through expertise. *Call:* Law 11 is excluded. Be hard to replace because of mastery, never because you withheld something.

## A11. The Standing layer (Robert Greene, filtered)

### A11.0 The kind truth about this layer
Greene's books describe how power was won in courts, wars and affairs. Most of his case studies are one-shot or adversarial games. Yours is the opposite: about 150 people, many of whom know each other, dealt with repeatedly for decades, in a city where word travels. In a repeated game, a manipulation discovered once is repriced across the whole network. Greene says as much himself in Law 5: reputation is the cornerstone, guard it with your life. So Greene's strongest law is the reason most of his tactics are excluded here.

Also plain: the Charmer chapter calls the other person a "target" and a "victim", recommends fake gestures, and aims at dependence. The *behaviours* it lists are sound and compatible with Port and Lencioni. The *intent* is not, and intent leaks. The system adopts the behaviours with the intent replaced: the point is their benefit, and yours follows.

**Who to read, in order of usefulness for this system:** Laws of Human Nature (understanding people, and yourself) → Mastery (respect that comes from skill, social intelligence, mentors) → 48 Laws (standing, and a defensive radar) → Art of Seduction (the Charmer's six behaviours and the Anti-Seducer list only).

### A11.1 Adopted: rule, source, where the system enforces it
| Rule | Source | Enforced in |
|---|---|---|
| Make them the centre; listen; learn what they value | Charmer; LoHN ch. 2 (empathy over self-absorption) | A9.1, after-call prompt, *Before you talk* card |
| Be a source of ease, not of problems | Charmer | A9.15 |
| Calm under adversity; never show temper | Charmer; LoHN ch. 1 | Hold (A9.19); law 8 |
| Be useful, and follow through (Greene: the promise you do not keep makes an enemy) | Charmer | Promises queue, intro matcher |
| Bring antagonism into harmony; never stir what you cannot settle | Charmer | weekly review, item 8 |
| Confirm their self-opinion: autonomous, intelligent, good | LoHN ch. 7 | A9.18 |
| Judge character by patterns over time, never by first impression or by words | LoHN ch. 4; Mastery (reading people) | reliability read (below) |
| Know who you are dealing with before you act | 48 Laws, Law 19 | `## Interpretations` prefix `character:`; *Before you talk* |
| Avoid the chronically unhappy and those who drain you | 48 Laws, Law 10 (infection) | owner-only `energy`; tier review |
| Say less than necessary | Law 4 | A9.13 |
| Win through actions, not argument | Law 9 | A9.14; "show, then ask" in proposals |
| Reputation is the cornerstone | Law 5 | `known_for` consistency; `reputation_signal` |
| Do not outshine those above you; do not look too perfect | Laws 1 and 46; Mastery (envy) | A9.16 |
| Appeal to self-interest when asking | Law 13 | A9.17 |
| Value your time; what is free is suspect | Law 40; Enns (no free pitching) | gives are knowledge, introductions and attention, **never unpaid deliverables** |
| Do not isolate | Law 18 | inner-tier cadence; one in-person touch a week (review) |
| Do not go past the mark; stop when you have what you came for | Law 47 | stalled-thread ladder stops at rung 4; one payload per message |
| Carry yourself as you wish to be treated | Law 34 | no softeners, dates on asks, fees stated once |
| Speak through your work | Mastery, social intelligence strategy 1 | `give_know` touches that show thinking; case outcomes reported to referrers |
| See yourself as others see you | Mastery, strategy 3 | `reputation_signal` review, monthly |
| Suffer fools gladly | Mastery, strategy 4 | review prompt: "what did I take personally that was only their pattern?" |
| Mentors: make their investment pay | Mastery (the mentor dynamic) | `relationship: mentor` touches lead with what you did with their last advice |

**The reliability read.** Closing a `commitment_theirs` item asks one thing: kept, late or dropped. The ledger shows `their promises: 5 kept · 1 late · 2 dropped` on the person page. This is Greene's "patterns over time" done with facts, so it needs no interpretation label. It informs tier, `fit`, and how much weight you put on their next promise. It never appears in a draft.

**The seven deadly realities as a checklist for yourself first** (Mastery): envy, conformism, rigidity, self-obsessiveness, laziness, flightiness, passive aggression. In others they are noted only as `character:` interpretations, my read, never public. Greene's practical advice for each is mostly the same: do not trigger it, do not take it personally, do not try to reform it.

### A11.2 Adapted: the contact floor and the quiet rule
Law 16 taken whole would break a keep-in-touch system, and its Reversal says so. Adapted:

| Tier | Minimum days between **unprompted** touches |
|---|---|
| `inner` | none |
| `core` | 10 |
| `active` | 30 |
| `wide` | event-driven only |

- Exempt from the floor: replies, `keep_promise`, `thank`, `celebrate`, and anything they asked for.
- New relationships (first 90 days) and `list_of_20` targets are exempt: be present first.
- **Quiet rule:** two consecutive outbound touches with no reply sets `quiet_until` = today + 2× effective cadence. Until then the engine surfaces nothing unprompted for that person except a promise due or a milestone. No third message, no "did you see my note". Then one fresh touch with a strong payload; if that also goes unanswered the weekly review proposes a tier demotion. This is respect running in both directions. *(Day counts are extrapolation.)*

### A11.3 Excluded, and why
Conceal your intentions (3) · get others to do the work and take the credit (7) · keep people dependent (11) · selective honesty to disarm (12) · pose as a friend, work as a spy (14) · crush your enemy (15) · suspended terror and unpredictability (17) · do not commit to anyone (20) · seem dumber than your mark (21) · cultlike following (27) · control the options (31) · play to fantasies (32) · find each person's thumbscrew (33) · stir up waters (39) · strike the shepherd (42) · the mirror effect as mockery (44). From Seduction: every process tactic (triangles, mixing pleasure with pain, isolating, regression, the Coquette's hot and cold), and tailored flattery. From Law 2, the friend-distrust half.

Reasons, in order: they break doctrine laws 5 and 7 (kind truth, facts as facts); they fail the repeated-game test in A11.0; several conflict directly with Lencioni and Enns, whose methods already produce the respect you want with none of the downside.

### A11.4 Defensive radar
The excluded laws are still worth knowing, because people will use them on you. Patterns worth an `interpretation` line prefixed `caution:` when you see them repeatedly: praise that arrives too early and too thick (Mastery on envy) · generosity followed quickly by a large ask (Law 12) · questions about your pricing, clients or plans with nothing offered back (Law 14) · manufactured urgency and vanishing options (Law 31) · promises that are warm, frequent and never dated (the reliability read will show it). Response is always the same: slow down, write it down, ask a calibrated question, decide tomorrow.

### A11.5 Standing scripts (before → after)
*(The full working reference lives in GREENE-HELPER.md: the Four Reads and 18 situations. These five stay here as the doctrine's own examples.)*
Price pushback. *Before:* four sentences on why the fee is fair. *After:*
> The fee for that scope is 4.5 lakh. If 3 is fixed on your side, I can show you what 3 covers. Which is more useful to see?

A message that stung. *Before:* a same-hour defence. *After (held overnight):*
> Understood. I'll look at the numbers tonight and call you Thursday at 11.

Sharing a win with a peer (envy-safe).
> The snack range finally reached shelf this week. Eight months and three rejected routes. Your point about the closure back in March is what unlocked it.

Kind truth with self-opinion intact.
> You know this category far better than I do, so correct me if I've read it wrong. On the new pack, the claim you care most about is the smallest thing on the front. I can show you two ways to fix that on Thursday. Your call whether it's worth changing.

To a mentor.
> In June you told me to stop quoting by deliverable. I moved three proposals to a single fee. Two closed, both higher than I would have quoted. Thank you. There is one thing I'm stuck on and I'd value ten minutes. Tuesday morning suits me if it suits you.

### A11.6 The reply-time model (GREENE-HELPER.md)
The Standing layer above tells the system what to allow. The helper tells *you* what to do in the thirty seconds before you reply. It is a separate doc because it is read at a different moment, by a person rather than an engine.

**Four Reads:** R1 read yourself (would this feeling author a message I'd stand by? if not, Hold) · R2 read them (which of the three self-opinions must stay intact, and what are they proud of?) · R3 read the field (the record and the pattern, not this message) · R4 read the move (name the outcome in one line, then write the smallest message that causes it).

Then an 18-row situation lookup, each row giving the real dynamic, the trap, the move and a line to adapt: price pushback · silence after a proposal · scope creep · criticism of your work · their win · your win · free-work requests · anger · your own lateness · competitor mentions · asking for a referral · writing to a mentor · stolen credit · saying no · bad news · early heavy praise · negotiating from the weaker side · a problem mentioned in passing.

`_System/greene-helper.md` loads into the Relationship OS Claude Project alongside `my-voice.md`, `draft-rules.md` and `positioning.md`. Drafting order is fixed: four reads → situation row → draft → linter (A9) → Anti-Seducer check. Log lines from helper-assisted replies carry the situation code (`greene:3.4`) so the quarterly review can show which situations recur.

---

# PART B: THE LAYERS

### 1. Capture (shared membrane with Life OS)
- Relationship context is captured the same audio-first way as everything else: record a thought after a call or meeting → Whisper transcribes → tagged `#person` (or classified) → routed to the right `07-People` note as an appended interaction entry. One recording = one entry, kept whole.
- **New: the after-call prompt.** When a capture is tagged `#person`, the confirmation card asks four things, each optional, each one tap to skip: *What is going on for them right now? What did they say they want? What did either of you promise, by when? Who should they meet?* Answers become `problem`, `goal`, `commitment_*` and `intro` proposals. (Keenan: the quality of what you know about their current state decides how useful you can be.)
- Dex mobile app also captures interactions directly. Both paths land in the same record.
- Triggers: Siri / Back Tap / Action Button / AirPods (no Apple Watch). "✅ Captured" instantly; routed entry appears after the watcher runs.

### 2. Engagement engine: Dex (paid, $20/mo)
- **Dex Professional**; connect Gmail, LinkedIn, WhatsApp, Calendar auto-sync.
- **CardDAV two-way sync** to iPhone + Mac contacts (enriched summaries propagate to your phone). The enriched summary now leads with recall aids: tier · last promise · next Ask about.
- Dex holds reminders and cadence; Obsidian holds the deep record.

### 3. System of record: Obsidian `07-People`
One note per person, under the **universal frontmatter** (Phase 0.4) plus relationship fields. The full contract lives in the two schema files and is identical in both.
```yaml
id: 20260605161200         # immutable
type: person
created: 2026-06-05
origin: human|ai           # provenance on every entry
source: dex|voice|manual|handshake|gmail
status: active|cold|dormant
name:
relationship: []           # family · friend · client · past_client · prospect · referrer · peer · collaborator · mentor · vendor
company:
channels: {whatsapp:, email:, linkedin:}
preferred_channel:
language:                  # en | hinglish | hi | mr
tier:                      # inner | core | active | wide
cadence_days:              # overrides tier default
last_contact:
last_give:
last_ask:
quiet_until:               # set by ledger after two unanswered outbound touches (A11.2)
energy:                    # owner-only: gives | neutral | drains (Law 10)
known_for:                 # what THIS person should associate me with
recall_trigger:            # the situation in their life that should make them think of me
dates: {birthday:, anniversary:}
referred_by:               # [[person-id]]
list_of_20: false
warmth_stage:              # identified → researched → engaging → conversing → warm → ready
# commercial module (blank for family/friend)
conversation_stage:        # none | probative | qualifying | value | closing | delivering | past
buyer_role:                # economic | influencer | user | gatekeeper | unknown
fit:                       # ideal | good | poor | unknown
dex_id:
dex_deeplink:
handshake_id:
outreach_id:
tags: []
categories: [[ ]]
subjects: [[ ]]
```
Body sections, in order: **Context** · **Current state** · **Future state** · **Needs** · **Can help with** · **How they communicate** · **Facts** · **Interpretations** · **Interaction log** · **Next action** · **Updates**.

`_System` holds `my-voice.md` (style reference), `draft-rules.md` (A9), `positioning.md` (your default `known_for`, per audience), `greene-helper.md` (the reply-time model, A11.6) and `reputation.md` (approved `reputation_signal` lines).

### 3a. What the record remembers (proposal types → sections)
Handshake reads WhatsApp and the cockpit pipeline reads captures. Both *propose*; a human approves every card (Rule 2: append-only, provenance `origin: ai · approved by …`).

| Proposal | Means | Lands in |
|---|---|---|
| fact / interpretation | stated vs. my reading; never in one list | Facts / Interpretations |
| commitment_mine / commitment_theirs | a promise, either side; theirs may add a check-in the day after | Interaction log · Next action |
| follow_up | something I must do | Next action |
| personal_detail (topic) | their people, health, home, interests, preferences, favours | Context |
| upcoming (date) | an event in *their* life → "ask how it went" the day after | Next action |
| milestone | new job, baby, move, award → congratulate today | Next action |
| need | what they're looking for → where I can help or intro | Needs |
| company_knowledge | durable org facts | company note `## Facts` |
| person_update | a durable attribute | Context |
| **problem** | a problem they named, with its impact if they stated one | Current state |
| **goal** | where they are trying to get to, in their words | Future state |
| **offer** | what they are good at, sell, or offered to help with | Can help with |
| **intro** | an introduction made or promised | Interaction log · Next action (check in 14 days after) |
| **give_mine** | something I gave: intro, resource, help, referral | Interaction log (sets `last_give`) |
| **give_theirs** | something they gave me | Interaction log · Next action (thank today; report outcome in 30 days) |
| **important_date** | birthday, anniversary, launch date | `dates` via Fill · Next action yearly |
| **reputation_signal** | something they said about me, or repeated from others: what I am actually known for | `_System/reputation.md` · Interaction log |

### 4. Proactive engine (the keystone)
- **`sync.py` (nightly):** Dex REST API → upsert `07-People` notes → pull `dex_id` → generate `dex_deeplink`s → write an enriched summary back to the Dex Description → CardDAV propagates to phone.
- **`ledger.py` (nightly, new):** parse interaction logs → compute gives/asks/received over 90 and 180 days → advance `last_give`/`last_ask` → advance `status` → compute each person's reliability read (`promise_kept · promise_late · promise_dropped`) → set `quiet_until` after two unanswered outbound touches → write `ledger.json`.
- **`match.py` (nightly, new): the intro matcher.** For every open `## Needs` and `## Future state` line, search other people's `## Can help with`, company `## Facts`, and Resource OS. Each plausible match becomes a *Could help* card: "Meera needs X · Sanjay offers X · why". AI proposes; you decide. Never contacts anyone.
- **`morning_engine.py` (daily):** builds the day's list in this fixed priority, then cuts to **5**:
  1. I owe a reply (oldest first; anything over 48 h is flagged)
  2. Promises I made, due or overdue
  3. Ask about, due today
  4. Celebrate (milestones, `dates`)
  5. Thank / report outcome
  6. Could help (top match)
  7. Going cold **with a payload** (inner first, then core, then active)
  8. Warm-up action due
  Before ranking, the engine drops anyone inside their **contact floor** or with `quiet_until` in the future, unless the item is a reply owed, a promise due, a thank or a milestone (A11.2).
  Overflow is counted in one muted line ("9 more, none urgent"), never listed as debt. → **ntfy** push → `today.json`.
- **One-tap action:** Claude drafts in your voice under A9 → linter → opens `wa.me` / Gmail compose / LinkedIn → **you send manually**. Logging the send appends `- {date} · out · {channel} · {touch_type} · {summary}` to the interaction log and resets `last_contact`.

`today.json` item shape:
```json
{"person_id":"…","name":"…","tier":"core","queue":"ask_about","touch_type":"remember",
 "payload":"Aarav's board results (fact 2026-09-02)","source_line":"bc:…",
 "due":"2026-09-17","channel":"whatsapp","ledger":{"gives90":3,"asks90":0,"received90":1}}
```

### 5. Warm-up engine (nurture new high-value relationships)
- `target` person-notes (`list_of_20: true`, max 20) + **PDL** firmographic enrichment (free tier + credit alerts) + a dossier (Claude web research + your paste of their LinkedIn About/posts; no automated scraping).
- **Warmth stages and what earns the next one** (Port: one link in the chain at a time; aim for the callback, not the part):

| Stage | You have | Next move (touch type) |
|---|---|---|
| `identified` | a name and why they matter | research |
| `researched` | dossier: their current state, goals, what they publish | a thoughtful public comment or a `give_know` with no ask |
| `engaging` | they have seen your name 2+ times | a direct message carrying one specific, useful thing |
| `conversing` | a two-way exchange | `give_who` or `give_know` tied to something they said |
| `warm` | they have replied to you 3+ times or met you | `invite`, or a probative conversation |
| `ready` | mutual regard | normal tier cadence; ask gate applies |

- **Fit check first:** a target marked `fit: poor` leaves the engine. Do not warm up people you would not want as clients or collaborators (Port).
- **50/50 rule** (Port): keep roughly half the List of 20 as potential clients and half as other professionals who serve the same clients.
- **Compliance:** no automated LinkedIn scraping, no auto-commenting/DMing, no auto-send anywhere.

### 6. Interface & action
- **MCP servers (4):** Dex, Obsidian (Local REST API), Gmail, Google Calendar, wired to a "Relationship OS" Claude Project (rules, your voice, schema, channel formats, deeplink patterns, **this doctrine**, and **GREENE-HELPER.md**). Slash command `/reply {person} {their message}` returns the four reads filled from the record, the matching situation row, the draft and the lints that fired.
- ntfy morning push; Obsidian Bases views ("today", "promises", "could help", "going cold", "warm-up queue", "List of 20", per-person); React dashboard later. UI rules: relationship-os-patterns.md.

### 7. Feeds the unified graph (capstone)
People notes carry typed edges into the rest of the system: a person `informed-by` a decision (`09-Decisions`), `part-of` a project (`05-Projects`), `derived-from` a resource they recommended (Resource OS). **New edges:** `introduced-by` and `referred-by` between people, each with the required one-line why. Fixed IDs make these durable. The graph can now answer "who sent me my best clients" and "who have I connected, and what came of it".

---

## REVIEWS AND METRICS (ADHD-safe)
RESEARCH.md §1: fragile streaks punish inconsistency; use cumulative and windowed measures. No streak counter appears anywhere in this system.

**Weekly review (Sunday, 20 minutes, writes to `08-Reflections`):**
1. *Three fears audit* (Lencioni). Where this week did I hold back a truth to protect the business? Avoid a question so I would not look uninformed? Decline unglamorous work to protect status? One line each.
2. *Softener audit.* Any message sent with an open-ended next step? Convert it to a date now.
3. *Ledger flags.* Review "I only take" and "one-way street".
4. *Tier moves.* Anyone to promote or demote? Any cap breached?
5. *List of 20.* One stage advanced this week?
6. *Could help.* Approve or reject the week's unmade matches.
7. *Anti-Seducer audit* (GREENE-HELPER.md §4). This week, was I at any point: a windbag, a moraliser, a tightwad (with money, credit or praise), a reactor, a bumbler, or pushy? One line, one name, one repair if needed.
8. *Self-possession.* What did I take personally that was only their pattern? Any held replies still unsent? Any antagonism I stirred that I should settle?
9. *In person.* Did I see at least one person face to face this week? (Law 18.)

**Monthly (10 minutes): see yourself as others see you.** Read `_System/reputation.md`. Do the words people use match `known_for`? If they say "logo guy" and you want "packaging strategist", that gap is the most important finding in the whole system, and it is fixed by what you show and say, not by what you wish.

**Windowed metrics (rolling, never streaks):**
| Metric | Window | Healthy |
|---|---|---|
| Touches carrying a payload | last 7 days | ≥ 80% of touches |
| Promises kept on or before date | last 30 days | ≥ 90% |
| Replies owed older than 48 h | now | 0 to 2 |
| Gives : asks | last 90 days | ≥ 5 : 1 |
| Intros made | last 30 days | ≥ 2 |
| `give_theirs` thanked within 24 h | last 90 days | 100% |
| Inner tier within cadence | now | ≥ 80% |
| Outbound messages over the length budget | last 30 days | ≤ 10% |
| Third-in-a-row unanswered messages sent | last 90 days | 0 |
| Replies sent the same hour while marked irritated | last 90 days | 0 |
| Referred revenue by source (`referred_by`) | last 12 months | readout only, never a ranking input |
*(Thresholds are starting points and extrapolation. Tune after 8 weeks of data.)*

## GUARDRAILS
- **Their interest is the point.** These tools help you be useful and honest. They are never used to steer someone against their own interest. (Lencioni, Enns and Baker all insist on this.)
- **Sensitive data minimalism.** Record health and family difficulty only when it changes how you should show up for them. Never on public surfaces, never in a draft without an explicit tap (A9.5).
- **Other people's messages are other people's data.** Handshake reads your own WhatsApp exports locally; nothing leaves the owned stack except the text sent to the model for extraction. Keep it that way. *(Privacy posture is extrapolation; check your obligations under India's DPDP Act before storing third-party personal data at scale.)*
- **No psychological profiling beyond "my read".** Communication-style notes are interpretations, labelled as such.
- **The books are a lens, not scripture.** Where a rule here makes you sound unlike yourself, `my-voice.md` wins on tone and this doc wins on substance (payload, dates, truth).

## SYNC & INFRASTRUCTURE (aligned, no iCloud)
- Vault + capture folder sync via **OneDrive** (simplest now) or **Syncthing + Möbius** (owned, no quota, native on the Linux server). Git the vault.
- Engines run on the **ZimaOS i7 server** (Docker) once Phase 5 lands; on the Mac while building. ntfy self-hosted.

## TOOLSTACK
| Layer | Tool | Cost |
|---|---|---|
| Engagement | Dex Professional | $20/mo |
| Record | Obsidian + Local REST API + Bases | Free |
| Orchestration | Claude via 4 MCPs (Dex/Obsidian/Gmail/Calendar) | API usage |
| Engines | Python (sync.py, ledger.py, match.py, morning_engine.py) | Free |
| Enrichment | PDL (free tier) | Free |
| Push | ntfy (self-hosted) | Free |
| Sync | OneDrive or Syncthing+Möbius + Git | Free/owned |

## BUILD SEQUENCE (EXECUTION-PLAN Phase 4; parallel-capable with P2/P3)
4.1 Foundation (Dex Pro + connections + CardDAV; `07-People` template under the v2.0 schema; 4 MCPs + Claude Project loaded with Part A; `positioning.md`, `my-voice.md`, `draft-rules.md`) → 4.2 Proactive engine (`sync.py` + `ledger.py` + `morning_engine.py` with the Payload Rule and the cap of 5 + one-tap drafts + linter) → 4.3 Intro matcher (`match.py` + *Could help* queue) → 4.4 Warm-up engine (List of 20 + PDL + dossiers + warmth stages) → 4.5 Greene panel in the composer + `/reply` command → 4.6 Weekly review + windowed metrics.

**Build-order warning (RESEARCH.md: building the system delivers the dopamine, using it does not).** Ship 4.1 and 4.2 with **ten real people** tiered and one week of real touches sent before writing a line of 4.3. The doctrine is worthless until messages go out.

**Done when:** Dex auto-syncs to owned notes nightly, your phone shows enriched contacts, and each morning you get at most five touches, each with a payload and a one-tap draft in your voice that passes the linter, and nothing sends without you.

## THE RULES
1. Nothing auto-sends, ever.
2. One-way, append-only into the record; never silently overwrite.
3. No automated scraping / auto-DM (platform compliance).
4. Drop-Dex test: the markdown archive must stand alone.
5. Drafts always in your voice (`my-voice.md`) and always through the linter (A9).
6. **No empty touches** (Payload Rule, A5).
7. **No proposed ask without gives** (ledger gate, A6), and none at all to family or friends (A3).
8. **A guess never reads as a fact**, in the record or in a message.
9. **No streaks.** Windowed metrics only.
10. **Never chase.** Contact floor and quiet rule hold unless you owe them something (A11.2).
11. **No Greene tactic from the excluded pile (A11.3) is ever drafted**, whoever the recipient is.
12. **Gives are never free deliverables.** Knowledge, introductions and attention are given; work is sold.

## SOURCE LEDGER
**Grounded in the library:** giver's stance, share who/what/how you feel, 50/50 rule, List of 20, Red Velvet Rope, keep-in-touch as the trust builder, permission for broadcast, one link in the chain, track lead sources (Port) · naked service principles and the three fears, applied beyond clients (Lencioni) · Four Conversations, the sale is the sample, expert vs vendor, willingness to walk (Enns) · five elements of current state, future state, the gap (Keenan) · economic buyer, conceptual agreement before proposal (Weiss) · labels, mirrors, calibrated questions, "that's right", no-oriented questions, three negotiator styles (Voss) · problem and trigger lead the brain's sequence (D'Souza) · positioning as being less exchangeable (Baker).
**Grounded in Greene (v2.1):** Charmer's behaviours and the Anti-Seducer types (Art of Seduction) · self-opinion and its three universals, increasing reaction time, character as pattern (Laws of Human Nature) · seven deadly realities, the four social-intelligence strategies, envy handling, the mentor dynamic (Mastery) · Laws 1, 4, 5, 9, 10, 13, 16 with its Reversal, 18, 19, 34, 40, 46, 47 (48 Laws).
**Extrapolated in v2.1:** the contact-floor day counts, the quiet rule and its 2× window, the length budgets, the Hold mechanic, the reliability read, the repeated-game argument for the excluded pile (my reasoning, though it leans on Law 5).
**Extrapolated beyond the library (treat as hypotheses, tune with data):** tier sizes and cadence days · daily cap of 5 · the 3-gives and 90-day ask gate · 30-day outcome report · double opt-in intros · the family/friend gate · all metric thresholds · privacy posture · everything about applying these books to personal relationships, which none of them address except Lencioni in one closing passage.
