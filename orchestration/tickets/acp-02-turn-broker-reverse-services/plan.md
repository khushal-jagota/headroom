# ACP-02 implementation plan

## Boundary, prerequisites, and settled decisions

This slice adds uncomposed conversation-domain services only. It does not add a route, websocket,
database writer, product runtime composition, Svelte code, legacy cutover, or backend registration.
ACP-00/00a/00b and the reviewed ACP-01 implementation must be integrated before implementation
starts. In particular, implementation begins against ACP-01's settled employee record, child
generation guard, binding generation, ordered ingress, and shutdown behavior rather than creating
another child/session owner.

The worker-delivery boundary is absolute: the only prompt delivery operations in this slice call the
live ACP child's exact `PromptRequest` through the typed runtime port. A delivery receipt, activity,
queue snapshot, Chat row, event-log row, or product-visible transcript row is evidence/UI state only.
No generic service imports `planner.chat`, writes a Chat table, or treats a published event as agent
context.

The existing frozen Panels contracts remain unchanged. ACP request, response, content, permission,
filesystem, and terminal values always use the installed official `acp.schema` models. Internal
ownership records may be frozen dataclasses, but they must not copy an ACP payload shape. The Hermes
checkout remains read-only; neither implementation nor tests write there or run git there.

Add these purpose-owned modules:

```text
src/planner/conversation/
  runtime_event_publisher.py     # one named typed publication protocol
  runtime_ports.py               # narrow ACP-01 child/registry and capture seams
  turn_broker.py                 # per-binding actor, active epoch, FIFO, cancel policy
  permission_broker.py           # browser attachments and exactly-once permission settlement
  hermes_turn_strategy.py        # the only Hermes steer/provenance/summary behavior
  reverse_services/
    __init__.py
    path_confinement.py          # shared root/cwd/path resolution rule
    filesystem.py                # exact fs/read_text_file and fs/write_text_file
    terminal.py                  # exact terminal lifecycle and display snapshots
```

Modify only `configuration.py`, `ordered_ingress.py`, `sdk_child.py`, `employee_registry.py`, and
`__init__.py` among the existing production modules. `employee_registry.py` receives the explicit
exact-record lease, planned-retirement, capture-replacement, and source-aware permission operations
described below; its durable binding algorithm and public ACP-01 lifecycle behavior remain unchanged.
`contracts.py`, `backend_contracts.py`, and `wire_contracts.py` remain frozen.

Add named defaults to `configuration.py`; every constructor accepts a smaller injected value for
tests:

```text
ACP_ACTIVE_PROMPT_CANCEL_TIMEOUT_SECONDS = 10
ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS = 10
ACP_REVERSE_TERMINAL_OUTPUT_MAX_BYTES = 1_048_576
ACP_REVERSE_TERMINAL_CLEANUP_TIMEOUT_SECONDS = 5
```

The existing `CONVERSATION_PERMISSION_RESPONSE_TIMEOUT_SECONDS = 300` remains the sole permission
default. Public cleanup methods take one absolute monotonic deadline; the configured shutdown value
is what later composition uses to create it. No child, terminal, or record receives a fresh budget
after the shared deadline has started.

## 1. Runtime ports and the sole event publisher

### ACP-01 runtime port

`runtime_ports.py` defines one immutable `ConversationRuntimeHandle` containing the exact
`ConversationEmployee`, exact `ConversationSessionBinding`, positive child generation, frozen
`AcpEmployeeChild`, selected `AgentBackendDefinition`, and an opaque record-identity token. It also
defines a short-lived `ConversationRuntimeLease` bound to that complete handle identity. It defines one
`ConversationEmployeeRuntimePort` with only the operations ACP-02 needs:

- resolve the current matching handle for employee + binding generation;
- acquire a lease only while employee, complete binding, child generation, record identity, and
  child aliveness still match, and execute prompt/cancel/strategy work only through that lease;
- retire and await teardown of one exact leased record after an indeterminate cancel, within the
  caller's one absolute deadline;
- replace one idle leased child for compaction capture: quiesce and retire exact generation N,
  spawn/initialize N+1 for the unchanged durable binding, privately load it, publish N+1 only after
  the capture barrier, and return the new handle plus exact `LoadSessionResponse`.

ACP-02 adds explicit internal methods to `AcpEmployeeRegistry`; an outside adapter never reaches into
its private record table. Each method acquires the employee publication/update gate, checks the
complete record identity under the short registry lock, releases the global lock, and retains the
exact child reference in the lease before releasing both locks. Prompt/cancel/strategy execution uses
that captured child outside the locks and never re-resolves "current"; if N+1 wins meanwhile, closing
N makes the operation fail rather than redirect. Generation transitions use the employee gate only
for their short state marks and final publication/quiescence; no injected await occurs under the
global lock and the update gate is never held across `child.prompt`, which would deadlock typed ingress.
Retirement removes only the matching record, closes it outside the global lock, and cannot retire
N+1. Capture replacement uses the existing spawn/initialize/load/publication machinery, keeps binding
ID/generation unchanged, and preserves the N-sink-before-N+1-publication guarantee. A stale
handle/lease fails; it is never silently redirected to the current child.

Intentional capture retirement has an exact `(employee_id, binding_generation, child_generation,
record_identity, capture_transaction_id)` planned-retirement token installed under the employee gate
before N is closed. `guarded_death` consumes that token only for the matching intentional-close
callback (`error is None`), retires the exact record, and settles the capture operation's private
retirement future without invoking the ordinary unexpected-child-death callback. A non-null error,
mismatched record/generation/token, or death before token installation remains unexpected and runs
the normal child-failure path. Intentional teardown of an unpublished N+1 capture candidate uses the
same private token. The capture actor awaits N's planned-retirement settlement before spawning N+1;
the callback may arrive first without failing boundaries or the actor.

