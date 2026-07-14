# Architecture deepening × restart recovery conflict-resolution plan

## Outcome and precedence

Resolve the merge by treating the architecture-deepening parent as the final public and domain shape, then
porting the restart-recovery parent's continuation and bounded-shutdown behavior into that shape. The result
must keep both parents' proven behavior without restoring an interface that AD01–AD09 deleted.

The integration has these non-negotiable rules:

- A Ticket has an explicit, immutable `worker_type`, a directly read `stage`, and a nullable
  `employee_session_id`. No live Python, HTTP, CLI, TypeScript, SQLite, registry, seed, recovery, or test
  helper chooses `coding`; tests pass it literally where needed. Only the terminal migration may classify a
  genuinely historical pre-Worker-type Ticket as `coding`.
- `AutomaticEmployeeStepDiscoveryLoop`, the complete eight-factor
  `is_eligible_for_automatic_employee_step`, `claim_automatic_employee_step`, and
  `AutomaticEmployeeStepEligibilityWake` remain the only automatic-discovery vocabulary and boundaries.
- `ChatTurnLifecycle` remains the one human Chat admission, execution, Pause, and now restart-continuation
  owner. Human delivery remains typed `HumanChatObservation`; no `stream`, `ChatStreamChunk`, generic
  history result, callback-only session observation, top-level human launcher, or compatibility method
  returns.
- Panels Chat remains only `chat_messages`, the live `chat_turn`, and display-safe activity. Hermes Employee
  history remains the explicit Ticket-only `read_employee_session_history` /
  `/api/tickets/{ticket_id}/employee-session-history` boundary. Recovery must not make Chat state consult or
  copy Hermes history.
- Startup resumes stranded Employee work and ordinary human Chat in the same stored Hermes conversation,
  using a fresh recovery message and never replaying the original prompt. Missing, unusable, or mismatched
  identities fail honestly and never create a replacement session.
- Shutdown uses one monotonic absolute deadline across discovery, the Employee runner, both routed role
  gateways, live-session cleanup, router joins, and child cleanup. Shutdown-time Employee interruption keeps
  the Ticket at `agent_running_step` for the next startup.

The integration contract deliberately and narrowly supersedes AD03's original no-argument discovery
`stop()` signature so discovery receives that same absolute deadline. The method's owner, stop ordering,
thread wake, candidate query, eligibility call, claim boundary, and all other public methods remain AD03's
exact design.

## Adapted interfaces

### Typed human continuation

Keep the AD06/AD09 transport and add only the strict-resume choice that restart continuation requires:

```python
def run_human_turn(
    self,
    session_key: str | None,
    entity_id: str,
    text: str,
    mode: str,
    bind_session_key: HumanSessionKeyBinder,
    image_paths: tuple[Path, ...] = (),
    *,
    require_existing_session: bool = False,
) -> Iterator[HumanChatObservation]: ...
```

This is still the single human transport capability. `require_existing_session=True` means a non-null stored
identity must resume or reuse its live handle; a missing Hermes session performs `session.resume` only and
must not call `session.create`, bind a new identity, attach an image, dispatch a command, or submit a prompt.
Exact `/new` remains the sole force-fresh operation and is invalid on the strict recovery path.

Extend the private admitted value with exactly one execution fact:

```python
require_existing_session: bool
```

Normal admissions set it to `False`. Restart admissions set it to `True`; they keep
`force_fresh_session=False`, no images, the stored identity as `expected_session_key`, and a gateway snapshot
from the lifecycle's existing dynamic provider.

Add the lifecycle method:

```python
def recover_human_turn(
    self,
    entity_id: str,
    mode: str,
) -> ChatTurn | None: ...
```

