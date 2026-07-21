# ACP-04 contract — Hermes vertical cutover

## Outcome

Compose the already-reviewed ACP runtime and typed Svelte pane into Panels' one production
conversation path. The Panels server owns one ACP conversation hub and one websocket route. Chief of
Staff and Ticket employees use the Hermes ACP definition through that hub. Human prompts and
automatic Employee steps share the same employee-keyed ACP child, durable session binding, turn
broker, typed event publisher, permission broker, and browser stream.

This ticket replaces the active Chief/Ticket conversation path directly. It does not add another
feature flag. The legacy chat, relay, neutral-pane, and raw-frame source remains physically present
for ACP-05 comparison and ACP-06 deletion, but production startup and the three current Chief/Ticket
UI mount points no longer select or start it. No legacy frame is translated into ACP and no ACP event
is translated into the legacy transcript vocabulary.

ACP-00/00a/00b, ACP-01, ACP-02, and ACP-03 are prerequisites. Their frozen public Python and
TypeScript values are unchanged. If ACP-02's settled internal interfaces require a composition
adapter, add a narrow adapter; do not create a second broker, permission owner, terminal owner,
session registry, transcript reducer, or browser envelope.

## One production conversation composition

Add one purpose-owned conversation composition module. It constructs, in dependency order:

1. the SQLite durable binding repository and employee resolver;
2. the concrete typed browser event publisher/hub;
3. the Hermes turn strategy and pinned Hermes backend definition;
4. the official-SDK child factory and `AcpEmployeeRegistry`;
5. the ACP-02 turn, permission, filesystem/terminal service owners required by the definition;
6. one `AcpStepGateway` over that same hub for `EmployeeStepRunner`;
7. one websocket handler over that same hub.

Hermes advertises permission only, exactly as its reviewed definition states. The generic hub and
route do not test the string `"hermes"`; backend-specific behavior remains in the definition/strategy.
The server uses the repository root already resolved by `core/server.py` as the sole workspace root
for this single-workspace product. It does not add speculative project paths or a new workspace
configuration field.

Production lifespan creates this composition before starting `EmployeeStepRunner`, publishes it on
one named `app.state` attribute, and injects its exact step gateway into the runner. It does not start
`SharedGateway`, `EntityRoutingGateway`, `EmployeeChildPool`, `EmployeeChildRelay`, a transcript tee,
or either relay websocket. Shutdown closes admission, stops the runner first so its matched active
steps can interrupt through ACP, then shuts down the conversation hub/children within the same one
absolute monotonic deadline. No component receives a fresh shutdown budget.

Test mode may inject the existing official-SDK scripted ACP subject and deterministic clocks/limits;
production source must not import a test module at runtime outside that explicit injected/test-mode
factory. There is no in-memory production fallback when SQLite binding persistence fails.

## Durable ACP binding repository and migration

Add one SQLite table named `conversation_session_bindings` through the canonical DDL/schema-version
path. Its columns are exactly:

```text
employee_id        TEXT PRIMARY KEY
entity_kind        TEXT NOT NULL CHECK (entity_kind IN ('ticket','agent'))
entity_id          TEXT NOT NULL
acp_session_id     TEXT NOT NULL UNIQUE
backend_key        TEXT NOT NULL
binding_generation INTEGER NOT NULL CHECK (binding_generation > 0)
created_at         INTEGER NOT NULL
updated_at         INTEGER NOT NULL
UNIQUE (entity_kind, entity_id)
CHECK (employee_id = entity_id)
```

The migration backfills every non-null Ticket `employee_session_id` and top-level-agent
`chat_session_key` as backend `hermes`, binding generation `1`. A duplicate session across employees,
an unsupported top-level-agent ID, or conflicting pre-existing row fails migration loudly; it is not
silently reassigned. Day chat sessions are not employee conversations and are not imported.