`GenerationBoundBackendTurnStrategy` is an internal proxy implementing the already-frozen
`BackendTurnStrategy` signature. The broker obtains it from an exact runtime lease before scheduling
Steer. Its injected concurrent-prompt port is closed over that lease and rejects unless the complete
record still matches; `HermesAcpTurnStrategy` therefore cannot resolve "current by binding" after a
crash. The public strategy contract is unchanged. The turn broker does not call the concrete SDK
connection, load a different binding, or mutate the durable binding.

`runtime_ports.py` also defines narrow exact-model protocols used by the SDK bridge:

- filesystem methods accept `ReadTextFileRequest` / `WriteTextFileRequest` and return the matching
  official response;
- terminal methods accept the six official request models and return the matching official
  responses;
- the generation-bound concurrent prompt port accepts the exact lease, binding, client-message ID,
  and `PromptRequest` for a backend strategy.

These are routing/ownership ports, not alternative protocol models.

### Event publisher

`runtime_event_publisher.py` defines the single injected `ConversationRuntimeEventPublisher`.
It has named methods for:

- activity state/detail;
- exact `TurnDeliveryReceipt`;
- an ordered tuple of exact `QueuedPrompt` values;
- exact `ContextCompaction`;
- permission-request inputs and exact permission-outcome inputs;
- exact `ConversationTerminalState`.

Every call includes the exact employee and session binding. The publisher alone allocates the outer
envelope sequence, `ConversationActivity.sequence`,
`ConversationPermissionRequest.opened_sequence`, and
`ConversationPermissionOutcome.settled_sequence`; its activity and permission methods return the
exact frozen value they created so the service can retain it. There is no generic `publish(kind,
data)` method and no route, websocket, Chat, or transcript vocabulary in this protocol.

All services await a publication before initiating the next externally visible transition. A
recording publisher in tests allocates monotonically increasing values and records a single total
order. Publisher failure is generation-fatal for the owning service: stop new delivery, do not start
another queued prompt, and use the same child-failure cleanup path rather than continuing with UI
state that did not publish.

## 2. Extend ordered ingress with response-consumption and private capture barriers

The installed SDK dispatches typed client callbacks as tasks, so a `session/prompt` response can be
resolved before a preceding provenance callback has passed through ACP-01's serial downstream sink.
Without a barrier, the broker could mark the owning prompt settled and start a queued prompt before
seeing its automatic-compaction notification. Reuse ACP-01's observer ordinal mechanism; do not add
a sleep, quiet period, raw JSON reducer, or second transport.

Refactor `ordered_ingress.py` from its single load epoch into named request-consumption epochs:

1. `begin_response_consumption_epoch(method, private_ingress=None)` returns a private epoch token.
   `session/load` remains serialized; multiple ordinary `session/prompt` epochs may be in flight so
   a declared native steer can run concurrently.
2. The synchronous observer associates each outgoing request of that method with the earliest
   unassigned token and its JSON-RPC request ID. Every incoming `session/update` still reserves one
   wire-order slot and is fulfilled only by the exact typed SDK callback, or by the existing frozen
   protocol rejection for an invalid SDK model.
3. The matching response freezes that epoch's target ordinal. The public request returns only after
   the SDK response and every slot through that target has completed its selected downstream sink.
   Failure, child death, overflow, and close wake all epoch waiters.
4. Ordinary prompt/load epochs use the existing ordinary downstream. A controlled capture child is
   born with a private ingress, and every slot through its one outgoing load response target goes only
   to that collector. A pre-load slot is an ambiguous capture failure. After the target is consumed,
   the ingress atomically switches to the ordinary sink before N+1 publication; later notifications
   are ordinary live traffic.
5. Capture is permitted only after the owning prompt response-consumption barrier, while the actor
   owns no running/cancelling/strategy task and admission is closed. Permission requests and live
   terminals for N are settled through their owners, then the registry quiesces and retires N before
   spawning N+1. Thus a delayed ordinary update from N either enters the ordinary sink before the
   employee gate transition or fails the stale-generation check; it can never enter N+1's private
   collector. N+1 has performed no operation except its private `session/load`, so its capture window
   contains only that replay. This is the capture-isolation barrier; there is no elapsed-time or quiet-
   period heuristic.

Add `SdkAcpEmployeeChild.capture_load_session(request, private_ingress)` as an extra concrete method;
do not change the frozen `AcpEmployeeChild` protocol. Its implementation is allowed only on the fresh
unpublished capture child, uses the same `_load_lock` and exact `LoadSessionRequest` delegation as
`load_session`, and switches from the private collector to ordinary ingress only after the response
target is consumed. The registry exposes it only through the generation-replacement operation above.
Capture failure closes N+1 and leaves no published live record; the next explicit demand owns normal
ACP-01 recovery. No captured replay prefix is rebroadcast.

Wrap `SdkAcpEmployeeChild.prompt` in an ordinary prompt response-consumption epoch. This keeps valid
thought/tool/provenance typing, ensures every update before the response is published/observed before
the response settles the turn, and still permits concurrent prompts. Tests pause the typed sink after
the raw response and prove both main prompt and steer calls remain pending until the ordinal is
consumed.

## 3. Turn broker actor and exact active-turn state machine

`turn_broker.py` owns one async actor record keyed by `(employee_id, binding_generation)`. The actor
is the sole writer of:

- current child generation and runtime handle;
- one monotonically increasing local prompt epoch;
- one active prompt transaction and its cancellation cause;
- ordered pending compaction boundaries keyed by distinct observation identity;
- the FIFO and its positive enqueue sequence;
- every client-message ID claimed during the live binding;
- observed backend compaction identities;
- open/closing/failed lifecycle.

The broker has a short lock only for locating/creating actor records. Public calls enqueue typed
commands and await command futures. The actor never awaits a full prompt task; prompt completion,
cancel-send completion, cancel timeout, strategy completion, capture completion, and child death
enqueue generation/epoch-tagged commands. It may await the publisher while processing a command, but
never awaits ACP cancel/prompt/strategy execution inline and holds no state mutex or registry lock.
This gives one deterministic mutation and publication order without allowing a stalled transport
send to block death, timeout, or shutdown commands.