It validates the entity and mode, rejects the Employee-owned `agent_running_step` Ticket lane, reads the
authoritative identity (`employee_session_id` for a Ticket; Chat-specific key for a Day or top-level agent),
and atomically rolls the stale visible turn into one fresh system recovery turn. It preserves partial output,
requires the stale turn's internal session key to match the authoritative identity, and launches the normal
typed `_execute_turn` daemon before returning. No stored identity settles the stale turn as an explicit
recovery error and launches nothing. Repeated restarts settle each prior recovery turn and create exactly one
new running turn.

The server remains the startup coordinator: role gateways become ready first, ordinary running human turns
are admitted through this lifecycle method without waiting for model completion, then Employee recovery and
automatic discovery start.

### Employee continuation

Keep the AD03/AD09 `EmployeeStepRunner` constructor and automatic method names. Add:

```python
def recover_running_step(self, ticket_id: str) -> None: ...
def stop(self, *, deadline: float | None = None) -> None: ...
```

The runner tracks owned Ticket ids as well as its active count, so startup recovery, automatic discovery,
and direct revision cannot submit the same Ticket twice in one process. Recovery reads a Ticket already at
`agent_running_step`, requires `employee_session_id`, rolls its stale worker-origin Chat turn, and calls the
existing employee-only `run_ticket_step(..., require_existing_session=True)` with the owner-approved recovery
message. It never calls `_next_step_prompt` and never runs automatic eligibility or a second claim. Session
rotation still passes through `EmployeeSessionIdTransition` and the one canonical Ticket writer.

The owner-approved message must tell the Employee that Panels restarted, to inspect the canonical Ticket and
existing conversation, continue unfinished work, avoid repeating completed actions, file the currently
requested proposal through the normal tools, and, if already filed, only say so in Chat. Proposal filing
remains the sole transition to `awaiting_approval`; recovery does not infer completion from prose.

No stored identity or a stale-turn identity mismatch settles the visible turn as errored, marks the still
running Ticket errored through the canonical Ticket writer, and calls no gateway. A shutdown-time gateway
error or interruption settles only the visible turn as interrupted and leaves `ticket_status` and
`employee_session_id` intact. The same outcome outside shutdown remains an ordinary errored run.

### One shutdown deadline

Use these exact lifecycle signatures:

```python
async def BackgroundLoops.stop(self, *, deadline: float | None = None) -> None: ...
def EntityRoutingGateway.shutdown(self, *, deadline: float | None = None) -> None: ...
def SharedGateway.shutdown(self, *, deadline: float | None = None) -> None: ...
```

If the server supplies no runtime deadline, `BackgroundLoops` derives one once from
`shutdown_grace_seconds`. It passes that same absolute value to discovery and the Employee runner. The routed
gateway deduplicates role gateways and passes the same value to each. `SharedGateway` passes it to
`LiveSessionManager.shutdown` and gives `GatewayChild.shutdown` only `max(0, deadline - monotonic())` seconds.
Do not catch broad `TypeError` to retry a lifecycle method without the deadline.

## Conflict resolutions by file

### `PROGRESS.md`

Preserve the architecture parent's completed AD01–AD09 record, final whole-program `NO VIOLATIONS` review,
and 738-unit/87-browser canonical pass. Preserve the restart parent's verified continuation/shutdown facts
and its 700-unit/78-browser parent-branch pass, but collapse that older cycle rather than replacing the newer
architecture memory. Prepend one current integration cycle naming both parent heads, the conflict-resolution
stage, the invariant that architecture vocabulary is authoritative, focused checks as they pass, the pending
independent review/full verify/merge, and any blocker. Do not claim the integrated gate before it runs.

### `docs/employee-runtime.md`

Use the architecture parent as the base: eight-factor Automatic Employee-step eligibility, atomic claim,
`AutomaticEmployeeStepDiscoveryLoop`, eligibility wake, explicit Worker types, stored Stage,
`employee_session_id`, and explicit Employee history all survive. Add the restart parent's startup sequence,
same-session recovery message, partial visible-turn preservation, post-proposal stale-turn settlement,
strict failure/no-remint rule, repeated-restart behavior, and single shutdown deadline. Say
`employee_session_id`, Employee, automatic discovery, and eligibility throughout; do not mention Ticket
readiness, a readiness doorbell, Ticket types, Ticket `chat_session_key`, or Hermes history being merged into
Panels Chat. Remove the stale deferred claim that restart recovery does not exist.

