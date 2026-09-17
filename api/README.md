# api — Pass 2

The FastAPI app. Empty until Pass 2.

## Relationship OS v2.2 (Pass RO)

`api/people.py` exposes the v2.2 pipeline read-on-write style: `GET /api/people`,
`GET|POST /api/people/{person_id}` for the person page, `GET /api/people/today`
(the Today strip, `queue.build`/`strip`), `GET /api/people/greene` and
`POST /api/people/lint` for the composer's Greene panel and draftlint reads,
`POST /api/people/{person_id}/draft` and `.../reply` for the doctrine-aware
draft (Four Reads + Greene situation), `.../contact` and `.../promise` for
typed touches and promise close, `.../owner` for owner-only field edits, and
`.../hold` (backed by the new `api/held.py`) for holding a draft instead of
sending it — nothing here ever sends.