The broker validates that every submitted `PromptRequest.session_id` equals the live binding before
claiming it. The first submission of a client-message ID claims it for the whole binding even if it
is rejected; later reuse always publishes a rejected receipt and cannot execute. A deliberately new
binding generation gets a new actor and new ID set. Child respawn under the same binding keeps the
set and the already-empty queue.

### Common start and settlement rules

Starting a prompt means incrementing the local epoch, installing the active record, creating the
exact child `prompt(request)` task, and only then publishing `started`. Task creation is the point at
which the invocation owns the active slot. The `PromptResponse` is retained only as settlement
(`stopReason`/failure); it is never published as transcript content.

An ordinary start publishes in this exact order:

1. `accepted` receipt;
2. create the exact child prompt task;
3. `started` receipt;
4. `thinking` activity.

For an exact explicit compaction command, the special order in section 4 replaces steps 2–4 so its
live boundary exists before invocation.

On an `end_turn`, `max_tokens`, `max_turn_requests`, or `refusal` response, clear the active prompt
and publish `idle`. If the FIFO is non-empty, then remove its head exactly once, publish the
replacement snapshot, create that exact queued prompt task, publish its `started` receipt with
choice `queue`, and publish `thinking`. This makes the completed transaction's idle state explicit
before the next queued transaction owns the slot.

A `cancelled` response is an interruption. If no broker cancellation was pending, publish the active
prompt's one `interrupted` receipt and then use the same FIFO-advance rule. A task exception that is
not already explained by an intentional cancellation is treated as an indeterminate child failure;
it never advances the FIFO.

Every completion/cancel/death command carries child generation and local epoch. A late response,
cancel completion, timeout, or death from N is ignored after N+1 owns the active slot. Duplicate task
callbacks can neither publish a second receipt nor pop a second queue item.

Cancellation cause freezes successor policy before any transport work:

| Cause | Active settlement | Successor policy |
| --- | --- | --- |
| `user` | interrupted once | may advance exactly one FIFO head |
| `send_now` | interrupted once | starts only the accepted Send Now submission; FIFO stays behind it |
| `new_conversation` | interrupted once | rejects entire FIFO, publishes empty snapshot, starts nothing |
| `shutdown` | interrupted once | rejects entire FIFO, publishes empty snapshot, starts nothing |
| `child_failure` / cancel timeout | interrupted once | rejects pending Send Now and entire FIFO, publishes empty snapshot, starts nothing |

Every cause installs `no_successor` or the exact successor reference before creating cancellation
tasks. Response, timeout, death, and close races consult that frozen cause rather than a common
completion branch.

### Delivery choices

`normal` is accepted only in `idle`. A running, cancelling, or capture-finalizing record publishes a
rejected `normal` receipt and does not invoke the child.

`queue` is accepted only while the prompt task is still active, including its bounded cancellation
wait. Construct the exact `QueuedPrompt` using the submitted request, the next broker enqueue
sequence, and injected integer wall time. Append it, publish one `queued` receipt with the exact
one-based position, then publish the complete replacement snapshot. No `/queue` text, child prompt,
Chat row, or event row is used as delivery.

`send_now` is accepted only for a running, not-yet-cancelling prompt. Its exact sequence is:

1. claim the new ID and publish its `accepted` receipt;
2. mark the old epoch `send_now` cancelling, freeze its successor, and arm the named cancel-settlement
   timeout immediately before any permission or transport await;
3. create owned generation/epoch-tagged tasks to cancel permission requests for that epoch and send
   the exact ACP `CancelNotification` through the runtime lease;
4. when the old prompt task settles, publish its one `interrupted` receipt;
5. create the submitted prompt task, publish its `started` receipt, then `thinking`.

The existing FIFO is not popped or republished in this path; it remains behind the Send Now prompt
in original order. A second Send Now, normal, or steer while cancellation is pending is rejected.

If the cancel-settlement timeout wins, atomically mark the child generation failed and forbid a
successor start. Cancel/await the owned permission-cancel and cancel-send tasks, then retire and
await teardown of the exact runtime lease within the active shared deadline. Publish in this order: interrupted old active
receipt, rejected Send Now receipt, one rejected receipt for each queued item in FIFO order, empty
queue snapshot, failed activity. A retirement deadline failure is part of the broker's typed failure
and may not leave admission open. The late old response and retirement death callback are idempotent
no-ops. This reject-all rule is mandatory because execution of the old prompt is indeterminate.

`steer` is accepted only while the main prompt is running and the selected definition declares
`supports_steer`. Before scheduling it, the broker acquires an exact-record runtime lease and its
generation-bound strategy proxy. It delegates the exact submitted `PromptRequest` and client-message
ID and publishes its exact accepted/rejected receipt when the strategy finishes. If N dies or N+1 is
published first, the lease rejects and no request reaches N+1. The active epoch and FIFO do not
change. Idle, cancelling, unsupported, and strategy-failed steer are visibly rejected and never
converted to normal, queue, or Send Now. The generic broker does not inspect content for Hermes
semantics and never checks a backend key.

### Cancel, child death, new conversation, and shutdown

Active browser cancel has no queued ID. It marks the epoch user-cancelling and freezes its successor,
arms the same settlement timeout before any await, and creates owned permission-cancel and cancel-
send tasks. Settlement publishes one
interrupted receipt. It then pops/publishes/starts at most one queued prompt by the common rule; with
no queue it publishes interrupted activity followed by idle. A cancel timeout retires the
generation and runs the child-failure rule below.

Queued cancel looks up only the named FIFO item. On a match, remove it once, publish its interrupted
receipt with choice `queue`, recompute later positions from the new tuple order, and publish one
replacement snapshot. Unknown IDs publish a rejected receipt with choice `queue` and do not mutate
the FIFO. Repeated active cancel while already cancelling is likewise a typed rejection and cannot
send a second ACP cancel.

Unexpected child death for the current generation atomically stops delivery, then:

