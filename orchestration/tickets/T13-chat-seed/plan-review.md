# T13 plan review — codex output and dispositions

Reviewer: `codex exec`, pointed at plan.md, ticket.md, SPEC §9/§11/§12/§14, and the
contract files on disk. Verdict: VIOLATIONS FOUND (4 numbered findings).

## Codex findings (verbatim summary)

1. **Malformed day ids materialize garbage.** `_resolve`'s `day_` branch calls
   `read_day` unconditionally, so `POST /api/chat/day_bogus/send` would permanently
   create a garbage `days` row plus a `day_created` event. SPEC §3.4 defines day ids
   as `day_YYYY-MM-DD`; the materialization rule applies to valid planning-date ids,
   not any `day_`-prefixed string. Fix: validate the date before `read_day`.

2. **Concurrent first sends can double-mint.** Two overlapping first sends both see
   `stored_key is None`, both call the gateway with `None`, and both persist + emit
   `chat_session_created`, breaking §11 "at most one chat session". Codex's fix:
   serialize first-send per entity and re-check before calling the gateway.

3. **Demo `links` count contradicts the contract.** `MigrationReport.links` is
   documented in `seed/contracts.py` as "belongs_to links made by title match", and
   the importer increments it only for belongs_to inserts. The demo dataset has
   2 belongs_to + 1 blocks; the plan's `COUNT(*) FROM links` would report 3.
   Fix: count `WHERE kind = 'belongs_to'` → demo `links: 2`.

4. **Symlink escape not prevented.** `Path(source_dir).is_dir()` does not prevent a
   symlinked source child from pointing outside the given directory; codex asks for
   root-resolution checks on every importer read plus a symlink-escape test.

## Dispositions

**Finding 1 — ACCEPTED, with a different error code.** The core point stands:
nothing may materialize before the id is known to be a canonical day id. Amendment
A1 requires the `day_` suffix to round-trip through `date.fromisoformat` back to the
identical string (pinning the canonical zero-padded `YYYY-MM-DD`; `fromisoformat`
alone also accepts compact forms). On failure the service raises `not_found`, not
codex's suggested `validation`: plan decision D4 already maps "names no chattable
entity" uniformly to `not_found` for unknown prefixes, and a `day_` id that cannot
be a date is the same case. A dedicated test asserts the 404 and that no days row
was created.

**Finding 2 — ACCEPTED in substance, narrower fix.** The invariant (at most one
persisted key, at most one event) is real, but per-entity locking around the gateway
call is disproportionate machinery for a single-process local server and would be
speculative resilience (PRINCIPLES). Amendment A2 gets the same invariant with the
transaction alone: the persist becomes a guarded
`UPDATE … WHERE id = ? AND chat_session_key IS NULL`; the event is appended only
when `rowcount == 1`; on `rowcount == 0` the service re-reads the winner's key and
returns it, so the loser never emits and never desyncs the panel. The loser's extra
gateway call is accepted — it is unavoidable without cross-request locks and is
harmless with the echo fake and idempotent-session gateways. A deterministic
service-level test simulates the race with a stub gateway that persists a key
mid-send (no threads needed).

**Finding 3 — ACCEPTED.** Contract comment is law; the blocks link is not a
`MigrationReport.links` link. Amendment A3 switches `_demo_report` to
`WHERE kind = 'belongs_to'` and corrects the expected demo report to `links: 2`
(test 9 and section B3 updated). Note: the orchestrator's dispatch brief said 3 —
that was derived from raw row counts, and the contract overrides it.

**Finding 4 — REFUTED.** The ticket's own parenthetical defines the requirement:
"never resolves outside given path semantics (no reaching into `~/.hermes/planning/`
by default anywhere in code)" — i.e. no default path and no live-directory fallback,
both of which the plan satisfies (the route only ever passes the caller's literal
value; there is no expansion and no fallback). The importer (`seed/importer.py`,
frozen T07 code outside this ticket's owned files) joins only fixed literal names
under the root (`sprints/current/…`, `deferred.md`, `ideas.md`) and iterates
`daily/` children it finds there — it never constructs paths from user content. A
symlink inside a source directory is the caller's own content choice, not a server
escape: the caller of `POST /api/seed` already names any directory they can reach,
so a symlink check adds no boundary. SPEC §14's "live directory never read at
build/test time" is about defaults and tests, which remain clean. No plan change;
no importer change (not owned).

## Result

Amendments A1–A3 appended to plan.md as binding. Plan otherwise stands as written.