One repository implements ACP-01's exact resolve/CAS seams. It uses short-lived connections and one
`BEGIN IMMEDIATE` transaction per CAS. The binding table is the complete ACP value; the existing
Ticket `employee_session_id` and Chief `agent_chat_sessions.chat_session_key` remain product mirrors
needed by worker-step settlement and current ownership checks until ACP-06 classification. Every
successful candidate writes the binding row and the correct product mirror atomically through the
existing canonical product writer. A mirror mismatch, wrong entity, wrong expected complete binding,
non-successor binding generation, backend/session mismatch, session already owned by another
employee, or write that retains a different winner returns the actual complete winner or fails
closed. The registry publishes no candidate before this transaction commits.

First binding is generation `1`. A deliberate new conversation or backend change is exactly current
generation plus one. Child crash/respawn, browser reconnect, server restart, and controlled
compaction child replacement preserve the ACP session ID and binding generation. There is one writer;
no route, hub, step gateway, or browser publisher writes the binding columns directly.

The employee resolver accepts only an existing Ticket ID or the existing Chief of Staff entity ID.
`employee_id == entity_id`. Ticket maps to entity kind `ticket`; Chief maps to `agent`. The current
binding backend wins when present, otherwise the configured registration default is `hermes`. An
unknown/deleted Ticket, day ID, arbitrary agent ID, or binding whose entity metadata disagrees fails
before child spawn.

## One typed browser stream per binding

Add one concrete implementation of ACP-02's `ConversationRuntimeEventPublisher`. It is also the sole
owner of browser subscription state and exact `ServerEnvelope` construction. For each complete
`(employee_id, acp_session_id, binding_generation)` it owns:

- one monotonically increasing positive envelope sequence for the live process;
- one current reset epoch buffer containing the exact serialized typed envelopes from its reset;
- the attached browser identities and one bounded outbound queue per browser;
- the exact current connection capability, queue, activity, permission, terminal, and compaction
  publications produced by the already-settled owners;
- any exact worker-prompt output collector registered by `AcpStepGateway`.

Every ACP child ingress value becomes exactly one `acp_session_update` or the already-frozen visible
`protocol_update_rejected` envelope. Every ACP-02 publisher call becomes its one matching frozen
envelope. The publisher allocates the outer sequence and the nested activity/permission sequence in
one operation, stores the envelope before making it visible, and enqueues that same immutable value
to every currently attached browser. A publisher failure is generation-fatal through the ACP-02
path. It never writes Chat messages/turns, the event log, or a canonical resource-cache event.

The hub additionally owns the two connection-only publications absent from ACP-02:

- one `connection: reset` before a canonical replay epoch, with exact binding generation and truthful
  backend `supportsSteer`;
- one `connection: ready` only after the reset's typed load/replay and all prior ordered ingress have
  completed.

It also publishes one `human_echo` with the exact browser client-message ID and `PromptRequest`
before asking the broker to accept/reject that human submission. This is transcript evidence, not
worker delivery. The only worker delivery is the broker's exact child `session/prompt` call.

Each browser has a bounded injected outbound-envelope capacity. Enqueue never waits on socket I/O.
A browser that exceeds its capacity is detached from permission ownership and closed as a slow
consumer; it cannot stall ordered ingress or another browser. The reset buffer has an injected byte
limit. Overflow marks that buffer unavailable for a new mid-turn subscriber but does not drop a live
publication from already-attached browsers. The next idle canonical load replaces it; a mid-turn
subscriber receives a visible connection error and retries rather than a partial transcript.

### Attach, replay, reconnect, and multiple browsers

The websocket path is exactly `/api/conversation`. The first valid action must be `attach`; one
socket is then permanently scoped to that employee. Later actions whose employee differs fail
closed. The cursor fields are both present or both absent. A future binding generation, future
sequence, malformed action, binary frame, action before attach, or second attach for another
employee is rejected without invoking a child.

Attach behavior is exact:

1. resolve the employee and durable binding, register the browser as attached, and serialize attach
   with new-conversation and child-death transitions for that employee;
2. when no prompt/cancel/capture is active, start one canonical replay epoch: publish reset, call the
   ACP registry's controlled typed attach/load for the same durable binding, allow ordered typed
   replay only through the normal publisher, restore the ACP-02 queue/permission/terminal snapshots,
   then publish ready;
3. when a prompt is active, do not issue `session/load` concurrently with it. Send the complete
   available reset-epoch buffer privately to the new browser, then publish one new ready envelope to
   all browsers. If the buffer is unavailable, publish/close with the visible retryable error above;
