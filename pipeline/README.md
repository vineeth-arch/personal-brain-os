# pipeline — Pass 1

The watcher + processing stages. Empty until Pass 1.

## Relationship OS v2.2 (Pass RO)

Pure-logic modules computed on read from the vault, no nightly job: `touchlog.py`
(the v2 Interaction log line format, `record_touch`, `close_promise`, the quiet
rule), `ledger.py` (gives/asks/received counts, reliability read, contact floor,
quiet/dormant status, `advance_statuses`), `queue.py` (the seven relationship
views, the payload rule, `build`/`strip` capped at `DAILY_CAP` a morning),
`draftlint.py` (the A9 linter and Anti-Seducer chips, stdlib only), and
`greene.py` (the Greene helper's situation lookup, presets, and the two
Four-Reads text blocks). `morning.py`'s `people_section` is built on
`queue.build`/`queue.strip` and runs the `ledger.advance_statuses` status
heartbeat before assembling the digest's People lines.
