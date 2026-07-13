# Implementation plan — t_yctn6m7m

This plan implements the accepted ticket contract and the owner’s continuation-first correction. It does not introduce a second scheduler or infer ticket completion from chat text.

## File ownership

- Runtime composition and ownership: `src/planner/core/server.py`, `src/planner/core/loops.py`, `src/planner/core/config.py`, `config.yaml`, `src/planner/runtime/employee_step_runner.py`, `src/planner/runtime/ticket_readiness_loop.py`, and one narrowly named recovery/lifecycle module only if it removes mixed responsibility.
- Chat continuation: `src/planner/chat/data.py`, `src/planner/chat/service.py`, its API wiring only where a lifecycle owner must be injected, and chat contracts only for a real shared type.
- Same-session transport and bounded gateway stop: `src/planner/core/adapters/`, `src/planner/minds/shared_gateway.py`, `src/planner/minds/sessions/service.py`, and `src/planner/minds/gateway.py` only where the existing interfaces cannot express strict resume/deadline behavior.
- Tests: focused unit/integration files beside the existing employee runner, chat, loops/server, config, and session/gateway tests; one e2e flow for the visible recovered transcript/state.
- Current docs: `docs/chat.md`, `docs/employee-runtime.md`, and `docs/systems.md` only where their present behavior/deferred gaps change.

## Strict-TDD tracer bullets

1. **Atomic visible-turn rollover.** First add a failing chat-data test that starts a durable running turn with partial output, invokes the wished-for recovery writer, and asserts one transaction settles the old turn as interrupted, preserves its partial output/message, clears transient activity, emits the normal finish/start events, and creates exactly one fresh recovery turn. Add mismatch/idempotence controls so repeated process interruption can recover again without two running turns.

2. **Ticket-worker same-session continuation.** Add failing employee-runner tests that seed `ticket_status='agent_running_step'` with its authoritative `tickets.chat_session_key` and stale worker turn. Recovery must settle/roll the visible turn, call `run_ticket_step(... require_existing_session=True)` exactly once, never call `_next_step_prompt`, and never submit the original prompt. Assert the recovery message itself tells the worker that Panels restarted, to inspect the canonical ticket and existing conversation, continue unfinished work, avoid repeating completed actions, file the currently requested proposal, and—if it already filed that proposal—only say so in chat. Let the existing proposal writer produce `awaiting_approval`. Cover no-proposal completion, proposal filing, null/missing session, resume-not-found, session-key mismatch, and an impossible current-proposal-plus-running claim; failures must be explicit and must never remint or duplicate submission.

3. **Ordinary chat same-session continuation.** Add failing chat-service/gateway tests for ticket/day/Chief human turns. The authoritative entity session key must be resumed strictly; worker-owned ticket ids are excluded so one entity is not recovered twice. The old visible turn keeps partial output and the fresh system recovery turn continues the response. Extend the gateway protocol and every real/offline/fake/routing implementation only as needed to express “require existing session”; prove missing sessions call resume only—never create or submit.

4. **One startup coordinator before discovery.** Add failing composition tests for the exact order: schema/type audit → skill provisioning → role gateways ready → recovery discovery/admission → readiness polling start. Ticket continuation is driven by durable `agent_running_step` tickets, while ordinary chat continuation excludes those ticket ids so one entity is never submitted twice. Separately settle any stale worker-origin running turn whose ticket has already left `agent_running_step`—especially the valid crash window after the atomic proposal write moved the ticket to `awaiting_approval` but before the visible worker turn settled—without sending another recovery prompt or changing ticket status. Starting without the polling lock still performs direct ticket/chat recovery because recovery belongs to the single-process service lifecycle, not the optional readiness timer.

5. **Tracked admission and one bounded stop budget.** Add failing tests around one monotonic absolute deadline derived from the configured grace. Stop accepting new employee/chat turns, stop discovery, drain accepted work, interrupt remaining owned sessions, and then stop both worker and Chief role gateways using only the time remaining on that same deadline. Thread joins, `LiveSessionManager` concurrent-stop waits and command-lock acquisition, router joins, and gateway-child waits must all receive the remaining budget; serial role-gateway cleanup must not restart the clock. Preserve the existing forced child-kill fallback. A service-stop interruption must settle the visible turn as interrupted while leaving unfinished ticket control durably `agent_running_step` for the next startup continuation—not `errored`.

6. **Failure recovery through existing control.** Add focused route/domain tests proving an unrecoverable ticket is user-visible and that existing takeover/release/readiness-doorbell behavior provides clear/retry without a parallel retry queue. Prove both non-actions: no automatic original-prompt replay and no natural-language reply parsing into ticket status.

7. **Visible behavior and documentation.** Add one Playwright flow that starts from a stale visible turn, proves partial output remains at rest, shows the recovery continuation as the active/new turn, and reaches the normal proposal or chat-complete state without duplicate prompt text. Update the three current system docs to describe continuation-first restart behavior and remove the stale “no recovery” deferrals.

## Review and verification

- Run each named focused test to meaningful RED before its production slice, then the exact command to GREEN.
- Run focused employee/chat/loops/session/config tests plus frontend compile/build and the new e2e flow.
- Obtain a read-only Codex implementation review against `contract.md` and this plan; disposition every finding in a tracked review artifact.
- Spot-check startup ordering, the atomic rollover writer, same-session/no-remint enforcement, and all deadline calculations.
- Run `PYTHONPATH="$PWD/src" ./verify` once in the isolated worktree. Commit the verified branch. Do not merge or remove the worktree during Implementation.
