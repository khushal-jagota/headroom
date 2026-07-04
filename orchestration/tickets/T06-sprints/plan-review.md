# T06 plan review — codex output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive), pointed at plan.md, ticket.md, SPEC §3.1/§3.2/§3.6/§4.4/§4.5/§5/§6.1/§18.3(10,20), and the seven contract/infra files. Run from repo root, 2026-07-04.

## Codex findings (verbatim substance)

1. **VIOLATION** — plan.md:368/372 vs plan.md:95: `transition_item_status(..., blocked_by=None)` passes `blocked_by` (typed `Sequence[str] | None`) into `classify_agent_transition(blocked_by: Sequence[str])` without normalizing; mypy strict (pyproject `[tool.mypy] strict=true`) rejects the `None` arm. Fix: normalize with `list(blocked_by or [])`.
2. **VIOLATION** — plan.md:487 test-import list omits `Addendum`, yet plan.md:584 asserts `Addendum(...)` → ruff F821. Same class of slip if plan.md:591's shorthand `code == frozen_write` is implemented literally instead of `ErrorCode.frozen_write`.
3. **VIOLATION** — SPEC §18.3 requires assertions on "exact error shapes" (SPEC.md:271); the plan defines `frozen_write` detail `{field, group}` (plan.md:317) and `sprint_overlap` detail `{conflict_id, date_start, date_end}` (plan.md:301) but the planned tests assert only `.code`, not the structured detail.
4. **RISK** — `deferred_next_sprint` under-fenced: tests reject direct `active→done` and accept `done` via proposal, but `deferred_next_sprint` appears only as a superseding pending proposal (plan.md:545). Add direct-transition rejection and accept-success for `deferred_next_sprint`.
5. **RISK** — `accept_item_status` takes `resolved_by` as attribution only; nothing in the data layer mechanically prevents a non-human caller from invoking the accept writer (SPEC §3.2 "proposal accepted by the human").
6. **RISK** — `test_a10_blocked_by_stored_and_blockers_cleared` doesn't assert the `item_status_changed` `{from, to, cause}` event for the `todo→blocked` edge (core/contracts.py:64 payload comment).

Clean per codex: agent transition-set exactness (matches `AGENT_ITEM_TRANSITIONS`, no agent exit from `blocked`, no direct done/deferred); item→sprint assignment as plain `sprint_id` update with `item_updated`, no copy; freeze semantics (structured `frozen_write`, addenda allowed post-kickoff-freeze, independent flags); overlap inclusive vs all sprints incl. boundary equality; `blockers_cleared` read-time-only; event kinds/payload shapes vs `EventKind` comments.

## Orchestrator dispositions

1. **Accepted.** Real mypy-strict defect. Amendment A1: normalize inside the writer — `intended: list[str] = list(blocked_by or []) if to_status is ItemStatus.blocked else []` — before calling the classifier.
2. **Accepted.** Amendment A2: tests import `Addendum` from `planner.sprints.contracts`; all error-code comparisons are written against `ErrorCode.<member>`, never bare names.
3. **Accepted in substance.** The SPEC fixes the error *shape* (`{code, message, detail}`) and the code; the detail keys are plan-defined, but asserting them costs nothing and hardens the fence. Amendment A3: freeze tests assert `detail["field"]` (and `detail["group"]`); overlap tests assert `detail["conflict_id"] == A.id`.
4. **Accepted.** `PROPOSAL_ONLY_STATUSES` has two members; fence 10's "agent direct write rejected / done via proposal + accept" pattern must demonstrably hold for both. Amendment A4: extend the supersede test to accept the pending `deferred_next_sprint` proposal and assert final status; add a direct agent `active→deferred_next_sprint` rejection assert (`item_transition_forbidden`).
5. **Refuted as out of ticket scope; flagged to integrator.** Caller identity (human UI vs claim-carrying agent request) does not exist at the data layer — it arrives with the API request (claim env/headers), and §7.6/`ErrorCode.agent_forbidden` enforcement belongs to the API wiring stage, which this ticket explicitly does not touch (`api.py` outside owned files). The data writer stays the single canonical accept door with `resolved_by` as attribution, mirroring how the ticket text frames "human accept". Recorded as an integration concern in report.md: stage-4 API must gate `accept-status` behind the human surface and reject claim-carrying callers with `agent_forbidden`.
6. **Accepted.** Amendment A5: the blocked-transition test also asserts one `item_status_changed` event with payload `{"from": "todo", "to": "blocked", "cause": "agent"}`.

No structural re-plan needed: all accepted findings are additive test/typing amendments; the writer architecture, permission logic, freeze/overlap semantics, and event model stand as planned.
