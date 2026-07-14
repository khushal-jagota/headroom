# AD03 — Automatic Employee-step eligibility

## Objective

Make **Automatic Employee-step eligibility** the one complete answer to whether Planner may start a
Ticket's next Employee step now.

- Replace the vague `readiness` / `runnable` vocabulary in the live runtime, tests, and docs.
- Put today's board membership, empty Ticket control status, non-terminal Stage, a next gated field, no
  parked proposal, scope permission, and no active blocker behind one framework-free eligibility function.
- Make both read-only discovery and the final transactional claim call that same function.
- Preserve the separate `EmployeeStepRunner`: discovery finds candidates; the runner owns the claim,
  prompt, Hermes turn, and settlement.

This is an internal architecture and naming replacement. It must not change which Tickets start, timing,
claim races, direct revision behavior, wake behavior, persisted values, events, API payloads, or UI.

## Binding decisions

- `CONTEXT.md`: **Automatic Employee-step eligibility** is the canonical term; avoid Ticket readiness,
  runnable Ticket, and ready Ticket.
- `D-automatic-employee-step-eligibility`: the complete decision includes today membership, control
  status, Stage, parked proposals, scope, and blockers; discovery and claim share it.
- `D-runtime-names`: discovery and Employee-step execution remain separate, and the claim is the race-safe
  authority.
- `D-readiness-ring`: a wake is best-effort same-process acceleration only; SQLite and the timer remain
  canonical.
- `D-ad02-definition-owns-workflow`: Stage interpretation requires the Ticket's explicitly resolved
  `WorkerTypeDefinition`; stored reads do not.
- `PRINCIPLES.md`: pure rules, one canonical writer per transition, descriptive names, and domain actions
  owning commit-before-wake ordering.

## Contract boundary

The delegated plan must specify and the orchestrator will lock the exact declarations in:

- new `src/planner/runtime/automatic_employee_step_eligibility.py`;
- new `src/planner/runtime/automatic_employee_step_discovery_loop.py`;
- new `src/planner/runtime/automatic_employee_step_eligibility_wake.py`;
- `src/planner/runtime/employee_step_runner.py` and `src/planner/runtime/__init__.py`;
- `src/planner/tickets/data.py`, against the existing `Ticket`, `TicketStatus`, `AtCap`, and
  `WorkerTypeDefinition` contracts; and
- `src/planner/core/loops.py`, which keeps optional discovery and always-composed execution.

The intended contract is:

1. `is_eligible_for_automatic_employee_step(conn, ticket, *, planning_day_id,
   worker_type_definition) -> bool` is the only production rule answering the complete question.
2. The answer is true exactly when the Ticket is on `planning_day_id`, has `ticket_status=empty`, is at a
   non-terminal Stage with a gated field, has no parked proposal, is allowed by `(ceiling, at_cap)`, and
   has no active blocker. The explicit definition belongs to the Ticket's stored Worker type.
3. `AutomaticEmployeeStepDiscoveryLoop` owns periodic/wakeable, read-only discovery. Its candidate query
   may narrow to Tickets attached to the current planning day, but it contains no separate control-status,
   terminal-Stage, proposal, scope, or blocker rule. Every candidate is passed to the complete function.
4. Discovery asks `EmployeeStepRunner.try_run_automatic_step(ticket_id)` to attempt work. It passes no
   prompt, worker, definition, or partial eligibility guard.
5. `tickets.data.claim_automatic_employee_step` is the one final status writer. Inside its
   `BEGIN IMMEDIATE` transaction it loads the current Ticket and definition and invokes a required
   eligibility check. There is no optional guard, `None` bypass, or second reduced claim predicate.
6. The runner resolves the current planning day inside that claim transaction and supplies the same
   `is_eligible_for_automatic_employee_step` function used by discovery. A planning-boundary wait, day
   removal, status change, proposal, scope change, Stage change, or blocker appearing after discovery must
   prevent the claim and Hermes prompt.
7. Directly requested revision turns do not call automatic eligibility. They retain their existing
   reserved handoff and claimed-running-status contract.
8. `AutomaticEmployeeStepEligibilityWake` is the narrow no-state, no-argument best-effort wake port used
   after an action has committed. `LoopAutomaticEmployeeStepEligibilityWake` delivers to discovery;
   `NoOpAutomaticEmployeeStepEligibilityWake` is used without the polling lock. Its operation is `wake()`,
   not `ring()`.