1. publishes one interrupted receipt for the active prompt, if any;
2. rejects a pending Send Now submission, if distinct;
3. rejects every queued prompt in FIFO order;
4. publishes one empty queue snapshot unconditionally so every attached consumer clears stale FIFO
   state;
5. fails every open compaction boundary in observation order with its same boundary ID;
6. cancels current-generation permission requests and cleans current-generation terminals through
   their owners;
7. publishes failed activity.

Nothing auto-resumes. A later explicit bind of a respawned child under the same durable binding
returns the actor to idle with an empty FIFO; stale queued intent is gone and must be resubmitted.

`prepare_new_conversation(old_handle, deadline)` rejects new delivery for the old binding, cancels
and settles the active prompt within the remaining budget after installing the `new_conversation`
no-successor cause, rejects the entire FIFO in order, publishes the empty snapshot, fails every open
compaction boundary, cancels permissions, releases terminals, and removes old terminal display
snapshots. A racing prompt response cannot run the user-cancel FIFO-advance branch. It never calls
`session/new`; ACP-01 remains the binding owner.

`shutdown(deadline)` first closes admission for every record, then drives all record actors
concurrently against the same absolute deadline. Each actor installs the `shutdown` no-successor
cause before creating at most one active cancel task, rejects the FIFO in order, publishes the empty
snapshot, fails every open compaction, cancels permissions, and releases terminals. At deadline, any
still-live prompt generation is retired and all local prompt/cancel-send/timer/strategy/capture tasks
are cancelled and awaited.
Shutdown is idempotent, leaves no actor/task/process handle, and returns or raises a typed shutdown
failure naming unfinished employee IDs rather than hanging.

## 4. Explicit and automatic compaction normalization

### Generic broker behavior

The generic exact-command predicate is true only when the prompt has one block, that block is the
official `TextContentBlock`, and `block.text.strip() == "/compact"`. It is special only for an idle
`normal` delivery on a definition declaring `observes_compaction`; the original exact
`PromptRequest` is still what reaches the agent.

Explicit publication/start order is:

1. accepted normal receipt;
2. allocate an injected stable boundary ID and publish `compacting`, trigger `explicit`;
3. publish `compacting` activity;
4. create the exact child prompt task;
5. publish the normal `started` receipt.

The boundary is attached to that local prompt epoch in its ordered boundary map. User cancellation,
child death, new conversation, or shutdown publishes `failed` once for every open boundary with its
same boundary ID and a display-safe reason.

Every ordinary typed `SessionNotification` is passed once, in ACP-01 consumption order, to the
selected `BackendTurnStrategy.observe_compaction`. A returned automatic observation is accepted only
when its exact value has state `compacting` and trigger `automatic`, and only for the current session
and running epoch. The broker treats the strategy's returned boundary ID as an opaque observation
identity for the live binding. The first occurrence allocates its own stable boundary ID, publishes
one `compacting` trigger `automatic`, publishes compacting activity, and appends it to the owning
epoch's ordered boundary map. Only the same observation identity is deduplicated; a second distinct
transition in one prompt opens a second boundary. A wrong-state/trigger return is a strategy contract
failure, and a duplicate or stale generation/epoch creates no event. The generic broker does not
parse metadata or preserve a strategy-supplied boundary ID.

One exception prevents double-reporting the explicit command's own provenance: while the exact
`/compact` epoch has its initial explicit boundary and no provenance identity yet, the first distinct
compression observation is attached as that boundary's observation identity instead of opening an
automatic boundary. A duplicate of it is suppressed; any later distinct transition in the same
prompt opens its own automatic boundary. Tests cover explicit+matching provenance, two later distinct
transitions, and duplicates of all three.

The prompt-consumption barrier in section 2 guarantees every pre-response provenance notification is
offered before the owning prompt settles. After a successfully settled owner with one or more open
boundaries, keep the transaction from advancing the FIFO, close admission for that actor, settle
generation-N permission/terminal owners, and perform the generation-replacement private capture from
section 2. One exact capture result finalizes every boundary opened by that epoch in observation
order. ACP exposes only the final compacted history, so two distinct transitions in one prompt
receive the same final captured summary while remaining two visible boundaries; duplicates receive
none. Normalize only the compacted summary or failed reason onto each broker-owned boundary ID and
original trigger, publish every terminal boundary, install the returned N+1 runtime handle, publish
idle, then advance the FIFO. Capture result IDs/triggers can never replace broker ownership. A
capture return that is not exact state `compacted` with a non-empty summary or exact state `failed`
with a reason is normalized to the fixed failed-capture reason for every open boundary.

A capture slot containing `ProtocolUpdateRejectedPayload`, a load/protocol exception, no recognized
summary, or blank normalized summary produces a visible `failed` boundary with a fixed display-safe
reason. Raw exceptions remain diagnostic only. The private capture collector records exact typed
updates in order; it does not call the ordinary event publisher, transcript sink, or activity
reducer. Thought remains `AgentThoughtChunk` and is never inspected as assistant content.

### Hermes strategy

Implement `HermesAcpTurnStrategy` only in `hermes_turn_strategy.py`, and inject its concurrent prompt
port and controlled capture port. `hermes_backend.py` continues to receive it through the existing
definition builder; no generic module imports it.

Hermes steer accepts exactly one non-empty official `TextContentBlock`. It builds one exact
`PromptRequest` for the same session whose sole block is
`TextContentBlock(type="text", text="/steer " + submitted_text)`, then invokes the concurrent prompt
port. Any official response produces an accepted steer receipt; invalid content or an exception
produces a display-safe rejected steer receipt. Multiple blocks, image/audio/resource content, and
empty text are rejected rather than flattened. The broker's active check happens before this call;
the strategy never implements an idle fallback.

Hermes automatic observation inspects only an official `SessionInfoUpdate` whose own `_meta` has the
inspected path `hermes.sessionProvenance`. Require exact `reason == "compression"`, matching
`acpSessionId`, non-empty distinct `previousHermesSessionId` and `currentHermesSessionId`, and an
integer `compressionDepth`. Its stable observation identity is the tuple of previous ID, current ID,
and depth. Unknown keys are ignored; a missing/wrong/partial path returns no observation. There is no
status-text, token-drop, command-catalog, or backend-name inference.

