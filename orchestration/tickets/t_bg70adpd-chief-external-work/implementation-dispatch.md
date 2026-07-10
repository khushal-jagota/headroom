# Reviewed implementation dispatch — t_bg70adpd

Implement this ticket under strict TDD in `/tmp/planning-v2-chief-external-work`. Do not touch files outside the scope below. Preserve the accepted user-facing contract in `ticket.md`.

## 1. Truthful request identity

Replace the misleading “no header = human” model with these meanings:

- No `X-Plan-Actor` header: **unattributed**. Ordinary product/CLI operations may be unattributed; do not claim a person performed them.
- `X-Plan-Actor: chief`: the Panels Chief.
- Any other actor header (`worker`, legacy `agent`, or another agent value): an attributed non-Chief agent and subject to the existing direct-write restrictions.

CLI request modes:

- Ordinary commands preserve the ambient `PLAN_ACTOR` header when present; otherwise they send no actor header. This lets the Chief use ordinary commands, keeps terminal use unattributed, and prevents a gateway worker from shedding its worker identity by invoking `panels ticket`.
- Worker commands use the ambient actor or legacy `agent` fallback.
- Chief commands use the ambient actor and the server rejects them unless it is exactly `chief`; do not let the command synthesize `chief` when run from a worker or unattributed shell.

Provision gateway roles in production without changing the dirty shared-gateway implementation: construct the worker gateway with `PLAN_ACTOR=worker` and the Chief gateway with `PLAN_ACTOR=chief` through its existing `base_env` seam in `src/planner/core/server.py`. Preserve all other environment values.

Rename misleading backend helpers/comments/constants/tests in the touched authority/ticket paths: ordinary direct writes are not “human” writes. A good shape is `unattributed`, `chief`, and `attributed non-Chief agent`, with a helper that permits unattributed/Chief direct operations and a `require_chief` helper for the new routes. Keep the stable `agent_forbidden` error code.

## 2. Exact contracts and routes

Add stdlib TypedDict contracts in `src/planner/tickets/contracts.py`; do not declare request shapes in API code.

### Reconcile existing

Route: `POST /api/chief/tickets/{ticket_id}/reconcile-from-external-work`

Allowed body keys only:

- required `state: str`
- required non-empty `user_note: str` (the complete resulting ticket note; the Chief reads and preserves prior context when composing it)
- optional `recap: str`
- optional `success: str`, `approach: str`, `plan: str`, `result: str`

### Create already-populated

Route: `POST /api/chief/tickets/from-external-work`

Allowed body keys are the reconcile keys plus ordinary creation metadata:

- required `title: str`
- optional `priority`, `deadline`, `project`, `project_id`, `sprint_id`, `sprint_item_id`

Reject unknown keys, including `proposal`, `fields`, `ceiling`, `at_cap`, and `ticket_status`. These commands do not expose proposal, control-status, or scope internals.

## 3. Coherent final ticket rules

Put framework-free validation/build rules in `src/planner/tickets/logic/`; canonical writes remain in `src/planner/tickets/data.py` and pass through the ticket resolution/decision door.

- Target state must be in linear `STATE_ORDER`; `dropped` is invalid.
- Existing reconciliation cannot move backward.
- Merge provided canonical values over existing settled values, preserving field user notes.
- The final settled-value prefix must match the target state exactly:
  - `needs_success`: no settled values
  - `needs_approach`: success only
  - `needs_plan`: success + approach
  - `in_progress`: success + approach + plan
  - `needs_review` or `done`: all four values
- Every provided/required canonical value, title, recap, and user note uses existing body/title/deadline/project/parent validation.
- Reject any pending proposal rather than silently clearing or superseding it.
- Reject `agent_running_step`, `awaiting_approval`, and `user_takeover` control statuses. Permit `empty`; permit `errored` and normalize it to `empty` after a successful reconciliation.
- Reject if any Panels chat turn for the ticket is running.
- Final scope is `ceiling = target state`, `at_cap = stop`; final `ticket_status = empty`. This prevents an imported partial ticket from auto-running unexpectedly.
- The create operation writes the complete coherent ticket atomically. Reconciliation updates all requested content/state/scope/status atomically. Any invalid input leaves ticket and events unchanged.

## 4. Existing events and readiness

Add no event kind and no external-work record/table.

Use the existing event vocabulary in the same transaction:

