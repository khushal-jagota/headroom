# ACP-05 requested-cancel runtime recovery implementation plan

## Design summary

The exceptional requested-cancel path is a same-binding child-generation replacement, not a
compaction and not a new conversation.

The broker remains the sole turn/FIFO owner. When Stop or Send Now begins, the actor creates one
absolute deadline, freezes the active turn, Send Now successor, and FIFO, and installs a narrowly
named exact-source hub quarantine before it sends ACP cancel. Updates admitted after quarantine begin
are held outside the browser and worker collector. A normal terminal prompt response releases them in
source order and keeps generation N; a successful-cancel exceptional unwind promotes the same token
to recovery and discards them. The registry then retires the exact leased child and
privately loads the unchanged durable ACP session through a fresh child generation. The broker adopts
that complete handle, retargets all frozen runtime handles, and the hub publishes one same-binding
`reset -> captured replay -> ready -> queue snapshot` transition before the actor can publish idle or
start a successor.

The following settled compaction internals are reused only where their meanings remain exact:

- `ConversationRuntimeHandle` and `ConversationRuntimeLease` for complete runtime identity;
- the registry employee gate, `_spawn_initialized_child`, `_load_request`, and private
  `capture_load_session` boundary;
- `ConversationIngressSource` and record-identity filtering;
- the broker actor command queue, `_settle_generation_services`, queue ownership, and absolute-deadline
  wait pattern; and
- the hub employee sequencer and stream/source-key validation.

`PreparedCompactionCapture`, `CompactionCaptureTransition`, `CompactionTransitionToken`,
`begin_compaction_transition`, actor rekey, session fork, binding CAS, binding-generation increment,
and compaction replay/echo rules are not generalized or renamed. Cancellation recovery gets exact
types and methods because its durable binding and browser sequence rules are different.

No public wire type, backend definition, frontend component, durable binding write, or hidden recovery
prompt is introduced.

## Phase 1 — freeze exact internal contracts and make the current wrong tests fail

Add the following internal values and port to `runtime_ports.py`:

- `RequestedCancelRuntimeReplacement`, containing only `replacement_handle` and the ordered private
  `replay` tuple;
- `RequestedCancelRecoveryTransitionToken`, containing an opaque `transaction_identity`, the complete
  `original_handle`, and the actor-owned absolute `deadline`; and
- `ConversationRequestedCancelRecoveryTransitionPort`, with exact methods
  `begin_requested_cancel_recovery_transition(handle, deadline)`,
  `resume_requested_cancelled_runtime(token)`,
  `commit_requested_cancel_recovery_transition(token, replacement_handle, replay, queued_prompts)`,
  and `fail_requested_cancel_recovery_transition(token, reason)`.

“Begin” is a precautionary quarantine, not a claim that recovery is already required. Its token is
stored on the active prompt before cancel is sent. `resume_requested_cancelled_runtime` flushes the
held exact-source payloads through ordinary ingress in source order and settles the token only for a
normal terminal prompt response. Commit discards held generation-N payloads because replay from the
fresh generation replaces them; failure discards them and leaves the stream non-ready.

Extend `ConversationEmployeeRuntimePort` with
`replace_runtime_after_requested_cancel(lease, deadline) -> RequestedCancelRuntimeReplacement`.
The method name must keep the trigger in the contract; it is not a generic respawn or transition API.
Export the values from `conversation/__init__.py` only if an existing internal import convention
requires it.

In `test_conversation_turn_broker.py`, replace the now-invalid expectations in
`test_user_cancelled_prompt_exception_settles_as_interrupted_and_keeps_child_usable` and
`test_send_now_delivers_one_successor_when_cancelled_prompt_raises`. Give the broker fake runtime an
exact N+1 handle and an implementation of `replace_runtime_after_requested_cancel`; give the fake
transition port begin/resume/commit/fail recording. The first red tests must assert:

- ACP cancel is not invoked until the begin/quarantine call has completed;
- an exceptional Stop cannot publish idle before the replacement commit;
- an exceptional Send Now cannot invoke the successor on generation N;
- the durable ACP session ID and binding generation remain unchanged; and
- child generation, child object, and record identity all change.