For capture, scan the private typed replay in wire order across official `UserMessageChunk` and
`AgentMessageChunk` text. Pinned Hermes may emit the compressed summary as a standalone user or
assistant message, or merge it into the first preserved user/assistant tail. A standalone candidate
must start with the exact pinned summary prefix and end with the exact summary-end marker. A merged
candidate must contain exactly one pinned prior-context header and merged-summary delimiter, followed
by the exact summary prefix/body and ending marker; extract only the text after that delimiter and
before the marker. The ACP replay does not expose Hermes' internal `_compressed_summary` flag, so the
strategy makes no impossible provenance claim about text alone. It accepts exactly one structural
candidate across both roles only while finalizing a broker boundary opened by the exact explicit
command or typed provenance transition; zero or multiple candidates are an ambiguous visible capture
failure. Preserve the summary body exactly apart from surrounding whitespace and require it non-
empty. Thought, tool output, ordinary agent response prose, and provenance metadata are never
candidates. Keep the pinned strings and fixtures for standalone user, standalone assistant, merged
user tail, merged assistant tail, zero candidates, and multiple candidates in the Hermes module;
generic capture has no Hermes field or prefix.

## 5. Permission broker and attached-browser ownership

`permission_broker.py` owns attached browser connection IDs per employee and pending requests keyed
by `(employee_id, acp_session_id, request_id)`. Browser connection IDs, request IDs, integer wall
time, monotonic sleep, and timeout are injected. A pending record captures binding generation, child
generation, and active prompt epoch when present. Timeout/finalizer commands carry all of those
tokens.

ACP-02 adds an internal source-aware callback at the registry composition seam without changing the
frozen one-argument `PermissionRequestCallback` passed to the child factory. For each spawn,
`AcpEmployeeRegistry._spawn_initialized_child` creates that one-argument closure over the exact
employee, complete binding/record identity when published, and child generation. `_SdkClientBridge`
calls the closure and awaits its result with `asyncio.shield`; cancellation of the SDK handler task
cannot cancel the broker-owned settlement future. The closure enters the turn-broker actor first.
That actor serializes permission-open with prompt cancel/death/new/shutdown, verifies the exact
runtime lease and active prompt epoch, records an admission token, and only then asks the permission
broker to publish. Terminal causes first tombstone the epoch/generation and then settle recorded
tokens. A callback arriving after a tombstone receives exact ACP `cancelled` with no publication, so
"cancel saw none, then late open" is impossible.

The public methods are `attach_browser`, `detach_browser`, `request_permission`,
`respond_to_permission`, cancellation by prompt epoch/child generation/binding, and bounded
shutdown. They serialize through one actor; no lock is held while publishing. Attachment means the
route-owned connection ID is currently present in that employee's set—not merely that the employee
has a historical browser session.

Opening a child callback follows this exact algorithm:

1. validate the source-captured employee, exact ACP session/binding, record identity, child
   generation, turn epoch admission token, and declared permission capability;
2. if no browser is attached now, return exact
   `RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))` immediately; publish no
   request, outcome, or waiting activity;
3. allocate one injected request ID, compute `deadline_at = integer_now + injected_timeout_seconds`,
   install the actor-owned pending future, and publish the exact agent request through
   `publish_permission_request`;
4. publish `waiting_for_permission` activity after the options are visible, then arm one timeout
   task for that same injected interval. The actor returns the pending future to
   `request_permission`; the child callback shield-awaits it outside the actor so browser-response,
   detach, timeout, death, and caller-task cancellation remain admissible.

The embedded `RequestPermissionRequest` is the sole owner of session ID, tool call, and ordered
`PermissionOption` values. Labels, IDs, kinds (including `allow_always`), order, and metadata are
unchanged. Panels stores no remembered grant, inferred scope, ticket proposal, gate resolution, or
Chat row.

One browser connection ID may be attached to only one employee at a time, and generated request IDs
must be unique among live/tombstoned requests. `respond_to_permission(browser_connection_id,
request_id, option_id)` resolves ownership from that attached employee and returns a small frozen
Panels result with disposition `selected | already_settled | rejected`; it is not an ACP response copy.
Validation order is: browser still attached to the employee, request exists for the live binding,
request pending, then exact option ID exists in the supplied agent options. Wrong browser, wrong
employee/session, unknown/stale request, and invalid option return rejected without settling. The
first valid response claims the settlement; simultaneous later valid responses return
already-settled.

For a visible request, every settlement first publishes exactly one matching
`ConversationPermissionOutcome`, then restores activity for the captured turn epoch (compacting if
that boundary remains active, thinking for an ordinary running prompt, idle if no prompt remains).
Restoration is suppressed when that epoch is already cancelling/failed, because its interruption
publication owns the next activity transition. The permission broker then resolves the child callback:

- selection uses exact `AllowedOutcome(outcome="selected", option_id=option_id)`;
- every cancellation uses exact `DeniedOutcome(outcome="cancelled")` and a display-safe cause.

One browser detaching does nothing while another connection remains. Detaching the last connection
cancels every pending visible request for that employee. Timeout, active-prompt cancellation, child
death, old binding replacement, new conversation, and shutdown use the same idempotent settlement
primitive. Settled tombstones remain for the live binding so duplicate responses can be distinguished
from unknown ones; finalizer and timeout tasks are cancelled/awaited and cannot settle a newer
request. Every published permission request therefore has exactly one published outcome, while the
required no-browser fast cancellation has neither.

## 6. Confined reverse filesystem

`reverse_services/path_confinement.py` resolves each declared workspace root with `strict=True` at
service construction and uses `Path.is_relative_to` containment—not prefix strings. It exposes
purpose-named read-file, write-file, and directory resolvers shared by filesystem and terminal cwd.
All requested paths must be absolute. Construction opens a read-only directory descriptor for each
resolved root; descriptors are owned and closed by the service.