4. a reconnect with a retained same-binding cursor receives only valid sequence behavior; it is not
   a new conversation and never mints a session;
5. disconnect detaches only that browser. ACP-02's last-browser rule owns permission cancellation.
   It does not cancel an ordinary active prompt or destroy the employee child.

The live server sequence may restart after a process crash. The server raises its first reset
sequence floor to the greatest valid same-binding `last_seen_sequence` presented by attached
browsers. Therefore the ACP-03 controller receives a reset greater than its old cursor. This ticket
adds the necessary bounded controller integration rule: an exact same-entity, same-session,
same-binding `connection: reset` with a greater sequence establishes a replacement stream base even
when it is not contiguous; the following ready and every ordinary envelope must again be exactly
contiguous. A stale/equal reset is ignored. A different entity/session at the same generation still
fails closed. A recoverable gap/error clears only after the replacement reset is followed by its
contiguous ready event; protocol rejection remains persistent. This changes no public wire shape.

The reset/buffer/load ordering is tested with two browsers, attach during an active prompt, server
sequence restart, reconnect gaps, slow consumer eviction, child death, and a concurrent new
conversation. No browser observes N+1 before every accepted N envelope, and no subscriber receives a
partial replay prefix as a usable ready state.

## Browser action routing

After attach, the route validates each raw JSON object with the frozen `BrowserAction` adapter and
dispatches serially through the hub:

- `prompt`: require exact current session ID; publish the one human echo; call the ACP-02 broker with
  the exact client-message ID, prompt, and `normal | steer | send_now | queue` choice. Do not call a
  Chat API, append a Chat row, submit native `/queue`, or infer capability from command text.
- `cancel` without a queued ID: cancel the exact active broker turn with user cause.
- `cancel` with a queued ID: remove/reject only that queued prompt through the broker.
- `new_conversation`: close broker admission, settle/reject the active/queued/permission/terminal
  state by the ACP-02 rules, create and persist one new registry binding, start its sequence at one,
  publish reset/load/ready, and only then reopen actions.
- `permission_response`: require the requesting browser attachment and exact current employee,
  binding, request, and option; delegate first-settlement ownership to ACP-02. When the permission
  belongs to a running Ticket Employee step, also require that the active worker-turn session and
  Ticket `employee_session_id` both equal the permission's ACP session before selection can win.

Action tasks are owned by the connection/hub and are cancelled/awaited on socket teardown or hub
shutdown. A transport exception never leaves a claimed client-message ID executing without its
broker-owned settlement.

## ACP step gateway and existing EmployeeStepRunner correctness

Add `runtime/acp_step_gateway.py` implementing the existing synchronous `StepGateway` surface over
the event-loop-owned hub. It is a bridge, not a second child/session/turn owner. It rejects calls from
the owner event-loop thread rather than deadlocking, and uses `run_coroutine_threadsafe` plus one
bounded caller-thread handshake for the existing `on_session_key` callback. The callback executes on
the original EmployeeStepRunner thread, where its SQLite connection is owned.

For `run_ticket_step`:

1. resolve the exact Ticket employee and durable binding. `require_existing_session=True` requires a
   stored ACP binding before any spawn/new-session operation; absence fails without minting;
2. ensure the hub/registry has that exact binding. On a first binding, the binding repository commits
   the Ticket mirror before registry publication. Before prompt invocation, hand the effective ACP
   session ID to `on_session_key` on the caller thread and await its result. A lost Ticket claim or
   callback exception sends no prompt and cannot settle another Ticket;
3. submit one text-only `PromptRequest` as broker choice `normal` with a unique worker client-message
   ID. A live/cancelling turn maps to the existing `SharedGatewayBusy` behavior with the effective
   session ID; it is never queued or steered implicitly;
4. await the exact broker prompt epoch's settlement. Accumulate only typed agent-message text emitted
   by that epoch for the product-visible `RunResult`; thought/tool/plan/terminal/permission content is
   not flattened into it. Map successful ACP stop reasons to `complete`, explicit/shutdown cancel to
   `interrupted`, and child/protocol/publisher/capture failure to `errored`;