Keep the existing normal-response tests as controls: a requested cancellation returning
`PromptResponse(stop_reason="cancelled")` resumes held source in order and continues on generation N
without a reset. Also retain
`test_uncancelled_prompt_exception_still_fails_generation`, cancellation delivery/permission failure,
timeout, Steer, and normal Send Now coverage unchanged.

### Checkpoint 1

The new contract types type-check, the two exceptional-cancel broker tests fail for the current
generation-N reuse, and the normal `stop_reason="cancelled"` controls remain green. No registry or hub
implementation exists yet.

## Phase 2 — replace one exact registry lease with a private same-binding load

Implement `AcpEmployeeRegistry.replace_runtime_after_requested_cancel` as one registry-owned operation
under the supplied deadline:

1. Acquire the existing employee gate with only
   `max(0, deadline - loop.time())` remaining, then revalidate employee, binding, child generation,
   child object, record identity, liveness, and registry-open state from the supplied lease.
2. Install an exact planned retirement keyed by
   `(employee_id, child_generation, id(record_identity))`, remove generation N from
   `_accepted_callback_generations`, and keep the employee transition reserved.
3. Close generation N and await its matching planned-death settlement before spawning N+1. Refine
   `_spawn_initialized_child`'s `guarded_death` only as needed so a matching `_PlannedRetirement` can
   settle under the registry lock without waiting on the employee gate already owned by the retirement
   transaction. Unplanned death retains the existing employee-gated source-aware callback path.
   A matching planned close never calls the hub death callback, whether it reports `None` or an error:
   success resolves its settlement, error rejects its settlement, and the replacement owner alone
   fails the open recovery token. This avoids ordinary child-death publication for expected retirement.
4. Allocate exactly one fresh child generation for the same employee/backend, call the existing
   `_spawn_initialized_child`, and call `capture_load_session` with the existing `_load_request` for
   the unchanged `binding.acp_session_id`. Collect only ordered
   `SessionNotification | ProtocolUpdateRejectedPayload` values in the private callback. Do not call
   ordinary `load_session` and do not publish this replay through generation N.
5. Re-read the durable binding before publication. It must equal the complete original binding,
   including the same ACP session ID and binding generation. A missing or changed durable row is a
   replacement failure, not a CAS/adoption case.
6. While the employee reservation still holds, publish one `AcpEmployeeRecord` for generation N+1,
   replace accepted callbacks with exactly `{N+1}`, and return
   `RequestedCancelRuntimeReplacement(_runtime_handle(record), tuple(captured))`.

Extract an exact `_publish_runtime_record_under_employee_gate` helper only if needed to avoid trying to
reacquire the already-owned gate. Do not route this through `_publish`,
`_adopt_compaction_winner_under_reservation`, or any compaction prepared state: those methods have
different binding/CAS rules.

Every failure/cancellation path removes the planned retirement, invalidates generation N so
`get_or_spawn` cannot return it, removes an unpublished candidate's accepted generation and record
identity, initiates candidate close, and releases the employee gate in `finally`. Blocking close,
initialize, private load, durable resolve, death settlement, and cleanup use the original deadline;
none creates a grace period. If the budget is exhausted, detach result consumption/close work rather
than await past the deadline, but leave no live registry record or accepted callback gate for either
child.

Add registry tests with these exact responsibilities:

- `test_requested_cancel_recovery_retires_exact_lease_and_privately_loads_same_binding_fresh` proves
  N is closed/quiesced before N+1 publication, the child generation increments, binding/session stay
  exact, ordinary ingress remains empty, and captured replay is ordered.
- `test_requested_cancel_recovery_rejects_stale_lease_without_retiring_current_record` covers wrong
  generation and wrong record identity.
- `test_requested_cancel_recovery_waits_for_admitted_old_ingress_and_filters_late_ingress_and_death`
  holds an old ingress callback at the employee gate, proves publication waits, then proves old update,
  permission, and death callbacks cannot reach or retire N+1.