Reject lexical `..` first. For an existing target—including a safe internal symlink—resolve it
strictly to one canonical path, select the containing resolved root, and open that canonical relative
path descriptor-to-descriptor with `os.open(..., dir_fd=parent_fd, O_DIRECTORY | O_NOFOLLOW |
O_CLOEXEC)` for directories. Open the canonical final component with `O_RDONLY | O_NOFOLLOW` for a
read or `O_WRONLY | O_TRUNC | O_NOFOLLOW` for a write. For a missing write target, resolve its parent
strictly, select/traverse that canonical parent, and open the final name with `O_WRONLY | O_CREAT |
O_EXCL | O_NOFOLLOW` and an explicit ordinary-file mode. `lstat`/`stat(...,
follow_symlinks=False)` distinguishes a dangling final symlink from a genuinely missing name before
the create branch. `fstat` the actually opened descriptor and require a regular file. All read/write
I/O uses that descriptor, never reopens the caller's path string. Replacing the caller's safe symlink
after resolution cannot redirect the already-selected canonical target; replacing a canonical
component fails no-follow/open-exclusivity. A dangling or escaping symlink, directory, missing
intermediate parent, traversal, sibling-prefix path, or root-resolution failure is rejected. The
service creates no directory and performs no other filesystem operation.

`ConfinedAcpFilesystemService` is enabled only when the selected definition advertises filesystem
and the exact live session/binding resolver agrees with `request.session_id`. Its methods consume
and return only official SDK models.

- Read bytes from the confined descriptor once, decode strict UTF-8, and fail visibly on decode error. `line=None` starts at line
  one; a supplied line must be at least one even though the generated model currently permits zero.
  Split with line endings retained, so `line=1`, non-negative `limit`, `limit=0`, a final line without
  newline, CRLF, and empty files preserve exact text. `limit=None` returns the remaining file.
- Write encodes the exact supplied string as strict UTF-8 and replaces the descriptor-owned target's entire
  byte content. It does not normalize newline or append. Encoding/I/O errors fail the request; a
  successful call returns exact `WriteTextFileResponse()`.

Relative paths, wrong sessions/generations, disabled capability, directories, and every escape fail
closed through one purpose-named reverse-service error translated by the SDK request router. There
is no glob, delete, mkdir, shell, product file writer, or fallback root.

## 7. Scoped reverse terminals and terminal-state snapshots

`reverse_services/terminal.py` owns live handles and display snapshots keyed by employee + ACP
session + terminal ID. Terminal IDs and monotonic time are injected. A released ID cannot be reused
while its binding's display snapshot exists. All six methods accept and return the exact official SDK
request/response models and validate the exact live binding/generation before touching a handle.

### Create and environment

For `CreateTerminalRequest`:

1. reject disabled capability or wrong session;
2. resolve explicit cwd as an absolute existing directory inside a workspace root; when omitted,
   resolve the injected backend working directory by the same rule;
3. validate every requested environment name with the existing backend rule (non-empty, no control,
   `=` or NUL), reject NUL values, and apply the ordered agent overrides to a fresh copy of the one
   injected base environment; never read `os.environ`;
4. choose `retained_byte_limit = min(configured_maximum, request.output_byte_limit)` when the agent
   supplies a limit, otherwise use the configured maximum;
5. spawn only with `asyncio.create_subprocess_exec(request.command, *(request.args or []), cwd=...,
   env=..., stdout=PIPE, stderr=STDOUT)`—never a shell;
6. install the handle, start reader/exit/publication tasks, publish the initial active empty
   `ConversationTerminalState`, then return exact `CreateTerminalResponse`.

Agent overrides are applied in list order, so a later duplicate name is the deterministic final
value; values otherwise survive exactly. A duplicate generated terminal ID, spawn failure, bad cwd,
or invalid environment publishes nothing and leaves no handle.

### Output, exit, kill, release, and replay

The stdout reader drains continuously in byte chunks; merged `STDOUT` preserves the process pipe's
stdout/stderr arrival order. Use an incremental UTF-8 decoder with replacement for invalid process
bytes, retain complete Unicode characters, count their encoded UTF-8 bytes, and discard whole leading
characters until the current snapshot is within the byte limit. Once any output is discarded,
`TerminalOutputResponse.truncated` stays true. A zero limit retains `""` and becomes truncated when
the first output arrives.

Draining never awaits a publisher. It updates the bounded live snapshot/version and signals one
coalescing publication task; that task publishes the latest version serially and rechecks the
version after every await. Thus a slow publisher cannot fill the subprocess pipe, publication order
is monotonic, and each externally observable bounded snapshot is complete. Only a successfully
published version enters the display-snapshot map; `terminal/output`, wait, release, and replay wait
for the relevant version's publication before returning it. Deterministic tests write one controlled
chunk at a time and wait for its publication.

On process exit, flush the decoder and map non-negative return code to exact
`TerminalExitStatus(exit_code=code, signal=None)`; map a negative POSIX return code to
`exit_code=None` and the exact signal name. Publish the active exited snapshot before resolving any
waiter. `terminal/output` returns the same current exact `TerminalOutputResponse` as the latest
published live snapshot. `terminal/wait_for_exit` waits for exit publication and returns exact
`WaitForTerminalExitResponse`. `terminal/kill` uses `Process.kill()` when running, waits until the
common watcher has observed process exit, drained/flushed the decoder, and published the exact exited
snapshot, then returns success without releasing the ID. It never reports success while a SIGTERM-
ignoring process remains live. The kill operation has one absolute cleanup deadline and returns a
typed failure if exit ownership cannot be established within it.

`terminal/release` is one idempotent owner operation: mark releasing, terminate if running, await the
remaining configured cleanup budget, kill if necessary, wait/drain, publish any missing exit update,
then publish one final lifecycle `released` snapshot. The single absolute deadline covers graceful
wait, force kill, process wait, incremental-decoder flush, exit publication, released publication,
and cancellation/await of reader, watcher, and coalescing-publisher tasks. Deadline expiry force-
kills the process, cancels/awaits every local task within the remaining shared shutdown disposition,
removes the live handle, and returns/reports its terminal ID as unfinished rather than hanging.
Remove the normal live handle only after the released publication and invalidate output/wait/kill/
release calls for the ID. Retain the latest released snapshot in the separate display map.

