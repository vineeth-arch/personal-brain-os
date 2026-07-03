# Logged for after the 30-day test

- Multi-topic splitter: auto-split a genuinely multi-topic journal/musing recording into multiple notes (needs an LLM topic-segmenter; route.py has the SPLITTABLE seam).
- Theme toggle (light mode tokens are shipped and working; dark is hardcoded as default this pass).
- Custom PWA install prompt/button (browser default install affordance is used for now).
- Settings: edit server config (paths, ntfy, threshold) from the cockpit via a config-write API endpoint.
- Capture confirmation echoes the first line of what was heard (transcript echo) — research B1.
- Triage cards show the AI's evidence for its guess alongside confidence — research B2.
- Track + display AI classification accuracy over time ("94% of last 50 approved unchanged") — research B3.
- Bound the triage queue view (~5 per visit) + optional 5-minute timeboxed triage with a visual pie timer — research B4.
- Anti-guilt drain: triage items older than N days auto-approve at best guess (origin: ai, git-revertible) — research B5.
- Readwise-style hybrid resurfacing: 1–3/day, stochastic + spaced, connect/act/archive responses — research B6.
- Show a related past note at classification time ("past-you thought this too") — research B7.
- Streak card → cumulative + windowed framing ("217 captures · 5 of last 7 days"), never broken-chain messaging — research B8.
- Daily ntfy digest: status + resurfaced note + queue count (the system visits you) — research B9.
- Optional Goblin-style micro-step breakdown for extracted todos with a "how hard does it feel?" dial — research B10.
- Surface the trust boundary in the UI ("nothing entered your vault without you — N notes gated this month") — research B11.