- Create: `ticket_created`; `field_value_edited` for each initial settled field; `recap_updated` when non-empty; `state_changed` when target differs from `needs_success`; `scope_changed` when final scope differs from the default.
- Reconcile: `ticket_updated` when user note changes; `field_value_edited` per changed settled value; `recap_updated` when recap changes; `state_changed` when state changes; `scope_changed` when scope changes; `ticket_status_changed` when `errored` normalizes to `empty`.

Use neutral/direct cause names, not `human_*`. Notes carry the external-work explanation; event payloads need no new evidence/cause schema. Poke System A after either successful route.

## 5. Exact CLI

Add a top-level `chief` Click group to the existing `panels` CLI:

- `panels chief reconcile-ticket-from-external-work <ticket-id>`
- `panels chief create-ticket-from-external-work`

Common options:

- required `--state` using linear ticket states only
- required `--user-note-file`
- optional `--recap-file`
- optional `--success-file`, `--approach-file`, `--plan-file`, `--result-file`

Create also exposes the same metadata selectors as ordinary `ticket create`: required `--title`; optional `--priority`, `--deadline`, `--project`, `--project-id`, `--sprint`, and `--sprint-item`.

Both support `--json`. Terse output must name the ticket id, external-work action, and final state. Chief commands use the Chief request mode above and fail with the normal structured error when called with no actor or a worker actor.

## 6. Skill and documentation changes

Edit source skills only:

- `skills/panels-chief-of-staff/SKILL.md`: add an **External work intake** section. Trigger: user says work already happened elsewhere and Panels should reflect it. Sequence: inspect `ticket list/show`; choose existing vs new; compose the complete user note preserving report/context and the reconciliation reason; invoke the corresponding exact command; read back with `ticket show`; report id and final state. Boundaries: only already-performed work; not ordinary planning, doing a ticket, approving proposals, or convenience state jumps; no special ambiguity machinery in v1.
- `skills/panels-worker/SKILL.md`: add only **Never invoke `panels chief`.** No extra explanation.
- `skills/panels/SKILL.md`: command map says `ticket` = ordinary actor-neutral operations, `worker` = gated ticket work, `chief` = importing work already completed outside Panels.

Update `docs/systems.md`, `docs/tickets-and-gates.md`, and any owning CLI doc that currently claims headerless/ordinary commands are human. Update `AGENTS.md` command-group map if needed. Do not edit `data/hermes-home/skills`; provisioning already symlinks repo source skills.

## 7. Strict TDD and verification

Capture RED before production edits. Add focused tests that fail for the missing behavior, then implement the smallest vertical slices.

Required coverage:

- request identity/header assembly: ordinary ambient/no-ambient, worker fallback, Chief requires ambient `chief`
- API rejects non-Chief access to both routes
- exact request-key validation
- target-state/value-prefix matrix
- no backward reconciliation
- transaction rollback on invalid late field
- pending proposal and active-control/running-turn rejection
- errored normalization and coherent scope/status
- exact existing event sequence and System A poke
- real-server CLI create and reconcile JSON output
- non-JSON terse output for both commands
- ordinary commands continue to work from unattributed and Chief contexts while attributed workers cannot use restricted direct writes
- source skill content and existing provisioning symlink test

Focused checks precede independent diff review. Run full `./verify` only after integration on the settled main worktree.

## File scope

Expected ticket-owned files:

- `src/planner/cli/main.py`, `src/planner/cli/http.py`
- `src/planner/core/authctx.py`, `src/planner/core/server.py`
- `src/planner/tickets/contracts.py`, `src/planner/tickets/api.py`, `src/planner/tickets/data.py`
- `src/planner/tickets/logic/admission.py`, `resolution.py`, `decisions.py`, `machine.py`, or one narrowly named new pure-logic module if it meaningfully deepens the domain
- focused tests under `tests/unit/` and `tests/e2e/`
- the three source skills and owning docs named above
- this orchestration folder

Avoid `src/planner/minds/shared_gateway.py`, `tests/unit/test_minds.py`, frontend files, chat/files domains, `PROGRESS.md`, and `decisions.md` in the isolated implementation; those are shared or updated by the parent after integration.

Return exact RED/GREEN commands and outputs, changed files, and any blocker. Commit the implementation locally on `feat/chief-external-work`; do not push.