- `test_requested_cancel_planned_retirement_error_stays_inside_replacement_transaction` proves a
  non-null planned-close error rejects replacement without invoking the source-aware death callback.
- `test_requested_cancel_recovery_rereads_unchanged_durable_binding_before_publication` holds the
  durable read and checks that no replacement handle is resolvable early.
- Parameterized private-load, fresh-child death/initialize, durable-binding drift, old-close error,
  and deadline tests prove no current record, accepted generation, planned retirement, child, or
  employee gate leaks. A later `get_or_spawn` may recover the durable binding only through another
  fresh generation.

### Checkpoint 2

The focused registry suite proves exact retirement, callback quiescence, same-binding private replay,
N+1 child publication, and hard failure cleanup. Existing fork/CAS compaction tests remain green and
unchanged in meaning.

## Phase 3 — add a same-binding hub barrier and browser epoch transition

In `hub.py`, add `_RequestedCancelRecoveryTransitionState` and a separate
`_requested_cancel_recovery_transitions` map. Do not reuse `_CompactionTransitionState` or rename the
compaction map.

Implement the four `ConversationRequestedCancelRecoveryTransitionPort` methods:

### Begin

`begin_requested_cancel_recovery_transition` runs as one employee-sequencer operation. It rejects an
existing requested-cancel recovery or compaction transition, validates the complete original runtime
handle against `_runtime_handle_for_stream`, rejects an expired deadline, and installs a one-shot
timeout at that exact deadline. The actor awaits this begin before invoking ACP cancel. From this
point, `_ingest_source` appends the transition's exact old-source payload to one bounded private deque
instead of publishing, recording prompt settlement, or collecting worker text. Overflow fails the
transition and generation; it never drops one payload and continues. Begin does not emit `reset`, does
not change the durable binding, and does not claim ready.

### Resume normal terminal cancellation

`resume_requested_cancelled_runtime` validates the exact token and original stream, drains the held
deque through `_publish_source_payload` in source order, then settles/releases the transition. The
broker calls it only after `task.result()` returns a terminal `PromptResponse`; it runs before the
existing prompt-settlement barrier closes so released payloads keep their ordinary notification and
rejection semantics. There is no reset and generation N remains current.

### Commit

`commit_requested_cancel_recovery_transition` also runs as one sequencer operation. It validates:

- the exact token/state and unexpired original deadline;
- the old stream still names the token's complete original handle;
- replacement employee, backend, and complete binding equal the original;
- replacement child generation is greater than the original and child/record identity are different;
  and
- every queued prompt still names the unchanged durable ACP session.

Replace the stream while retaining both attached browser subscriptions and the previous same-binding
sequence as its floor. Then enqueue, without an await or interleaving action:

1. one same-binding `connection(reset)` (detail `Conversation runtime recovered`);
2. each privately captured replay item through `_publish_source_payload` in tuple order;
3. one `connection(ready)`;
4. one queue snapshot for the still-frozen FIFO.

Do not emit queue human echoes during recovery: the browser already received the original product
audit echo, and no backend recovery prompt exists. Settle the transition and release waiters only
after the whole sequence is enqueued. Old-source ingress and old-source death remain ignored after
commit because their complete source keys no longer match the stream. The pre-cancel held generation-N
deque is discarded rather than replayed or collected.

### Failure/expiry

`fail_requested_cancel_recovery_transition` is one-settlement/idempotent for its exact token. It makes
the old stream non-ready with no runtime handle, rejects the old source, emits exactly one
`connection(error, "Employee connection failed")`, records the reason, cancels the timeout callback,
and releases every waiter. Deadline expiry calls that same exact failure settlement synchronously;
the actor's later failure call must not emit a second connection error.

Refactor `registry_child_died` so the employee sequencer first classifies the complete source identity.
If it is the exact old source of an open requested-cancel token, the hub performs the same one-shot
transition failure instead of `_publish_child_death`, then invokes `broker.child_died` only to settle
the matching actor/generation. An ordinary source keeps the current publish + broker callback. A stale
post-commit source matches neither current stream nor open token and is ignored. Thus an unexpected
quarantined-child death produces one actor failure and at most one connection error.

