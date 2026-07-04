# T06 report — sprints: item permissions, status proposals, freeze rules, overlap

## What was built

Three owned paths, nothing else touched:

- `src/planner/sprints/logic/` — pure package (stdlib + contracts only): `transitions.py` (verdict-returning `classify_agent_transition` / `classify_human_transition` over the contract's `AGENT_ITEM_TRANSITIONS` and `PROPOSAL_ONLY_STATUSES`), `blockers.py` (`blockers_cleared` — non-empty blockers all at ticket-state `"done"`), `freeze.py` (`frozen_group`, `field_write_admissible` over independent kickoff/review timestamps), `ranges.py` (`DateRange`, inclusive `ranges_overlap`, `find_overlap`, `current_sprint_id`), re-exported via `__init__.py`.
- `src/planner/sprints/data.py` — canonical writers, each one `BEGIN IMMEDIATE` transaction pairing the mutation with its event(s) via `append_event`, single `clock.now_unix()` per write: `create_sprint` (overlap vs ALL sprints inside the tx → `sprint_overlap` with `conflict_id`), `update_sprint_field` (whitelist + freeze admission → `frozen_write` with `{field, group}`), `freeze_kickoff`/`freeze_review` (atomic idempotent latch, event only on the NULL→set flip), `add_addendum` (always allowed, append-only), `create_item`, `update_item_field` (plain fields only — status/blocked_by/status_proposal/sprint_id barred), `transition_item_status` (verdicts → `item_transition_forbidden` / `validation`; one `item_status_changed {from,to,cause}`), `propose_item_status` (proposal-only targets; supersede logs `proposal_superseded` with full replaced body; rejected on terminal-status items), `accept_item_status` (**no grant parameters** per §4.4.7 — signature guarded by test), `assign_item_sprint` (plain event-logged `sprint_id` update, no copy); reads `read_item` (`blockers_cleared` derived on read from `tickets.state`) and `read_sprint`.
- `tests/unit/test_sprints.py` — items 10 and 20. Per the 1:1 fence (one named test per §18.3 item, enforced by the verify scorer), exactly one `test_a10_*` and one `test_a20_*` exist, each asserting its item's full statement in legs; supplementary coverage runs under unanchored `test_x06_*` names. Originally written as 6+5 anchored tests, merged post-integration-finding with zero assertions dropped (justification logged centrally in decisions.md by the lead).

## Test results (fresh run, orchestrator shell)

`.venv/bin/pytest tests/unit/test_sprints.py -q`:

```
.....                                                                    [100%]
5 passed
```

| Test | Result |
|---|---|
| test_a10_sprint_item_permissions (anchored, item 10 full statement) | PASS |
| test_a20_freeze_rules_and_overlap (anchored, item 20 full statement) | PASS |
| test_x06_item_proposal_supersedes_prior (supplementary) | PASS |
| test_x06_item_blocked_requires_blockers (supplementary) | PASS |
| test_x06_current_sprint_selection (supplementary) | PASS |

Gates: `ruff check .` clean repo-wide; `mypy src/` `Success: no issues found in 49 source files`; instrument scan clean (no skip/xfail/empty/commented tests).

## Reviews

- **Plan review** (plan-review.md): 3 violations + 3 risks. Accepted five as binding amendments A1–A5 (mypy-strict `blocked_by` normalization; `Addendum` import + `ErrorCode` member comparisons; structured-detail assertions; `deferred_next_sprint` fenced on both sides; blocked-edge event payload assertion). Refuted one (mechanical human-only enforcement inside the accept writer) as API-layer scope — see concerns below.
- **Impl review** (impl-review.md): 4 violations + 1 risk, all dispositioned and fixed by the orchestrator directly (small fixes), gates re-run green: human transitions out of `done` forbidden; proposals rejected on terminal-status items; test fixture UPDATE moved behind a sanctioned helper; supersede + freeze event payloads asserted in full; freeze latch made atomic (conditional UPDATE + rowcount-gated event). Codex explicitly cleared the three dispatch-mandated checks: agent transition-set exactness, proposal-only statuses with grant-free accept, and item→sprint assignment as a plain event-logged field update.

## Pipeline notes / deviations

- The first Opus implementer was killed mid-run by an account session limit after completing only `logic/`; I verified those five files against the plan and dispatched a second Opus implementer for `data.py` + tests. No content divergence resulted.
- Implementer deviations from plan, both accepted: a `value is None` guard before `Priority(value)`/`Project(value)` in `update_item_field` (mypy strict; behavior unchanged), and dropping an unused `Priority` test import (ruff F401).
- Post-review fixes were applied by the orchestrator directly rather than a fix agent (all small, playbook-sanctioned).

## Rulings made (delegated judgment, logged here)

- **Exits from proposal-only statuses** (SPEC silent): `done` is terminal for everyone at this layer — entered only via accepted proposal, no exit edge; a future "reopen" must be an explicit spec'd action. `deferred_next_sprint` is human-resumable (`→ todo/active/blocked` via `transition_item_status(by_agent=False)`), because deferral is temporary by construction and needs an exit for next-sprint resumption. Agents remain confined to exactly `AGENT_ITEM_TRANSITIONS` on both endpoints.
- **Proposals on terminal items** rejected with `validation` (§4.4.7 "terminal for agent involvement").
- **Freeze re-latch** is idempotent (original timestamp preserved, no second event) rather than an error.

## Concerns for the integrator

1. **Human-vs-agent gating of `accept_item_status` and `transition_item_status(by_agent=False)` is not enforced at the data layer** — caller identity doesn't exist there. Stage-4 API wiring must route these behind the human surface and reject claim-carrying/agent requests with `ErrorCode.agent_forbidden` (§7.6). Same for `freeze_kickoff`/`freeze_review`/`update_sprint_field`.
2. `blockers_cleared` compares `tickets.state` against the literal string `"done"` (deliberate: no cross-domain import of T04's in-flight contract). If T04's state enum ever renames `done`, this literal must follow — a grep for `_TICKET_STATE_DONE` finds it.
3. `read_item` is the only read path that derives `blockers_cleared`; stage-4 item GET/list endpoints must go through it (or replicate the derivation), never expose the raw row as "cleared".
4. Approval queue (§4.5) must include items with pending `status_proposal` — the column round-trips through `read_item`/`_load_item`; no queue view was in this ticket's scope.

No contract-change requests. No files outside the owned set were modified.