Expose `display_snapshots(binding)` in stable creation order for ACP-04 attach replay. Exited active
and released snapshots may remain only while that binding is live. Child death releases every live
handle for the matching child generation but retains its final display state. New conversation
releases, publishes, then removes every old-binding display snapshot. Shutdown releases all handles
concurrently against one shared absolute deadline, clears display state, and reports unfinished IDs
without hanging. Cleanup, exit, release, child death, and timeout commands are generation-guarded and
idempotent.

## 8. SDK reverse-service bridge and truthful capability negotiation

Update the concrete `SdkAcpEmployeeChildFactory` to accept optional injected filesystem and terminal
ports. Replace ACP-01's temporary blanket rejection with exact composition validation:

- a definition declaring filesystem must receive the complete filesystem port;
- a definition declaring terminal must receive the complete terminal port and typed event
  publisher through that service;
- a missing required service fails before spawn/initialize;
- an injected but undeclared service is not advertised and an unexpected reverse call receives
  method-not-found;
- permission remains routed through the existing exact child permission callback and is likewise
  callable only when declared.

`build_panels_initialize_request` continues to derive `ClientCapabilities.fs.readTextFile`,
`writeTextFile`, and `terminal` only from the definition after the complete-port checks pass.

Extend `_SdkClientBridge` with the exact public `acp.interfaces.Client` callback signatures for
`read_text_file`, `write_text_file`, `create_terminal`, `terminal_output`,
`wait_for_terminal_exit`, `kill_terminal`, and `release_terminal`. Each callback constructs its exact
generated request model from SDK keyword arguments, invokes the injected scoped service with employee
and child generation, and returns the exact generated response model. It does not accept raw dicts,
parse JSON-RPC, or reinterpret service errors as assistant messages.

Hermes continues to advertise permission only. Filesystem/terminal tests use fake backend
definitions to prove truthful negotiation; no generic factory or service checks `"hermes"`.

## 9. Deterministic fixtures and TDD implementation order

Extend `tests/support/acp_scripted_agent.py` only with opt-in behavior for a held active prompt,
observable cancel, concurrent steer, exact current/partial/duplicate Hermes provenance, controlled
load summary replay, reverse permission calls, reverse filesystem calls, terminal streaming, and
deterministic death. Keep default ACP-00/01 scripts unchanged and use the official SDK for normal ACP
traffic.

Add only focused fixture files below `tests/fixtures/acp/` when a subprocess script is necessary. A
terminal fixture writes alternating `os.write(1, ...)` / `os.write(2, ...)` chunks, split UTF-8 code
points, environment/cwd facts, a large bounded stream, and a kill-held process without shell syntax.
Hermes-shaped compaction fixtures contain the exact inspected provenance path and exact typed replay
chunks; no test reads or modifies the Hermes checkout.

Implement in this TDD order:

1. Write the recording publisher, fake runtime/child, actor latches, and exact receipt-order tests in
   `tests/unit/test_conversation_turn_broker.py`; implement publisher/ports and the ordinary FIFO
   state machine.
2. Add prompt-response consumption and private capture barrier tests, then update
   `ordered_ingress.py` and `sdk_child.py`. Prove delayed provenance is consumed before prompt return
   and capture slots never reach the ordinary sink.
3. Add explicit/automatic compaction and Hermes strategy tests; implement broker boundary ownership,
   capture finalization, `/steer`, exact provenance, and summary extraction.
4. Write attachment/response/cause race tests in
   `tests/unit/test_conversation_permission_broker.py`; implement exactly-once settlement and actor
   lifecycle hooks.
5. Write path/read/write/session/capability tests in
   `tests/unit/test_conversation_reverse_filesystem.py`; implement shared confinement and filesystem.
6. Write real subprocess argv/env/output/wait/kill/release/cleanup tests in
   `tests/unit/test_conversation_reverse_terminal.py`; implement the terminal owner and typed
   snapshots.
7. Extend the SDK bridge and run reverse calls through a real scripted ACP child from the filesystem,
   terminal, and permission tests. Prove undeclared/missing services fail before use.
8. Extend/create `tests/support/acp_runtime_subject.py` after ACP-01 is integrated so production
   broker/permission/strategy subjects provide ACP-00 evidence for probes 5, 7, and 8. Run the
   existing assertion functions rather than adding a lookalike vocabulary.
9. Run the focused commands once after implementation settles. The orchestrator, not the
   implementation sub-agent, performs the independent diff review and later canonical `./verify`.

## Acceptance-to-test map