5. never send a worker-turn row, activity row, event row, or stored reply back to the agent. The
   `on_event` argument remains for `StepGateway` compatibility but ACP does not translate typed
   updates into legacy gateway events.

The gateway's active-step map is keyed by complete employee/session plus worker client-message ID.
`interrupt(session_key, entity_id, deadline=...)` validates both stored/current identities and
cancels only that exact active worker epoch through the broker. It uses the caller's remaining
absolute deadline for the synchronous wait. Late completion after an interrupt/lost claim cannot
complete or error a successor worker turn. `status().available` is false after hub admission closes
or when the Hermes definition cannot spawn; it performs no child spawn itself.

The existing `EmployeeStepRunner` remains the product claim/settlement owner. Only bounded adapter
changes are allowed. Its established tests plus new ACP composition tests must prove:

- the `on_session_key` Ticket compare-and-swap and worker-turn attachment complete before the one ACP
  prompt invocation;
- a lost claim invokes no ACP prompt and does not settle the winner's Ticket/turn;
- complete, errored, interrupted, busy, child-death, and post-claim failures settle worker turn and
  Ticket status exactly once;
- restart recovery uses the stored ACP session with `require_existing_session=True` and cannot mint a
  replacement after missing/load failure;
- shutdown interrupts the exact matched worker prompt within the existing deadline and leaves no
  Ticket stranded at `agent_running_step`;
- the worker product row is visibility/runtime settlement only and never appears in the actual ACP
  prompt or session history;
- clarification/permission response ownership rejects a stale worker-turn/Ticket session mismatch.

## Production Svelte mount

Add one non-visual composition wrapper which creates the production `PanelsTransport` for
`/api/conversation`, creates one `ConversationController` for its injected employee ID, and renders
the reviewed `AcpConversationPane`. It owns no transcript state beyond that controller and introduces
no new styling system.

Replace the Chief/Ticket conversation branches in exactly these current mount points:

- Board workspace Chief inspector;
- Ticket route conversation rail (including the Board-selected Ticket route);
- Chief of Staff route.

Each passes `employeeId == entityId` and the existing human-readable employee label. Remove the
relay-capability/loading/error and legacy gateway-status selection from those mount points. They
always render the ACP wrapper. Do not modify the approved Workspace information hierarchy, Ticket
fields, shared app layout, or unrelated route state.

The wrapper uses the current origin's `ws:`/`wss:` scheme, exact `/api/conversation` path, browser
`WebSocket`, existing reconnect timing, and cryptographically strong client-message IDs. It does not
call `/api/chat/commands`, a private skills endpoint, a Chat turn endpoint, or either relay route.
Images remain exact ACP image content blocks through the reviewed composer. Slash commands come only
from typed per-session `available_commands_update`. Stop and new-conversation remain separate Panels
controls.

Use the existing Panels tokens and cardless conversation geometry. Do not copy acp-ui styling, add
decorative cards/gradients/shadows, alter shared tokens, or redesign the surrounding routes in this
cutover ticket.

## Focused proof

Add focused unit/integration/browser tests with an official-SDK scripted agent. At minimum prove:

1. **Binding migration/repository:** exact backfill, first bind, successor generation, CAS loser,
   mirror atomicity, uniqueness, rollback, restart resolve, child crash stability, and invalid entity
   rejection.
2. **Hub publisher:** exact envelopes/sequence and nested sequence equality, typed ingress and visible
   rejection, reset/load/ready barrier, capability truth, human echo before receipt, no Chat/event-log
   writes after binding, and publisher failure generation teardown.
3. **Websocket:** trusted ingress, attach-first/action validation, one employee per socket, refresh
   typed thought, two browsers, active-turn buffer replay, same-binding replacement reset, gap
   recovery, slow consumer, detach/permission ownership, new conversation, and child death.
4. **Actions:** normal/queue/send-now/declared Hermes steer, queue cancellation, active cancel,
   explicit compaction command, image prompt, command catalog provenance, each permission option,
   session mismatch rejection, and no legacy delivery call.