Add explicitly named waiting/serialization to `ensure_employee_stream`, `ensure_stream_ready`,
`attach_browser`, `dispatch_action`, and `new_conversation`. A helper may be called
`_wait_for_compaction_and_requested_cancel_recovery`, but must keep both named states and preserve the
existing compaction return used for old-session prompt retargeting. A same-binding recovery needs no
prompt-session rewrite. Both begin methods cross-reject the other transition so attach, new
conversation, child death, socket actions, and compaction cannot enter the retirement/publication gap.
`close_admission` and `shutdown` fail and wake requested-cancel transitions alongside, but separately
from, compaction transitions.

Add hub tests:

- `test_requested_cancel_recovery_rebinds_two_browsers_same_binding_with_ordered_replay_and_queue`
  checks both browsers receive identical increasing same-binding sequences and exactly
  `reset -> replay -> ready -> queue_snapshot`.
- `test_requested_cancel_recovery_suppresses_old_source_before_and_after_reset` admits late old update
  after begin and on both sides of commit; it proves held text is neither rendered nor collected and
  no old-source envelope follows reset.
- `test_requested_cancel_quarantine_resumes_held_source_for_normal_terminal_response` proves ordered
  release through ordinary publication, collector, and prompt-ingress behavior with no reset.
- `test_requested_cancel_recovery_owns_unexpected_old_child_death_once` drives the production
  `registry_child_died` entry while begin is held open and asserts one actor settlement, one transition
  failure, and at most one visible connection error.
- `test_requested_cancel_recovery_serializes_attach_action_new_conversation_and_compaction` proves all
  wait before commit, then re-resolve against N+1; a prompt keeps the same session ID and is delivered
  once.
- `test_requested_cancel_recovery_failure_and_expiry_release_all_waiters_with_one_connection_error`
  proves the one visible failure and no transition/source-capture leak.

### Checkpoint 3

Hub-only tests prove same-binding sequence continuity, two-browser ordering, source suppression,
cross-transition serialization, and one failure wake path. The existing N→N+1 compaction hub tests
continue to use their original methods and new-binding sequence semantics.

## Phase 4 — make the broker actor own recovery and settlement

Add a broker binding method
`set_requested_cancel_recovery_transition_port(port)` with the same pre-actor bind-once rule as
`set_compaction_transition_port`. Carry that exact port plus an injected
`requested_cancel_recovery_timeout_seconds` on `_Actor`; production defaults to
`ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS`. This is an internal timeout parameter for focused
tests, not a new config field. Keep `capture_timeout_seconds` and all compaction names unchanged.

Refactor `_settle_prompt` to distinguish result shape before normal cancellation settlement:

- `task.result()` returns a `PromptResponse`: resume the exact quarantine before closing the existing
  prompt-ingress barrier, then retain the existing path, including `stop_reason="cancelled"` on N.
- `task.result()` raises `CancelledError` or another exception after successful cancellation and
  permission-cancellation tasks, with cause `user` or `send_now`: call a new exact
  `_recover_after_requested_cancel_prompt_exception(active)` path.
- the same exception with `new_conversation` or `shutdown`: record that exact lease for close-owned
  retirement under the already-supplied close deadline; never enter same-session recovery.
- no requested cause, `child_failure`, failed cancel delivery, failed permission cancellation,
  cancellation timeout, and Steer retain their existing failure/settlement paths.

For user Stop and Send Now, `_begin_cancel` creates one absolute deadline, awaits
`begin_requested_cancel_recovery_transition`, stores its token/deadline on `_ActivePrompt`, and only
then starts permission cancellation and calls `lease.cancel`. The existing shorter
`cancel_timeout_seconds` remains ACP-02's fail-fast detector for cancel-delivery settlement; it is
not a second requested-cancel recovery budget. If that detector wins, the actor fails the stored
quarantine token and generation through the existing cancel-timeout path and never enters fresh-child
recovery. Once exact cancel and permission cancellation have succeeded and the prompt then unwinds
exceptionally, every recovery operation reuses only the stored longer deadline; no recovery owner
starts or extends another budget. Cancel-delivery or permission-cancellation failure, timeout, and
actor failure fail the stored token once before or with generation failure. New conversation and
shutdown do not install this reusable-session quarantine.