## Rejected vocabulary and parallel rules to delete

The plan must remove, not alias:

- `runtime/readiness.py`, `is_runnable`, and imports/variables/comments that call eligibility readiness or
  runnability;
- `runtime/ticket_readiness_loop.py`, `TicketReadinessLoop`, `ticket_readiness_loop`,
  `run_ready_step`, and readiness-named thread/log text;
- `runtime/readiness_doorbell.py`, every `*ReadinessDoorbell`, `readiness_doorbell`, and the `ring()` wake
  operation; and
- `tickets.data.start_run_if_runnable`, its optional `guard`, and discovery SQL that independently repeats
  a subset of the eligibility decision.

Historical orchestration evidence keeps the vocabulary that was true when written. The generic English
word “ready” may remain where it does not name this domain concept, but live Employee-runtime code and docs
must use the exact eligibility/discovery/wake names.

## Planning task

Produce `plan.md` only. Do not edit implementation or contract files.

The plan must:

1. Inventory every production, test, typing, doc, generated-doc, and root-instruction path affected by the
   three module replacements and their consumer names.
2. Give the exact eligibility, discovery loop, wake port/adapters, runner operation, and transactional
   claim signatures. No compatibility re-export or old-name alias is allowed.
3. Trace discovery from planning-day resolution through candidate enumeration and the complete decision,
   then trace the runner from asynchronous attempt through transaction-time day resolution, the same
   complete decision, atomic claim, prompt, and settlement.
4. Preserve the current lock winner/loser/disabled/partial-start composition, stop ordering, timer backstop,
   best-effort wake failure handling, and action-owned commit-before-wake behavior.
5. Prove direct revision does not become subject to automatic eligibility and retains strict real-Hermes
   delivery through the reserved runner handoff.
6. Define a bounded implementation allowlist. Module moves and renamed tests must be explicit deletes plus
   adds; no compatibility module, facade alias, wrapper, or parallel predicate is allowed.
7. Replace old predicate tests with a full decision table that includes every conjunct, both shipped Worker
   types, and an explicit planning-day id. Include discovery and final-claim proofs that exercise the same
   function rather than merely producing coincidentally equal outcomes.
8. Preserve and rename the wake acceptance matrix: Ticket, Day membership, blocking Link, and runner
   settlement success paths; failures/no-ops; lock/no-lock composition; delivery failure; timer fallback;
   and no Ticket id/state/IPC in the wake.
9. Add static assertions that fail if the deleted files, symbols, state attributes, `ring()` operation,
   optional claim guard, or partial discovery predicates return in live code.

## Acceptance

- One function gives the complete Automatic Employee-step eligibility answer, including today membership
  and empty control status.
- Discovery and the transaction-time claim both invoke that function with the Ticket's explicit
  `WorkerTypeDefinition`; no partial rule can disagree with it.
- The final claim still owns race safety and resolves the planning day after its write transaction begins.
- A stale discovery cannot produce a status event, Panels Chat row, gateway call, or Employee prompt after
  any eligibility conjunct changes.
- Direct revision bypasses automatic eligibility and retains its current same-session delivery behavior.
- `AutomaticEmployeeStepDiscoveryLoop` and `EmployeeStepRunner` remain separate responsibilities.
- Domain actions still commit before a best-effort eligibility wake; wake failure never reverses a
  successful action; SQLite and the periodic timer remain canonical.
- No readiness/runnable module, symbol, state seam, claim writer, doorbell type, compatibility alias, or
  parallel discovery predicate remains in live code.
- Live Markdown/HTML docs and `AGENTS.md` describe the new names and the one complete decision plainly.
- Product-visible behavior and payloads are unchanged, and the full canonical `./verify` passes once after
  implementation and review fixes.

## Out of scope

- Combining discovery with `EmployeeStepRunner`, changing Hermes/gateway/session behavior, or changing
  direct revision ownership.
- Adding a durable queue, retry policy, cross-process wake, Ticket id payload, event kind, Ticket status,
  database column, or migration.
- Changing Worker-type workflow rules, Ticket scope semantics, blocker semantics, planning-date math, or
  which domain actions trigger a wake.
- Review, Chat, Markdown, resource-catalogue, or Employee-session-history work from AD04–AD09.