### `docs/systems.md`

Keep the architecture parent's system map and boundaries: Worker definitions, Ticket Stage, complete
eligibility, typed `ChatTurnLifecycle`, one canonical Employee-session writer, explicit Ticket-only Hermes
history, Resource Catalogue, and Managed Markdown. Add startup order and lane ownership: both gateways ready;
ordinary human Chat continuation admitted non-blockingly; stranded Employee steps recovered; only then
automatic discovery begins. Add the same-session/no-original-prompt, post-proposal settlement, strict
missing/mismatch, repeated-restart, and single-deadline shutdown rules. In the Chat section, state that the
fresh visible system recovery turn is Panels state while the actual recovery text is also delivered through
the Hermes session path; neither is inferred from the other. Do not revive the old session-key friction item,
generic Chat history, or readiness vocabulary.

### `src/planner/chat/service.py`

Take the architecture parent's file structure and public surface: `_resolve` reads Ticket
`employee_session_id`; `state` is Panels-only; `ChatTurnLifecycle` owns admission, typed execution, Pause,
causal binding, and first-wins settlement; worker helpers remain separate. Port restart behavior only as
`ChatTurnLifecycle.recover_human_turn` plus the `require_existing_session` field on
`_AdmittedHumanChatTurn`. The method uses `chat_data.roll_running_turn_for_recovery`, never the deleted
top-level `_run_human_turn`, `start_human_turn`, `pause_turn`, `stream`, `send`, `run_command`, or `history`.
`_execute_turn` passes the strict flag to `gateway.run_human_turn` and otherwise consumes only
`ChatActivityObservation`, `HumanChatOutputDelta`, and `HumanChatCompletion`. Missing/mismatched recovery
settlement goes through the canonical Chat settlement implementation and creates no replacement turn or
gateway call.

### `src/planner/core/adapters/base.py`

Keep the architecture parent's narrow protocol: `status`, Ticket-only
`read_employee_session_history`, typed `run_human_turn`, `interrupt`, and `catalog`. Add the keyword-only
`require_existing_session=False` argument to `run_human_turn`. Do not bring back `history`, `send`, `stream`,
`run_command`, `ChatHistory`, `ChatSendResult`, `ChatStreamChunk`, `CommandRunResult`, or an optional
session-key callback.

### `src/planner/core/adapters/fakes.py`

Keep AD09's `EmployeeSessionHistory` storage/read and AD06's typed human observations. Add the strict keyword
to Echo and Offline implementations. Echo must reject strict recovery with a null identity and must reuse the
provided identity without minting; normal and exact `/new` behavior remains unchanged. The fake continues to
record actual Employee history independently of Panels Chat. No legacy result/chunk classes or generic
history method return.

### `src/planner/core/adapters/real.py`

Keep the architecture placeholder's Ticket-only history and typed human-turn methods, adding the strict
keyword to the latter before raising the normal offline error. Do not restore any of the removed legacy
adapter methods or imports.

### `src/planner/core/loops.py`

Use the architecture parent's names and composition: `AutomaticEmployeeStepDiscoveryLoop`,
`AutomaticEmployeeStepEligibilityWake`, `try_run_automatic_step`, and the optional polling lock all survive.
Port three restart/shutdown responsibilities:

1. discover every Ticket durably at `agent_running_step` and call `recover_running_step`, whether discovery
   is disabled, this process loses the polling lock, or it owns the lock;
2. before recovery, settle a worker-origin running Chat turn whose Ticket has already left
   `agent_running_step`, preserving partial output without a prompt or Ticket mutation; and
3. stop discovery then the runner with one absolute deadline before releasing the polling lock.