`_recover_after_requested_cancel_prompt_exception` runs in the actor command and performs this order:

1. Cancel the old cancellation-timeout task, publish the predecessor's `interrupted` receipt exactly
   once, but leave its tracked completion pending and keep `active` installed so attach state remains
   `cancelling`.
2. Reuse the exact stored token and actor deadline that were installed before ACP cancel; do not create
   a new budget or call begin again. All post-cancel generation-N source is already quarantined.
3. Call `_settle_generation_services("Prompt was cancelled", deadline)` so old-generation permission
   and terminal owners are completely settled inside the same budget.
4. Call `runtime.replace_runtime_after_requested_cancel(active.lease, deadline)` and validate its
   replacement has the same employee and complete binding, a greater child generation, and different
   child/record identity.
5. Replace every `queued_runtime_handles` value with the complete replacement handle. Queue prompt
   content/session/order/client IDs/enqueue metadata remain unchanged. If `active.successor` exists,
   construct one replacement `_QueuedSubmission` preserving client ID, choice, prompt, origin,
   completion future, and hooks while changing only its runtime handle.
6. Assign `actor.handle = replacement_handle`; actor rekey is deliberately not called because the
   broker key `(employee_id, binding_generation)` is unchanged.
7. Commit the hub transition with captured replay and the exact frozen FIFO. Only after commit returns,
   clear `active` and settle the predecessor's tracked completion as `interrupted`.

Successful Stop then fails any pending compaction boundaries as interrupted, publishes
`activity(interrupted)`, publishes `activity(idle)`, and calls `_advance_queue` exactly once. Successful
Send Now does not publish an intervening idle: it calls `_start` exactly once with the retargeted
successor, then the ordinary successor lifecycle advances the preserved FIFO. No old-generation
notification can enter the successor collector because the hub suppressed the old source before the
registry replacement and the successor's `TrackedTurnHandle` names N+1.

On any cleanup, registry, validation, or hub-commit failure/deadline:

- invalidate whichever exact old/replacement runtime can still be current;
- leave actor lifecycle `failed`, clear `active`, fail compaction boundaries, and never call
  `_advance_queue`;
- settle the predecessor tracked work once as errored, reject the Send Now successor once, reject the
  FIFO in order, and publish the empty queue snapshot;
- fail the hub requested-cancel transition once (expiry may already own it); and
- do not publish idle or attempt to restore generation N.

Add broker tests for exact order and ownership:

- Rewrite the two Phase-1 red tests as
  `test_stop_prompt_exception_recovers_fresh_child_before_idle_and_later_prompt` and
  `test_send_now_prompt_exception_retargets_fresh_child_and_starts_successor_once`.
- In both, hold registry replacement and hub commit separately to prove no idle/successor/FIFO advance
  crosses either gate; hold quarantine begin to prove cancel is not sent early, then emit a unique
  generation-N notification after cancel acceptance and assert it is absent from browser/worker text.
- Add `test_requested_cancel_recovery_retargets_fifo_handles_and_advances_once_after_stop` and a Send
  Now variant preserving FIFO order after the successor.
- Add a tracked-worker Stop case proving completion remains pending through registry load and hub
  commit, then resolves to the same `TrackedTurnResult("interrupted")` as a normal requested cancel.
- Parameterize reverse-service cleanup failure, stale/invalid replacement, private-load failure, hub
  commit failure, and deadline expiry. Assert one predecessor settlement, one successor/FIFO rejection,
  one transition failure, no idle, and no owned recovery task.
- Add an unexpected old-child-death case through the hub-owned production callback disposition; assert
  one actor failure, one successor/FIFO rejection, and at most one connection error.
- Keep explicit controls for ordinary cancellation response, unrequested exception, cancel delivery
  failure, permission-cancel failure, timeout, child death, and Steer.