| Named acceptance | Exact focused proof |
| --- | --- |
| Turn state machine | `test_conversation_turn_broker.py`: idle/running/cancelling/capture/failed choices; exact accepted/queued/started/interrupted/rejected order; one active task; FIFO positions/snapshots; queue single delivery; Send Now priority and cancel timeout reject-all; cancel-send itself held before timeout; active/queued/unknown cancel; duplicate IDs; prompt stop reasons; response/cancel/death epoch guards; response racing new/shutdown with no successor; publisher failure; shared-deadline shutdown |
| Child-death recovery | same file: active interrupted once, pending Send Now and every queued item rejected in FIFO order, one empty snapshot, no auto-resume after same-binding respawn, stale death ignored |
| Compaction | turn tests plus `test_hermes_acp_turn_strategy.py`: explicit live boundary before invocation; two distinct provenance transitions plus duplicates in one prompt; every broker boundary completes/fails once; one generation-replacement capture finalizes the ordered set; N's intentional-close callback arrives before N+1 spawn without child-failure cleanup; fresh-child isolation keeps a deliberately delayed N ordinary update out of private capture and prevents replay rebroadcast; standalone user/assistant and merged user/assistant summary fixtures; thought typing/protocol rejection; zero/multiple summary candidates fail visibly; no FIFO advance before capture |
| Native steer | Hermes strategy tests: exact concurrent `PromptRequest` with sole `/steer ` text block; accepted/rejected receipt; multiple/non-text/empty/idle/unsupported failures; no queue/normal fallback; strategy exception visible; pause N Steer across death/N+1 same-binding publication and prove no request reaches N+1 |
| Permission | `test_conversation_permission_broker.py`: exact option object/order; no-browser immediate ACP cancellation without events; two-browser ownership; same session ID under different employees; first valid response; invalid/wrong/stale/duplicate dispositions; late open after cancel/death tombstone; SDK callback-task cancellation cannot cancel settlement; injected timeout drives deadline and task; one-tab vs last-tab; timeout/prompt cancel/death/new/shutdown exactly once; request/outcome sequence ownership; activity restore; generation-guarded finalizers; no product canonical writer |
| Filesystem | `test_conversation_reverse_filesystem.py`: exact line/limit/newline/UTF-8 read and replacement write; safe internal symlink; relative/zero-line/traversal/sibling-prefix/escaping and dangling final symlink/wrong-session/wrong-generation/directory/decode/missing-parent/disabled capability rejection; deterministic parent/final path swaps for read and write cannot redirect the descriptor outside a root |
| Terminal | `test_conversation_reverse_terminal.py`: `create_subprocess_exec` argv literalness; confined explicit/default cwd; injected base env and exact overrides with ambient sentinel absent; employee/session/ID isolation; stdout/stderr order; split/invalid UTF-8; configured/smaller/zero byte limits and exact `truncated`; output/wait/force-kill/release including a SIGTERM-ignoring fixture; creation/output/exit/released publications; replay map; child-death/new/shutdown cleanup; one absolute deadline while decoder drain and final publications are latched |
| SDK/capability bridge | permission/filesystem/terminal tests through a real scripted child: exact official models, only fully injected declared services advertised, missing service fails before spawn, undeclared reverse call method-not-found |
| ACP-00 production probes | production runtime subject passes `permission_exactly_once`, `delivery_capabilities`, and `compaction_visibility`; the existing matching evidence mutations fail. Focused negative mutations/fakes also prove double completion cannot double-deliver queue, unsupported steer cannot fall back, permission cannot settle twice, capture cannot rebroadcast, path escape cannot pass, and terminal IDs cannot cross sessions |
| Generic/backend boundary | source/AST assertion over `turn_broker.py`, `permission_broker.py`, `runtime_ports.py`, `sdk_child.py`, and `reverse_services/**`: no backend-key literal/conditional; only `hermes_turn_strategy.py` knows Hermes provenance/prefixes; no conversation source imports current `chat/` or treats publisher calls as worker delivery |

Additional load-bearing race tests are explicit, not incidental coverage:

- raw prompt response observed while its typed provenance sink is blocked;
- prompt N settles concurrently with automatic provenance, queue submission, and child death;
- Send Now cancel-send stall, cancel response, cancel timeout, and child death race with exactly one winner;
- prompt response races new-conversation and shutdown cancellation after their no-successor state;
- queued head completion callback delivered twice and late after epoch N+1 starts;
- a generation-bound Steer/capture lease pauses across N death and N+1 publication without redirecting;
- planned N capture retirement delivers its close callback before N+1 spawn/load/publication without
  failing the actor or boundaries; an error callback with the same tuple remains unexpected;
- permission open races cancel tombstoning; select races timeout and last-browser detach; child death
  races final outcome publication; cancellation of the SDK callback task leaves actor settlement owned;
- terminal exit races kill, release, child death, and shutdown; every path publishes at most one
  released snapshot and leaves no reader/wait/publisher task;
- capture replacement races a delayed ordinary N update; capture load fails mid-replay and cannot
  leak its accepted prefix to ordinary ingress;
- descriptor-backed read/write pauses while the caller path is replaced by an escaping symlink;
- shutdown begins while prompt start publication, cancel, capture, permission publication, terminal
  spawn, and terminal release are each held by a deterministic latch.

## Focused acceptance commands

Do not install dependencies and do not run `./verify` from the ACP-02 implementation sub-agent.
After ACP-01 is integrated and the slice is settled, run these commands once and retain their full
output in this ticket directory:

```sh
.venv/bin/ruff check src/planner/conversation tests/unit/test_conversation_contracts.py tests/unit/test_acp_conformance_harness.py tests/unit/test_acp_employee_child.py tests/unit/test_acp_employee_registry.py tests/unit/test_hermes_acp_backend.py tests/unit/test_conversation_turn_broker.py tests/unit/test_conversation_permission_broker.py tests/unit/test_conversation_reverse_filesystem.py tests/unit/test_conversation_reverse_terminal.py tests/unit/test_hermes_acp_turn_strategy.py tests/support/acp_*.py
.venv/bin/mypy src/planner/conversation
.venv/bin/pytest tests/unit/test_conversation_contracts.py tests/unit/test_acp_conformance_harness.py tests/unit/test_acp_employee_child.py tests/unit/test_acp_employee_registry.py tests/unit/test_hermes_acp_backend.py tests/unit/test_conversation_turn_broker.py tests/unit/test_conversation_permission_broker.py tests/unit/test_conversation_reverse_filesystem.py tests/unit/test_conversation_reverse_terminal.py tests/unit/test_hermes_acp_turn_strategy.py
node web/tests/acp-contracts.test.mjs
npm --prefix web run check
npm --prefix web test
```

The independent implementation review should concentrate on prompt/cancel epochs, the response-
consumption barrier, queue single delivery, Send Now timeout retirement, capture isolation,
permission first-settlement ownership, resolved-path/symlink confinement, terminal byte-bound/output
publication/process cleanup, truthful capability negotiation, worker prompt delivery through the ACP
child, and the absence of generic Hermes branches. The orchestrator integrates ACP-02 serially after
ACP-01 and runs the canonical `./verify` only after review disposition.
