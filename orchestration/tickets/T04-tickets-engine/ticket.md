# T04 — Ticket state machine, resolution engine, recap, sprint-assignment rules (stage 3)

## Scope

The tickets domain's pure logic and data layer: the state machine, the resolution engine (SPEC §4.4 — the single door to canonical values), proposal lifecycle, onward grant, recap rules, title limit, and ticket sprint-assignment rules. Plus the unit tests for acceptance items 2, 3, 4, 5, 6, 7, 8, 13, 36 (exact assertions per SPEC §18.3, test names `test_a02_*` … anchored).

Contracts implemented against (never modified): `src/planner/tickets/contracts.py`, `src/planner/core/contracts.py`, `src/planner/core/errors.py`, plus `core/db.py`, `core/events.py`, `core/ids.py`, `core/clock.py` as given infrastructure.

## Files owned

- `src/planner/tickets/logic/` — pure functions, stdlib + contracts imports only: gating/advance tables application, proposal admission rules (ceiling/at-cap/§7.6 actor rules), resolution decisions (what an accept does: value, state, events, grant effects), recap admission, title validation, sprint-assignment admission (standalone vs parented per §3.3).
- `src/planner/tickets/data.py` — the canonical writers over SQLite: create ticket, file proposal, auto-accept path, human accept/edit-accept (with mandatory grant pair), needs_review approve, drop, set note, write recap, set priority/deadline/day/sprint assignment, grant change, human state jump (§4.3: human may move a ticket to any state; skipped gating fields keep value null). Exactly one writer per transition edge; every transition writes exactly one `state_changed` event `{from, to, cause}`.
- `tests/unit/test_tickets_engine.py` — items 2–8, 13, 36.

## Behavior (binding, from SPEC)

- §4.2 gating map and advance targets; §4.4 semantics 1–7 exactly, including: one pending proposal per field with supersede logging the replaced body; auto-accept iff proposal is on the current state's gating field AND advance-target ≤ ceiling; `resolved_by: "auto"`; pending otherwise; human accept/edit-accept (edited text stored exactly, `edited: true` in event); no reject action; result special case (ceiling `done` → straight to `done`, else `needs_review`); needs_review approve is a dedicated action requiring no grant; the onward grant pair (`next_ceiling` ∈ states-at-or-beyond-new-state ∪ {`none`}, plus `at_cap`) mandatory on every gating-field accept — absence rejected with the structured error, nothing changed.
- §4.3: at ceiling + `stop` → agent proposals rejected with structured error (including current gating field); at ceiling + `propose` → gating-field proposal parks pending; below ceiling both behave identically. Agent auto-accepts never change ceiling or at_cap. Defaults per R2 at creation.
- Recap (§3.3): writable only past `needs_success`; earlier writes rejected with structured error; overwrite allowed; event logged; no state effect.
- Title ≤ 200 chars on every write path.
- Sprint assignment (§3.3): `sprint_id` writable only when `sprint_item_id` IS NULL; parented ticket's sprint derived from parent item; write rejected otherwise (item 13).
- Proposer identity: claim-carrying context vs `PLAN_ACTOR` default `agent` — represent as an explicit `actor` argument to writers; claim validation itself is T05/stage-4 territory, but the writer signatures accept the actor and the proposal records `proposed_by`.

## Test fences

Items 2, 3, 4, 5, 6, 7, 8, 13, 36 exactly as §18.3 states them, one named test each, assertions on exact states, events, error shapes, and values. Tests run against a temp SQLite DB via the shared conftest fixtures; no mocks for logic.

## Constraints

- No imports of FastAPI/pydantic anywhere in this ticket's files.
- No files outside the owned list. Shared conftest is read-only for you.
- ruff + mypy strict clean; all nine named tests green via `pytest tests/unit/test_tickets_engine.py`.