### Checkpoint 4

Broker plus registry/hub fakes prove Stop and Send Now never reuse N after exceptional unwind, all
intent remains frozen until reset/replay/ready, and success/failure settle every receipt, worker, queue,
and deadline exactly once.

## Phase 5 — preserve new-conversation and shutdown ownership

Extend `_ActivePrompt` with the close deadline only when `_begin_cancel` is called for
`new_conversation` or `shutdown`, or store an exact close-retirement lease on the actor when that
prompt ends exceptionally. `_close_actor_steps` remains the owner of the caller-supplied absolute
deadline.

For an exceptional prompt unwind after either close cause:

- publish/settle the existing close interruption once;
- retire the exact lease with `runtime.retire_runtime_lease(lease, deadline)` before the broker close
  returns;
- do not call requested-cancel recovery begin/commit;
- do not publish same-session idle, reset, or start a successor/FIFO; and
- let `hub.new_conversation` continue with `registry.new_conversation` only after exact retirement,
  causing registry demand to use a fresh child before it creates the new durable session. Shutdown
  proceeds to registry shutdown after the same exact retirement attempt.

If close retirement cannot finish by the existing close deadline, the close remains failed/timeout and
force-disposes the actor; it never marks the indeterminate record reusable. A normal terminal
`PromptResponse`, including `stop_reason="cancelled"`, keeps the existing close behavior.

Add parameterized broker controls for `new_conversation` and `shutdown` where cancel succeeds and the
prompt raises. Assert one exact retirement, no recovery-port call, no idle/reset/successor, and close
completion/failure bounded by the caller's original deadline. Retain
`test_late_generation_callbacks_after_actor_exit_settle_without_hanging` as the post-close callback
control.

### Checkpoint 5

Close controls prove exceptional children are retired without turning new conversation or shutdown
into a reusable same-session recovery.

## Phase 6 — bind production ownership and prove the official-SDK race

In `composition.py`, bind the hub through
`broker.set_requested_cancel_recovery_transition_port(hub)` before any actor can be created, alongside
the existing separately named compaction port. Add a composition unit assertion that both ports are
the single production `ConversationHub`. Existing close-admission ordering must wake both kinds of hub
transition before broker/registry shutdown drains them under the service deadline.

Extend only `tests/support/acp_scripted_agent.py` with a `requested_cancel_unwind` prompt script using
the official SDK surface:

- emit/audit the normal user prompt and wait for the existing public ACP cancel notification, which
  production cannot send until the hub quarantine begin has returned;
- after cancel, synchronously attempt one `client.session_update` carrying a unique old-generation
  `AgentMessageChunk` without adding it to durable fixture history, then append a separate external
  audit record only after that send attempt returns;
- raise a scripted exception from `prompt` so the official `session/prompt` RPC unwinds at the client
  while the process is still able to attempt the late update; and
- keep ordinary/default prompts unchanged so the fresh process can load the same durable session and
  return one exact expected answer.

This is a protocol-level fixture, not a Hermes-private hook. It must not inspect a backend queue,
running flag, or repository internals. The e2e must assert the late-send audit exists; absence of the
marked text alone is insufficient. Use existing prompt audit evidence and the composed registry
handle solely to assert that the second prompt reached a different child object/generation while using
the same ACP session/binding.

Add one production-composition e2e,
`test_official_requested_cancel_exception_recovers_same_session_for_stop_and_send_now`, with separate
fresh Ticket conversations for Stop and Send Now:

### Stop scenario

1. Attach two WebSocket browsers and capture binding N/session S and child generation G.
2. Start `requested_cancel_unwind`, wait for `thinking`, send browser Stop, and collect through idle.
3. Assert both browsers receive same-binding/generation-N
   `interrupted receipt -> reset -> replay -> ready -> queue snapshot -> interrupted -> idle` in the
   contract-owned relative order, with no unique late-old text.
4. Resolve the runtime and assert binding/session still N/S, child generation is G+1, and child object
   and record identity changed.
5. Send one ordinary follow-up and assert its audit/session and visible answer occur exactly once on
   the replacement.

