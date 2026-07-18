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

- Flag-on composition never starts the legacy worker gateway; the pool owns every
  ticket employee. One stored session, one owning process — the startup assertion
  extends to ticket entities.
- The pool adopts each ticket's persisted `employee_session_id` on demand and persists
  fresh bindings through the existing ticket-session ownership writers (call-only),
  **fail-closed**: any persistence failure must leave no live-but-unpublished child
  (the S2B-OWN-001 lesson is a design input here, not a review finding to rediscover).
- The central pool-ownership crossover guard extends to ticket entities across every
  gateway-touching lifecycle op, and startup recovery settles-not-resumes stale running
  ticket turns when pool-owned.

## Worker steps through the pool

- `EmployeeStepRunner` submits the step prompt as a plain send through the ticket's
  pool child and observes settlement from teed lifecycle events (its submitted turn's
  completed/failed), then settles Panels state exactly as today. The S1/S2a demux-free
  path replaces the legacy consequence machinery for pool-owned tickets; legacy paths
  remain intact for flag-off.
- No admission gate (`D-native-turn-concurrency`): a human send during a running step
  follows stock semantics (queue, default-interrupt) — that is product behavior, not an
  error. The complete automatic-dispatch eligibility decision remains exactly as it is
  (dispatch bookkeeping, including its active-Chat factors); only chat-side rejection
  machinery is absent on the relay path.
- Step revision guidance and recovery flows ride the same send path.

## `panels` CLI identity (unblocks de-patch)

- Worker identity resolution reads `PLAN_TICKET_ID` (the existing CLI convention) from
  the child's spawn env; the `HERMES_UI_SESSION_ID`/`HERMES_SESSION_KEY` reads are
  retired from the worker resolution path (`D-child-per-employee`). Server-side
  resolution goes by ticket id directly, with the existing ownership validation.
- Flag-off ticket employees still run through the legacy gateway whose children carry
  the same spawn env the pool sets — the CLI change must hold for both compositions
  (state exactly how in the plan; if the legacy children cannot carry it, that is a
  collision to flag, not to patch around).

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