For the lock owner, stale-turn settlement and Employee recovery happen after runner construction and before
`AutomaticEmployeeStepDiscoveryLoop.start`. For the disabled and lock-loser branches they still happen even
though no discovery loop starts. Use `settle_chat_turn`'s canonical transition for stale-turn settlement,
not the legacy `finish_turn` copy. Keep startup failures isolated so direct revisions remain available.

### `src/planner/runtime/automatic_employee_step_discovery_loop.py`

Keep the auto-merged deadline-aware `stop(*, deadline=None)` as the one intentional integration-level
signature supersession recorded in `main-integration.md`. It still sets stop and wake before joining, and
uses only `max(0, deadline - monotonic())` as the join timeout. Do not change polling, candidate discovery,
the complete eligibility call, submission, start, or wake behavior.

### `src/planner/minds/shared_gateway.py`

Take the architecture parent's implementation wholesale for Employee history normalization, typed human
observations, causal binding, exact `/new`, dormant retry, Employee delivery, and command/image ordering.
Port strict continuation into the existing `run_human_turn` method with the keyword-only flag. Strict mode
requires a non-null identity and calls `_resume_or_create(..., allow_create=False,
reuse_live_session=True)`; it must reject exact `/new` and must bind any legitimate resumed/rotated id before
the write. `EntityRoutingGateway` forwards the flag to the selected role gateway.

Port the restart parent's shutdown signatures and monotonic remaining-budget calculation to both routed and
role gateways. Preserve `read_employee_session_history`; do not resurrect generic `history`, `send`,
`stream`, `run_command`, or old stream helpers.

### `src/planner/runtime/employee_step_runner.py`

Use the architecture parent's complete automatic claim, Worker-type definition lookup, stored Stage prompt,
`EmployeeSessionIdTransition`, eligibility wake, and method name `try_run_automatic_step`. Port the restart
message, per-Ticket in-process ownership set, `recover_running_step`, stopping state, and deadline-aware
drain. Recovery is a third explicit execution mode beside automatic and revision; it reads the already
claimed Ticket rather than invoking `claim_automatic_employee_step`, rolls the stale visible worker turn,
and strictly resumes `employee_session_id`. All session updates and settlement transitions retain AD09's
canonical writer arguments. Missing/mismatch and shutdown behavior follow the adapted Employee-continuation
contract above. Do not reference `readiness`, `coding_bridge`, Ticket type, `state`, `chat_session_key`,
`start_run_if_runnable`, or the old readiness doorbell.

### `tests/unit/test_chat_activity.py`

Keep every architecture-parent activity and typed-human-turn test. Port the restart parent's atomic
rollover, strict same-session ordinary continuation, null-key failure, mismatch failure, and nonblocking
background-admission tests to `ChatTurnLifecycle` and typed observations. Test gateways implement
`run_human_turn(..., require_existing_session=...)` and yield `HumanChatOutputDelta` /
`HumanChatCompletion`; no test calls a deleted top-level launcher or creates `ChatStreamChunk`. Assert old
partial output is one settled visible message, one fresh system recovery turn is created, repeated restart
leaves exactly one running turn, missing/mismatch calls no gateway and does not change the authoritative id,
and recovery returns while the typed stream remains blocked.

### `tests/unit/test_core_loops.py`

Keep the architecture parent's discovery/wake naming and lock-composition assertions. Port and adapt the
restart tests for recovery-before-discovery, recovery when the lock is lost, post-proposal stale worker-turn
settlement, one absolute loop/runner deadline, no broad-`TypeError` retry, routed-gateway deadline
propagation, nonblocking ordinary Chat startup recovery, the test-mode startup-recovery switch, and runtime
before gateway shutdown. Every created Ticket supplies `worker_type="coding"`; every Ticket identity assertion
uses `employee_session_id`; runtime doubles expose `automatic_employee_step_discovery_loop` and
`automatic_employee_step_eligibility_wake`. Test human gateways use the typed observation method and strict
flag. Preserve the existing absolute Hermes-home proof.

### `tests/unit/test_minds.py`