5. **Step composition:** every EmployeeStepRunner obligation named above through the real ACP step
   gateway and same child used by a browser; automatic step output appears once in the ACP pane and
   product worker turn, while the row itself is absent from worker context.
6. **UI mount:** all three mount points instantiate the production ACP wrapper with correct identity;
   no `ChatPanel`, `ChiefNeutralPane`, relay capability, legacy status, Chat API, or relay websocket
   is imported/called by the active Chief/Ticket path.
7. **Ownership/composition:** production startup constructs one hub/registry/broker and one step
   gateway; neither legacy gateway nor relay pool starts; browser and step demand coalesce onto one
   employee child/session; shutdown order and one deadline are exact.
8. **Real vertical e2e:** with the scripted ACP backend, a browser sends a human prompt, sees separate
   collapsed typed thought/tool/assistant content, refreshes without thought spill, exercises a
   mid-turn choice and permission, starts a new conversation, and observes a triggered automatic
   Ticket step on the same route.

The test suite must use deterministic latches for reset/load/prompt/death races. It may inspect the
scripted agent's received ACP requests to prove actual worker delivery. A DB Chat row, browser echo,
event publication, or mocked broker call is not delivery evidence.

## Allowed files

- `src/planner/conversation/**`
- `src/planner/runtime/acp_step_gateway.py`
- `src/planner/runtime/step_gateway.py`
- `src/planner/runtime/employee_step_runner.py`
- `src/planner/runtime/__init__.py`
- `src/planner/core/db.py`
- `src/planner/core/server.py`
- `src/planner/core/loops.py`
- `src/planner/core/testmode.py` only for the existing explicit test trigger
- `src/planner/chat/data.py` only to extract an in-transaction top-level-agent session-key writer;
  `record_agent_session_key` must delegate to that same writer so there remains one canonical
  transition. This exception is required because the current public writer opens its own
  `BEGIN IMMEDIATE` and therefore cannot join the binding repository's atomic transaction.
- `web/src/lib/acp/**`
- `web/src/components/acp/**`
- one new non-visual production ACP wrapper below `web/src/components/`
- `web/src/routes/BoardRoute.svelte`
- `web/src/routes/TicketRoute.svelte`
- `web/src/routes/ChiefOfStaffRoute.svelte`
- focused `tests/unit/test_acp_*.py`, `tests/unit/test_conversation_*.py`, and
  `tests/e2e/test_acp_conversation.py`
- `tests/support/acp_*.py` and `tests/fixtures/acp/**`
- focused `web/tests/acp-*.test.mjs`
- `orchestration/tickets/acp-04-hermes-vertical-cutover/**`

Any additional file requires an orchestrator-written reason in this ticket before implementation.
Generated `web/dist` output is refreshed only after reviewed source integration, not hand-edited.

## Must not touch

Do not delete or edit `minds/**`, `hermes_backend/**`, `chat` tables/contracts/routes/legacy transport
(apart from the exact `chat/data.py` writer extraction allowed above),
`neutralPane.ts`, `ChatPanel.svelte`, `ChiefNeutralPane.svelte`, shared CSS/tokens, dependency
pins/locks, config YAML/schema fields, ticket/domain contracts, unrelated routes/tests, `docs/`,
`verify`, or the Hermes checkout. ACP-05 owns dogfood corrections; ACP-06 owns classification and
legacy deletion. Do not edit an existing non-ACP test merely to make it pass.

## Review and integration

One sub-agent writes a concrete implementation plan and acceptance map after reading the settled
ACP-02 source/report as well as this contract. A different sub-agent performs one focused plan review
against the program, the frozen contracts, current server/runner/DB ownership, and ACP-02/03 APIs.
After finding dispositions, one implementation sub-agent works only the allowed files; a different
sub-agent performs one focused implementation review. Use a second round only for a material
load-bearing blocker.

The orchestrator spot-checks durable binding/mirror CAS, reset/load/ready and sequence restart,
prompt/queue/cancel single delivery, permission attachment/session validation, step callback ordering,
worker settlement, startup/shutdown ownership, and absence of active legacy calls. The implementer
does not run `./verify`. ACP-04 integrates serially after ACP-02 and ACP-03. ACP-05 computer-use
dogfood is the next gate.
