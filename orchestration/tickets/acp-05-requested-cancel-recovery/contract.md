# ACP-05 requested-cancel runtime recovery

## Why this ticket exists

Real Hermes dogfood proved that successful ACP cancellation delivery does not by itself make the
current child reusable. Hermes accepts the notification, but its `session/prompt` RPC can unwind at
the client before the server-side prompt finalizer clears the adapter's private running state. A
follow-up Stop prompt was accepted by Panels and then stranded in Hermes's private queue. Send Now
was worse: the cancelled terminal continued, late old-generation text entered the successor turn,
and the visible answer became
`OLD TURN SHOULD NOT COMPLETE.SEND NOW REPLACEMENT READY.`.

Panels must not sleep until Hermes looks idle, inspect Hermes's private queue/state, or patch the
read-only Hermes checkout. The already-required exact-generation runtime boundary is the proof seam:
after this specific exceptional unwind, retire the indeterminate child and reload the same durable
ACP binding through a fresh child generation before publishing reusable idle or starting a successor.

## Frozen behavior

### Exact trigger and ownership

1. Recovery runs only when all of these are true: the active cause is user Stop or Send Now; exact
   ACP cancel delivery succeeded; permission cancellation succeeded; and the cancelled prompt await
   then raised or was locally cancelled instead of returning a terminal `PromptResponse`. A normal
   response with `stop_reason == "cancelled"` keeps the existing settlement path because the RPC has
   itself reached a terminal result.
2. An exception without a recorded requested cancellation remains an ordinary generation failure.
   Cancel-delivery failure, permission-cancellation failure, and the existing cancellation timeout
   remain generation-fatal. Steer never enters this path. New conversation and shutdown still own
   their existing close transitions; an exceptional cancelled child must be retired, but neither
   transition publishes a reusable same-session idle state.
3. The actor freezes the captured Send Now successor and all FIFO intent before recovery. It does not
   publish idle, advance the queue, or start any successor against the indeterminate child.
   For user Stop and Send Now only, the actor installs an exact-source hub quarantine before sending
   ACP cancel. Source updates admitted after that point are held, not rendered or collected. If the
   prompt returns a normal terminal `PromptResponse`, the hub releases those held updates in source
   order and the unchanged generation-N settlement path continues. If cancel delivery, permission
   cancellation, or the actor deadline fails, the quarantine fails with the generation; if the prompt
   unwinds exceptionally, the same quarantine becomes the recovery transition and its held old-source
   updates are discarded. There is no interval after cancel delivery in which late old output can
   publish before recovery begins.

### Same-binding fresh-child replacement

4. One actor-owned absolute deadline covers old-generation permission/terminal cleanup, exact lease
   retirement, fresh child spawn/initialize, private `session/load`, registry publication, broker
   handle replacement, browser transition, and final settlement. No owner creates a new budget or
   shields work past it.
5. The registry replacement names the exact leased child generation and record identity. It first
   removes that generation from accepted callbacks, closes/kills it within the deadline, and waits
   until its admitted ingress/death callback can no longer affect the current record. It then spawns
   one fresh child generation for the same employee/backend and privately loads the unchanged durable
   ACP session ID. The binding generation and durable Ticket/Chief mirror do not change.
   A matching planned-retirement death, including one carrying a close error, settles only that
   registry transaction and never enters the ordinary hub/broker child-death path. An unexpected
   exact-old-record death while the quarantine is open is consumed by the recovery failure owner:
   it fails the actor once and emits at most one visible connection error.
6. Private replay from the fresh child is captured through the ordered typed boundary and is not
   published into the old source epoch. Only after the exact durable binding is re-read and still
   matches may the registry publish the replacement handle. Late update, permission, terminal, prompt,
   or death callbacks from the old record identity are rejected and cannot fail or contaminate the
   replacement.
7. The hub serializes replacement with attach, new conversation, child death, compaction, and socket
   actions. Every attached browser receives one same-binding `reset`, captured replay in source order,
   `ready`, and the current queue snapshot before ordinary N+1-child publication resumes. This is a
   runtime-generation transition, not a new conversation: the ACP session ID and binding generation
   stay unchanged, and no new public wire type or visual treatment is added.

### Settlement after recovery

8. Stop settles the predecessor once as `interrupted`, publishes the replacement transition, then
   publishes idle and advances the FIFO once. A later ordinary prompt uses only the replacement handle
   and succeeds without entering a backend-private queue.
9. Send Now settles the predecessor once as `interrupted`, retargets the frozen successor prompt to
   the complete replacement handle/session, publishes the replacement transition, and starts that
   successor exactly once. No late old-generation output is rendered or collected into the successor.
10. A worker tracked completion remains pending through recovery and receives the same interruption
    result it would receive after a normal requested cancellation. Browser human echoes/receipts remain
    product-visible audit state; they are not replayed to the backend as hidden recovery prompts.
11. Failure or deadline expiry at any replacement stage rejects the Send Now successor and FIFO,
    settles tracked work exactly once, releases all hub/registry waiters, and publishes one visible
    connection failure. It never restores the indeterminate old child or claims reusable idle.

## Required proof

- Broker regressions model a Hermes-like child whose cancel succeeds and whose prompt raises while its
  old source can still emit. Stop must transition to a fresh child before idle, then deliver a later
  prompt exactly once. Send Now must retarget/start one successor after the transition and ignore late
  predecessor output. A held quarantine proves ACP cancel is not sent before suppression is active;
  the normal cancelled-response control proves held source is released in order on generation N.
- Registry tests prove exact-lease rejection, callback quiescence before replacement publication,
  same durable binding/session with child generation incremented, private ordered load/replay, late old
  ingress/death filtering, and deadline/failure cleanup with no leaked child or gate.
- Hub tests prove two attached browsers receive same-binding reset -> replay -> ready -> queue snapshot,
  attach/new-conversation serialization, no old-source envelope after quarantine begin, and one
  failure settlement when the production child-death path fires while that quarantine is open.
- New-conversation and shutdown controls prove an exceptional cancelled child is retired without
  publishing reusable idle or starting a same-session successor. Ordinary cancellation responses,
  cancellation failures/timeouts, child death, and Steer remain unchanged.
- A production-composition official-SDK e2e reproduces the real race without Hermes-private hooks: the
  old scripted process accepts cancel, lets the prompt RPC unwind, and continues attempting late output.
  Stop recovery plus a later prompt and Send Now recovery must each use a fresh process/generation and
  produce only their exact expected text. The fixture records an external audit only after its unique
  late update send attempt completes; the test asserts that audit exists while the marked text is absent
  from browser output and worker collection.
- Focused Ruff, strict Mypy, affected Python suites, and the ACP browser state/component suites pass.
  Do not run canonical `./verify`; ACP-10 owns the one final run.

## Allowed files

- `src/planner/conversation/runtime_ports.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/turn_broker.py`
- `src/planner/conversation/hub.py`
- `src/planner/conversation/composition.py`
- `src/planner/conversation/__init__.py` only if an internal port/type export changes
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_acp_conversation_composition.py`
- `tests/e2e/test_acp_conversation.py`
- `tests/support/acp_scripted_agent.py`
- this ticket's plan/report/review/evidence files
- `PROGRESS.md`
- `decisions.md`

No backend definition, SDK child protocol implementation, public wire schema, browser component,
shared visual asset, generated distribution, config, unrelated domain, runtime database, or
Hermes-checkout file is in scope. Implementation waits for the durable-compaction ticket to settle
these shared files; if its final API makes one named seam unnecessary or exposes a required production
file, amend this contract with the exact reason before source work.