### Send Now scenario

1. Start a fresh conversation/session, run `requested_cancel_unwind`, enqueue one FIFO item, then send
   one Send Now replacement.
2. Assert the predecessor is interrupted once; same-binding reset/replay/ready and queue snapshot
   occur before the Send Now `started` receipt; the Send Now answer contains only its exact expected
   text; and the FIFO starts once after it.
3. Assert the prompt audit has predecessor, Send Now, and FIFO exactly once with the latter two on the
   unchanged session through the fresh child generation. The unique late-old text never appears.

Add failure fixtures only in unit/composition tests; the official e2e should exercise the successful
real race without timing sleeps or Hermes state inspection.

### Checkpoint 6

The production composition and official SDK subprocess prove the dogfood failure shape, fresh process
generation, same durable binding/session, old-source exclusion, Stop reuse, and Send Now exact-once
successor/FIFO behavior.

## Phase 7 — focused browser and regression proof

No frontend source changes are expected. Run the existing browser state/component/contract suites to
prove a same-binding reset clears the old rendered epoch, replay rebuilds it, queue state remains
exact, and no new visual treatment was added:

```sh
cd web && node tests/acp-browser-state.test.mjs
cd web && node tests/acp-browser-components.test.mjs
cd web && node tests/acp-browser-conformance.test.mjs
cd web && node tests/acp-contracts.test.mjs
cd web && npm run check
```

If an existing state test lacks same-binding reset coverage, extend only the allowed ACP browser test
file if the contract is first amended; frontend files are currently outside this ticket and must not be
silently added.

### Checkpoint 7

All ACP browser suites remain green without a component, wire-schema, or design change.

## Phase 8 — focused evidence and handoff

After the implementation and tests settle, run these focused commands once and record full output in
the ticket evidence/report:

```sh
.venv/bin/ruff check \
  src/planner/conversation/runtime_ports.py \
  src/planner/conversation/employee_registry.py \
  src/planner/conversation/turn_broker.py \
  src/planner/conversation/hub.py \
  src/planner/conversation/composition.py \
  src/planner/conversation/__init__.py \
  tests/unit/test_acp_employee_registry.py \
  tests/unit/test_conversation_turn_broker.py \
  tests/unit/test_conversation_hub.py \
  tests/unit/test_acp_conversation_composition.py \
  tests/e2e/test_acp_conversation.py \
  tests/support/acp_scripted_agent.py

.venv/bin/mypy --strict \
  src/planner/conversation/runtime_ports.py \
  src/planner/conversation/employee_registry.py \
  src/planner/conversation/turn_broker.py \
  src/planner/conversation/hub.py \
  src/planner/conversation/composition.py

.venv/bin/pytest -q \
  tests/unit/test_acp_employee_registry.py \
  tests/unit/test_conversation_turn_broker.py \
  tests/unit/test_conversation_hub.py \
  tests/unit/test_acp_conversation_composition.py \
  tests/e2e/test_acp_conversation.py
```

Then run the Phase-7 browser commands and inspect the allowed-file diff. Do not run canonical
`./verify`; ACP-10 owns the one final run.

The implementation report must include phase checkpoint results and explicitly state:

- the replacement keeps ACP session ID and binding generation exact;
- child generation/child/record identity all change;
- one actor deadline reached every blocking owner;
- no old-source payload was published or collected after recovery begin;
- Stop and Send Now each settled predecessor/successor/FIFO exactly once;
- new conversation/shutdown retired exceptional children without reusable idle; and
- compaction fork/CAS/actor-rekey behavior and normal cancellation-response behavior were unchanged.

## Contract/API blocker audit

No contract amendment or additional production file is currently required. The settled child factory,
private load boundary, source-aware callbacks, broker actor, hub sequencer, reverse-service cleanup,
and composition binding provide all required seams. If implementation discovers that the official SDK
child cannot close and deliver its death callback within the shared deadline, or that the browser needs
a new public envelope to distinguish this same-binding reset, stop before inventing a fallback and
amend the contract with that exact blocker; neither issue is evidenced by the current source/tests.