Use the architecture parent's complete test file so typed observations, causal winner binding, exact `/new`,
Ticket-only Employee history, command/image ordering, and removed-interface guards remain covered. Port the
restart parent's `LiveSessionManager` deadline-failure test and update all shutdown doubles to accept the
keyword-only deadline. Add a typed-human strict-resume regression against the real `SharedGateway`: a 4007
resume failure with `require_existing_session=True` sends only `session.resume`, never creates, binds,
prepares context, attaches, dispatches, or submits. Keep the existing strict Employee-step test separately.
No legacy stream/history test or compatibility caller survives.

## Required integration repairs outside the conflicted paths

These files auto-merged text from both parents but need explicit repairs to make the combined contracts
true. They are not optional scope expansion.

### `src/planner/chat/data.py`

Keep `roll_running_turn_for_recovery`, but remove its duplicated terminal-transition code. Extract one
non-owning in-transaction implementation from `settle_chat_turn`; the public `settle_chat_turn` transaction
wrapper and atomic rollover both call that implementation. This preserves the AD06 one-settlement-door rule
while allowing old-turn settlement plus replacement-turn insertion to remain one transaction. Both
interrupted partial-output settlement and mismatch-error settlement must retain first-wins behavior, one
visible terminal message at most, one finish event, and transient activity cleanup.

### `src/planner/core/server.py`

Replace the auto-merged call to deleted top-level `chat_service.recover_human_turn` with the composed
`app.state.chat_turn_lifecycle.recover_human_turn`. The recovery coordinator may discover running human rows,
but lifecycle admission/execution remains owned by `ChatTurnLifecycle`. In production the order is schema
and Worker-type audit, skill provisioning, both role gateways ready, routed adapter installed, ordinary Chat
recovery admitted, Employee recovery admitted inside loop composition, then automatic discovery. The
test-only switch uses the same lifecycle and configured fake adapter. Keep the existing single server
deadline and dynamic gateway provider.

### `tests/unit/test_employee_step_runner.py`

Repair the auto-merged restart tests to the architecture interfaces. Replace `readiness_doorbell` with
`automatic_employee_step_eligibility_wake`, `run_ready_step` with `try_run_automatic_step`, Ticket
`chat_session_key` with `employee_session_id`, `start_run_if_runnable` with the canonical automatic claim (or
an explicit already-running fixture), and `claim_running_step_chat_session_key` with
`claim_running_step_employee_session_id` plus `EmployeeSessionIdTransition`. Preserve tests for shutdown-time
recoverability, ordinary interruption error, duplicate same-process recovery admission, exact recovery
message/no original prompt, proposal handoff, null stored identity, and stale visible-turn identity mismatch.
All fixtures pass `worker_type="coding"` explicitly.

### `tests/e2e/test_live_chat_state.py`

Repair only the restart fixture's stale architecture names: create the Ticket with explicit
`worker_type="coding"`, assign its `employee_session_id` through the canonical Ticket transition, and keep
the internal `chat_turns.session_key` for delivery/Pause. Preserve the existing browser proof that original
input appears once, partial output remains, one visible system recovery turn appears, strict continuation
settles, and no pending turn remains. Run it together with
`test_ticket_panels_rows_do_not_fall_back_to_employee_session_history` so recovery cannot repopulate Panels
rows from retained Hermes history.

## Implementation order

1. Resolve the protocol and lifecycle seam first: adapter base/fakes/real, `SharedGateway`, Chat data's one
   settlement implementation, `ChatTurnLifecycle`, and server startup coordination.
2. Resolve Employee recovery and lifecycle: `EmployeeStepRunner`, loops, and deadline propagation. Keep
   automatic eligibility/claim code byte-for-byte unless an interface rename is required.
3. Resolve the three unit-test conflicts against those final interfaces, then repair the two auto-merged
   restart test files and the browser fixture.
