# T06 implementation review — codex output + orchestrator dispositions

Reviewer: `codex exec` (read-only) over the implemented files (`src/planner/sprints/logic/*`, `src/planner/sprints/data.py`, `tests/unit/test_sprints.py`) against ticket.md, plan.md (incl. amendments A1–A5), SPEC §3.1/§3.2/§4.4/§4.5/§5/§6.1/§18.3(10,20), and the contracts. Run 2026-07-04.

## Codex findings

1. **VIOLATION** — `logic/transitions.py:41` (`classify_human_transition`) constrained only the *target* status, so `done → todo/active/blocked` and `deferred_next_sprint → todo/active/blocked` passed through `data.py transition_item_status`, exceeding the plan's "human movement within {todo, active, blocked}".
2. **VIOLATION** — `data.py propose_item_status` allowed filing a new status proposal regardless of the item's current status; after an accept lands `done`/`deferred_next_sprint`, agents could file again, contradicting SPEC §4.4.7 "terminal for agent involvement" (SPEC.md:100).
3. **VIOLATION** — `test_sprints.py:181` used a raw `UPDATE tickets ...` outside the sanctioned `_insert_ticket` fixture.
4. **VIOLATION** — supersede test asserted only `replaced_body.to_status` (not the full payload); kickoff-freeze test only counted `kickoff_frozen` events without asserting payload shape — weaker than §18.3's exactness bar.
5. **RISK** — freeze latch checked before `BEGIN IMMEDIATE`; two concurrent freeze calls could both observe `NULL` and both write + emit events.

Clean per codex: agent transition-set exactness via `AGENT_ITEM_TRANSITIONS` with proposal-only targets rejected; `accept_item_status` carries no grant arguments; `assign_item_sprint` is a plain `sprint_id` update with `item_updated` and no copy; overlap checked against all sprints inside the write transaction, inclusive, with `conflict_id`; `blockers_cleared` derived on read only; logic purity holds (SQL/events/clock confined to data.py); one timestamp per write and one `item_status_changed` per transition; all tests named `test_a10_*`/`test_a20_*`, no skip/xfail/empty, A1–A5 present.

## Orchestrator dispositions (all applied by the orchestrator as small direct fixes; gates re-run green)

1. **Accepted, fixed with a semantic ruling.** SPEC is silent on exits from the proposal-only statuses, so the fix distinguishes them: `done` is terminal at this layer — entered only via accepted proposal (§4.4.7), no exit edge for anyone; `deferred_next_sprint` remains human-resumable (`→ todo/active/blocked`), because deferral is by construction temporary — without a human exit the status is a dead end and next-sprint resumption becomes impossible. Agents remain confined to exactly `AGENT_ITEM_TRANSITIONS` on both sides. Fix: `classify_human_transition` now returns `forbidden` when `from_status is ItemStatus.done` (src/planner/sprints/logic/transitions.py:43-46). Any future "reopen a done item" needs an explicit spec'd action, not an accident of this writer.
2. **Accepted, fixed.** `propose_item_status` now rejects when the item's current status is in `PROPOSAL_ONLY_STATUSES` with `ErrorCode.validation` ("item status is terminal; no further proposals", detail `{status}`) — src/planner/sprints/data.py:447-453.
3. **Accepted in form.** The raw `UPDATE` was the same fixture class as the sanctioned `INSERT` (blocker ticket-state setup, not an exercise of T04's writers), so no behavioral issue — but the sanction is now explicit: a `_set_ticket_state` helper with the same justification comment replaces the inline SQL (tests/unit/test_sprints.py).
4. **Accepted, fixed.** The supersede test now asserts the entire `proposal_superseded` payload (`{field, replaced_body:{to_status, note, proposed_by, created_at}}`); the kickoff-freeze test asserts the `kickoff_frozen` payload equals `[{"frozen_at": <the sprint's kickoff_frozen_at>}]`.
5. **Accepted, fixed.** The freeze latch is now atomic: the `UPDATE` inside `BEGIN IMMEDIATE` carries `AND kickoff_frozen_at IS NULL` (resp. review), and the event is emitted only when `rowcount == 1`. The early-return fast path stays for the common already-frozen case.

## Post-fix gate run (fresh, orchestrator shell)

- `.venv/bin/pytest tests/unit/test_sprints.py -q` → `12 passed`
- `.venv/bin/ruff check src/planner/sprints/ tests/unit/test_sprints.py` → clean; `.venv/bin/ruff check .` → clean
- `.venv/bin/mypy src/` → `Success: no issues found in 49 source files`
