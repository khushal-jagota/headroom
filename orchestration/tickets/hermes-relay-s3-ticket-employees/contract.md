# Hermes relay S3 — ticket employees through the child pool contract

Plan and grounding: `orchestration/hermes-relay-redesign/plan.md`. Decisions bound here:
`D-child-per-employee`, `D-chief-first-cutover` (its second half: ticket chat and steps
move TOGETHER), `D-native-turn-concurrency`, `D-only-free-hermes-features`,
`D-codex-loop-cap`, `D-stock-hermes-only` (no Hermes-side change; S3 completing unblocks
the separate de-patch stage). Builds on landed S1/S2a and on S2b —
**implementation must not begin until the orchestrator confirms S2b is integrated and
verified green**; planning and its single review round may proceed now.

## Outcome

Flag-on, every active ticket's employee is its own pool child: worker steps are
submitted through it, ticket chat panes speak the neutral vocabulary over the relay
(step turns stream live into the pane — chat IS the worker, preserved), the legacy
worker gateway is never started, and `panels` CLI worker identity reads the Panels-set
spawn env. Flag-off is exactly today's system. Gates, proposals, the resolution engine,
and the eligibility decision are untouched.

## Ownership handoff (generalizing S2b's Chief pattern, per ticket)

- Flag-on, the pool owns every ticket employee's session; the legacy worker gateway
  OWNS NO pool-owned entity's session (guard + empty entity map), but it keeps running
  to serve its non-ticket consumers — day chat, the command catalog, entity status,
  employee-session-history — until their own cutover. The invariant is per stored
  session (one owner), enforced by the extended startup assertion; "process not
  started" was imprecise and is superseded by this wording (ruled on Collision #4).
- The pool adopts each ticket's persisted `employee_session_id` on demand and persists
  fresh bindings through the existing ticket-session ownership writers (call-only),
  **fail-closed**: any persistence failure must leave no live-but-unpublished child
  (the S2B-OWN-001 lesson is a design input here, not a review finding to rediscover).
- The central pool-ownership crossover guard extends to ticket entities across every
  gateway-touching lifecycle op, and startup recovery settles-not-resumes stale running
  ticket turns when pool-owned.

## Worker steps through the pool

- `EmployeeStepRunner` submits the step prompt as a plain send through the ticket's
  pool child and observes settlement from teed lifecycle events, then settles Panels
  state exactly as today. The S1/S2a demux-free path replaces the legacy consequence
  machinery for pool-owned tickets; legacy paths remain intact for flag-off.
- RULED (Collision #A): settlement correlation uses the `prompt.submit` ACK
  disposition (streaming / queued / steered) — the pool step gateway owns the correct
  terminal event from what stock Hermes already returns, reconstructing owned-turn
  identity without the demux. Tests must cover all three disposition variants AND an
  interleaved human send during a running step (native queue/interrupt semantics,
  `D-native-turn-concurrency`).
- No admission gate (`D-native-turn-concurrency`): a human send during a running step
  follows stock semantics (queue, default-interrupt) — that is product behavior, not an
  error. The complete automatic-dispatch eligibility decision remains exactly as it is
  (dispatch bookkeeping, including its active-Chat factors); only chat-side rejection
  machinery is absent on the relay path.
- Step revision guidance and recovery flows ride the same send path.

## `panels` CLI identity (unblocks de-patch)

- Worker identity resolution reads `PLAN_TICKET_ID` (the existing CLI convention) from
  the child's spawn env FIRST. Server-side resolution goes by ticket id directly, with
  the existing ownership validation.
- RULED (Collision #1): the contract's original premise was false — the legacy shared
  worker child serves all tickets and cannot carry a per-ticket spawn env; identity
  there rides per-turn Hermes env. The CLI therefore keeps an explicitly TRANSITIONAL
  fallback: when `PLAN_TICKET_ID` is absent, the existing Hermes-env resolution
  applies, so flag-off remains exactly today's system. The fallback is scoped to die
  with the legacy worker path (S4 deletion); the de-patch stage (S3b) may proceed once
  flag-on is the operating mode, since the patched behavior only ever protected the
  shared-child topology.

## Ticket pane cutover

- The ticket route's chat pane uses the neutral client for pool-owned tickets flag-on;
  legacy pane flag-off. Step-generated turns and approval re-prompts appear in the
  transcript and stream live like any turn.

## Lessons carried as constraints (from S1/S2a/S2b reviews)

- Scripted-child payload shapes in tests are captured from the real `tui_gateway`
  source or the S0 record, with cites — never authored from memory.
- Every persistence write in spawn/bind paths sits inside the failure-cleanup guard.
- Behavior tests exercise the behavior (a test that stays green when the behavior is
  deleted is a defect).
- No `pytest.skip`; repo-wide ruff before reporting; contract on disk outranks
  messages.

## Acceptance (all inside `./verify`; fakes only)

1. Composition and ownership: flag-off exactly today's wiring; flag-on no legacy worker
   gateway, per-ticket adoption/persistence fail-closed, extended startup assertion,
   guard coverage, settle-not-resume recovery.
2. Steps: a full automatic step through a scripted pool child — dispatch, live
   streaming into a subscribed pane, settlement on completed and on failed; revision
   guidance; a human send mid-step following native queue semantics; eligibility
   decision unchanged (existing suites).
3. CLI identity: `panels worker` resolution from `PLAN_TICKET_ID` in both
   compositions; Hermes-env reads absent from the worker path.
4. Playwright, both flag states: existing ticket-chat scenarios re-anchored flag-on and
   unchanged flag-off; a step turn streaming live into the ticket pane; recovery after
   child reset.

## Out of scope

Deleting legacy machinery (S4), the Hermes patch revert (separate stage, after this
lands), transcript ruling, Chief changes, Hermes-side changes, production flag flip,
idle reaping.