4. Resolve current docs and `PROGRESS.md` last so they describe the implementation actually present.
5. Run focused checks and scans below. Fix every integration defect before independent review. Do not run
   full `./verify` until the resolved change is reviewed and ready for the canonical gate.

## Focused proof

Run the affected Python set with this worktree on `PYTHONPATH`:

```text
tests/unit/test_chat_activity.py
tests/unit/test_human_chat_turn.py
tests/unit/test_employee_step_runner.py
tests/unit/test_core_loops.py
tests/unit/test_minds.py
tests/unit/test_minds_sessions.py
tests/unit/test_employee_session_history.py
tests/unit/test_automatic_employee_step_eligibility.py
tests/unit/test_automatic_employee_step_discovery_loop.py
tests/unit/test_automatic_employee_step_eligibility_wake.py
tests/unit/test_config.py
```

The focused assertions must prove:

- normal human Chat still uses typed observations, causal binding, exact `/new`, images, commands, and
  first-wins settlement;
- strict ordinary recovery resumes/reuses only, preserves partial visible output, returns before model
  completion, and rejects null/mismatch/not-found without a create or write;
- Employee recovery uses the stored `employee_session_id`, sends the exact recovery message once, does not
  build/replay the original prompt, and lets only proposal filing produce `awaiting_approval`;
- post-proposal stale worker turns settle without reprompting; repeated restart and duplicate same-process
  admission leave one owner;
- the complete eight-factor eligibility decision and final atomic claim are unchanged;
- shutdown-time interruption remains recoverable and every runtime/gateway/session layer receives one
  absolute deadline.

Run Ruff and mypy over all touched Python source plus the focused tests. Then run these two browser proofs:

```text
tests/e2e/test_live_chat_state.py::test_startup_recovery_preserves_partial_chat_and_continues_without_duplicate_input
tests/e2e/test_live_chat_state.py::test_ticket_panels_rows_do_not_fall_back_to_employee_session_history
```

## Static and repository scans

Before review, require all of the following:

- `git ls-files -u` is empty, `rg -n '^(<<<<<<<|=======|>>>>>>>)'` finds no conflict markers, and
  `git diff --check` is clean.
- A retired-runtime raw scan over live `src/planner` and current `docs/` finds no `TicketReadinessLoop`,
  `ticket_readiness`, `readiness_doorbell`, `ReadinessDoorbell`, or `runtime.readiness`. Tests use the
  existing AST/identifier and deletion assertions so their intentional rejected-name string fixtures remain
  valid evidence rather than false-positive live vocabulary.
- A retired-human-transport scan over Chat/adapters/shared-gateway code and their tests finds no
  `ChatStreamChunk`, `ChatHistory`, `ChatSendResult`, adapter `stream`, generic adapter `history`, deleted
  top-level human launchers, or `/api/chat/{entity_id}/history`.
- A Ticket-identity scan finds no `Ticket.chat_session_key`, `claimed.chat_session_key`,
  `tickets.chat_session_key`, `UPDATE tickets SET chat_session_key`,
  `SELECT chat_session_key FROM tickets`, `claim_running_step_chat_session_key`, old worker self-resolution
  route, or Ticket `chat_session_created` decoder. Day/top-level-agent `chat_session_key` and internal
  `chat_turns.session_key` remain valid.
- Existing deletion/AST tests still prove there is one Ticket `employee_session_id` writer, Panels Chat
  state never calls Hermes history, Employee history is Ticket-only, and only migration code can rewrite
  historical Ticket identity/events.
- A live-default scan plus the existing Worker-type boundary tests prove every production creation/import
  call supplies Worker type explicitly and no default/fallback/registry-order/Stage inference selects
  `coding`. Literal `worker_type="coding"` is allowed only as explicit fixture/product input and the terminal
  migration's historical rewrite.

After focused green and an independent read-only review reporting no violations, create the merge commit,
run canonical `PYTHONPATH="$PWD/src" ./verify`, fast-forward/integrate `main`, and run the canonical gate again
there as required by `main-integration.md`.
