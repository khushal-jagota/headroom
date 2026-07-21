# ACP migration program

Status: independently reviewed — ready for ACP-00 contract freeze  
Owner brief: `orchestration/acp-migration/direction.md`  
Evidence: `orchestration/acp-migration/research.md`
Review: `program-review-round-1.md` (findings resolved), `program-review-round-2.md` (`READY`)

Owner compaction amendment (2026-07-20): compaction is an opaque backend lifecycle, not readable
conversation content. Panels shows that compaction started, finished, or failed with the exact reason;
it never extracts, stores, renders, or offers an expandable backend summary. A backend may complete a
valid compaction without producing a replay summary marker. Backend support for compaction is optional
and must not gate otherwise-conformant worker registration; an unsupported backend disables/rejects the
operation visibly. This amendment supersedes every older reference below to an inspectable, extracted,
or expandable compaction summary and every use of fork/summary support as a general backend-registration
requirement.

## Outcome

Panels has one conversation system. The server is an ACP v1 client, each employee owns one ACP
agent child and durable ACP session, and the browser displays the typed transcript over a thin
Panels websocket. Hermes works first. Codex and Claude become backend definitions rather than new
conversation implementations. Once the ACP route is proven, both non-ACP paths and their feature
branching are removed. Gemini is outside the owner-approved delivery scope.

The user can always see whether an employee is thinking, working, compacting, waiting for a
permission, interrupted, or idle. Thought and tools are closed by default and remain typed after a
refresh. Mid-turn delivery is an explicit Steer, Send Now, or Queue operation with acknowledgement.

## Non-goals

- This is not a new product-data store. Tickets, board state, gates, and other canonical resources
  keep their existing server-owned model and event invalidation.
- This is not a general frontend redesign or a React migration. The UI stays Svelte and uses the
  current Panels tokens, spacing, typography, markdown, image, and managed-preview behavior.
- This does not create a second ACP implementation in the browser. The browser speaks only the
  Panels websocket.
- This does not change Hermes source or run git commands in the Hermes checkout.
- This does not emulate broken backend conformance with timers or flattening.

## System shape

```text
Svelte ACP pane
    │ Panels websocket: route + sequence + ACP update/Panels event
    ▼
ConversationHub ─── TurnBroker ─── PermissionBroker / reverse services
    │                         │
    │ ordered typed updates   └── queue, send-now, steer, compaction policy
    ▼
AcpEmployeeChildRegistry
    │ official Python ACP SDK over stdio
    ▼
AgentBackendDefinition: Hermes | Codex | Claude
```

The existing `EmployeeChildRegistry` remains the proven ownership/lifecycle primitive, but its
Hermes RPC reader is not the ACP protocol seam. The ACP implementation reuses or extracts its
employee-keyed lifecycle, identity environment, durable binding, and teardown behavior behind an
ACP child factory. It does not force the SDK connection into the legacy raw-frame interface.

## Contracts to freeze before implementation

Contracts live under `src/planner/conversation/`. ACP generated SDK types remain the authority for
ACP data; these files define only Panels concepts and adapter boundaries.

### `contracts.py`

- `ConversationEmployee`: employee ID, entity kind/ID, workspace roots, backend key.
- `ConversationSessionBinding`: employee ID, ACP session ID, backend key, binding generation.
- `ConversationActivity`: `connecting | loading | idle | thinking | working | compacting |
  waiting_for_permission | interrupted | failed` plus a human-readable detail and sequence.
- `TurnDeliveryChoice`: `normal | steer | send_now | queue`.
- `TurnDeliveryReceipt`: client message ID, choice, `accepted | queued | started | interrupted |
  rejected`, queue position when applicable, and reason when rejected.
- `QueuedPrompt`: stable client message ID, typed ACP prompt blocks, enqueue order/time.
- `ContextCompaction`: stable boundary ID, `compacting | compacted | failed`, trigger
  `explicit | automatic`, and an exact display-safe reason only when failed. It contains no backend
  context or summary text.
- `ConversationPermissionRequest`: stable Panels request ID, employee ID, ACP session ID, ACP tool
  call ID, requesting backend key, the exact ordered ACP options (`option_id`, label/name, and ACP
  option kind), lifecycle `pending | answered | cancelled`, and deadline. ACP v1 has no separate
  option scope field; Panels must not invent one. It does not invent
  an allow scope or summarize the choices into approve/approve-all/deny.
- `ConversationPermissionOutcome`: stable request ID, exact selected ACP option ID or cancellation
  reason, and first-settlement sequence. Permission state is transient and deliberately not a ticket
  proposal/status.

### `backend_contracts.py`

- `AgentBackendDefinition`: executable argv, explicit environment allowlist/overrides, cwd policy,
  expected agent identity/version, and advertised reverse-service needs.
- `BackendTurnCapabilities`: whether native steer and compaction observation are implemented.
  Queue and Send Now are Panels turn-broker behavior for every conformant backend; unsupported
  steer is visible in the browser and the core does not guess.
- `BackendTurnStrategy`: translate a declared steer operation and observe/normalize compaction.
- `AcpEmployeeChild`: initialize/new/load/prompt/cancel lifecycle and typed update ingress. The
  official SDK connection implements this interface.
- `AcpEmployeeChildFactory`: the injected construction seam used by employee lifecycle ownership and
  scripted test agents.

### `wire_contracts.py`

Server-to-browser messages have one envelope with employee/entity/session IDs and a monotonically
increasing per-session sequence. The payload is exactly one of:

- an ACP `SessionUpdate` serialized from the pinned SDK;
- a Panels activity event;
- a delivery receipt or queue snapshot;
- a context-compaction state/boundary;
- a permission request lifecycle event;
- a terminal-state snapshot wrapping the exact SDK terminal output response;
- a connection/session/reset/error event, including `protocol_update_rejected` with the rejected
  ACP discriminator and a display-safe reason.

Browser-to-server actions are `attach`, `prompt`, `cancel`, `new_conversation`, and
`permission_response`. `prompt` includes a client message ID, ACP content blocks, and a delivery
choice. Panels never puts reasoning inside a message payload.

Every event has a stable schema discriminator and sequence. No generic free-form `data` object is a
public contract. ACP updates are not translated into a second vocabulary.

### Browser contracts

`web/src/lib/acp/contracts.ts` adopts the pinned `acp-components/core` session types and defines
only the Panels envelopes/actions above. `conversationController.ts` owns attach/load/reconnect,
optimistic user echo, sequence/gap handling, typed reduction, and acknowledgements.

The command palette is session state sourced only from typed ACP `available_commands_update`
updates. It never calls the legacy `/api/chat/commands`, reads a private `_acp/skills/list`, or
hard-codes agent slash commands. **Stop** and **New conversation** remain explicit Panels lifecycle
controls outside the slash catalog. `/compact`, `/steer`, and `/queue` appear in the palette only
when the attached agent advertises them; the separate mid-turn delivery controls are governed by
`BackendTurnCapabilities`, not by command-string discovery.

## Runtime invariants

### Ordered typed replay

An SDK callback does only validation and enqueue. One task per employee/session drains updates in
wire order, updates server-side activity/turn bookkeeping, and broadcasts. A browser attach performs
a controlled `session/load`: reset its stream generation, forward the typed replay, then announce
ready. The load response is never treated as transcript data. A partial/unknown replay produces a
`protocol_update_rejected` event, leaves a persistent **Agent sent an unsupported update** status,
and does not silently degrade to text. Raw protocol detail stays in server diagnostics and the
error disclosure; it never becomes an assistant message.

Reconnect is not a new conversation. The durable binding resolver and compare-and-swap persistence
remain the single owner of employee-to-session identity. A browser gap or reload requests a fresh
typed load for that same binding.

### Turn delivery

- `normal`: accepted only when no prompt is active.
- `queue`: Panels appends the prompt to its per-session FIFO, broadcasts a queue snapshot/receipt,
  and starts it automatically only after the active prompt settles. It does not append a database
  chat row and call that delivery.
- `send_now`: Panels cancels the active ACP prompt, awaits settlement/cleanup, emits an interrupted
  receipt and boundary, then starts the submitted prompt immediately.
- `steer`: available only when the backend strategy declares a live-turn steering operation. Hermes
  maps this to its `/steer` behavior. The receipt confirms whether the steer was accepted. A backend
  without steer keeps the control visibly unavailable; it does not silently convert steer to queue
  or cancellation.

The turn broker is the single writer of active-turn and queued-prompt state. Child death rejects or
retains queued prompts only according to one explicit recovery rule frozen in the turn-broker
ticket; it never duplicates a prompt.

### Compaction

Compaction is a first-class Panels conversation event even though ACP v1 lacks it. The backend
strategy returns normalized `ContextCompaction` transitions.

For explicit Hermes `/compact`, the broker emits `compacting` before invoking the agent. Backend
command success plus the controlled durable session transition emits one content-free `compacted`
boundary. A private backend replay marker, when present, is suppressed rather than parsed or rendered;
no marker is also a valid completion. For automatic Hermes compression, the session-provenance update
triggers the same lifecycle. A failed operation remains a visible failed boundary with its exact reason;
no compaction is silent.

Other backend definitions normalize their typed tool/session metadata through their strategy. The
generic broker never checks a backend name.

### Reverse calls and permission

Permission requests are relayed to every attached browser for the owning employee but have one
pending request and one result. The request preserves the agent's exact option IDs, labels, kinds,
tool-call identity, and session identity. The first valid response from any browser still attached
to that employee wins; later responses receive an already-settled receipt. One browser disconnecting
does not cancel a request while another attached browser can answer. A request with no attached
browser is rejected immediately, and the last attached browser disconnecting cancels it. A five
minute response timeout lives in conversation configuration. Panels may remember a session-scoped
option only when the agent supplied that option; it never invents a broader grant. Timeout, prompt
cancel, child death, new conversation, and shutdown deny/cancel the outstanding request exactly once.

Filesystem service paths are absolute, normalized, and confined to the employee's declared
workspace roots. Terminal handles are scoped to employee + ACP session + terminal ID and are
cleaned on release, child death, new conversation, and shutdown. The latest bounded terminal output,
exit, and release snapshot is emitted through the typed Panels terminal-state envelope and replayed
after session load so a terminal tool remains legible without becoming transcript prose. Reverse services are composed only
for a backend that requests them, and Panels advertises only services that are fully present.

### Product/runtime boundary

The ACP websocket does not emit canonical resource invalidations and is not consumed by the keyed
ticket/board cache. Employee-step execution uses an `AcpStepGateway` through the existing
framework-neutral `runtime/step_gateway.py` contract, so automatic work and human chat share the
same employee child/session without making the product-visible `chat_messages` table into worker
context. Any product turn records still needed for step settlement are written explicitly at that
boundary, not used as transcript replay.

## Browser component inventory

Keep the inventory small and merge components when separation has no behavioral value:

```text
web/src/lib/acp/
  contracts.ts
  panelsTransport.ts
  conversationController.ts

web/src/components/acp/
  AcpConversationPane.svelte
  TranscriptView.svelte
  ThoughtView.svelte
  ToolCallCard.svelte       # includes terminal/tool-state variants
  DiffView.svelte
  PlanView.svelte
  PermissionPrompt.svelte
  AcpComposer.svelte        # includes queue chips and delivery choices
  ConversationStatus.svelte # includes live compaction and streaming state
```

A plain content-free `Context compacted` row can live in `TranscriptView`; usage can live in the
status line; command search can live in the composer. Compaction is never expandable because Panels
does not receive or expose backend context. These become separate components only if their state or
tests prove that useful.

Reuse `MarkdownBlock`, managed previews, chat image mechanics, route/right-rail sizing, and existing
tokens. Ordinary messages remain cardless. Thought and completed tools are compact disclosure rows,
closed by default even while streaming. Permission is prominent because it blocks work, but it does
not reuse the canonical ticket `ApprovalBlock` writer. Status is one persistent textual line rather
than decorative chrome. acp-ui's styling is not a donor.

## Migration graph

Each ticket receives frozen contracts, named acceptance tests, allowed files, and prohibited files.
Implementation agents do not change the contracts. Each ticket follows plan review → implementation
→ implementation-diff review. Integration is serial where composition overlaps.

### ACP-00 — contracts and conformance harness

Freeze the Python and TypeScript contracts above; pin the Python SDK and typed browser donor; add a
scripted ACP agent and serialization/conformance fixtures. This ticket has no production route and
no visual UI. Pin `agent-client-protocol==0.11.0` in the Python requirements. Vendor only the exact
framework-free `acp-components/core` revision
`525a9d83c5ace577ac0417bf82bf983da4042663` under `web/src/vendor/`, including its upstream license
and a provenance/readme file; do not add the stale npm package or fork its ACP unions.

The scripted harness freezes all ten backend registration probes from `research.md`:

1. typed thought replay completes before `session/load` returns;
2. live and replayed thought never becomes assistant text;
3. grouping works both with stable message IDs and the deterministic missing-ID turn fallback;
4. plan snapshots replace and tool updates reconcile by tool-call ID;
5. permission cancel/death/disconnect/timeout settlement occurs exactly once;
6. browser refresh loads the same durable employee session without binding drift;
7. every backend either declares a real steer strategy or exposes steer as unavailable, while the
   common broker proves Queue and Send Now independently of backend vendor commands;
8. explicit/automatic compaction yields visible started/completed lifecycle, or exact failure, without
   exposing backend context;
9. callback concurrency still reduces and broadcasts in wire order;
10. unknown or partial replay raises `protocol_update_rejected` and never text fallback.

This is the only ticket allowed to define public contract shapes. Later contract corrections return
to the orchestrator as a new explicit decision rather than being improvised in implementation.

### ACP-01 — generic ACP child runtime + Hermes definition

Implement the SDK connection, stderr drain, ordered ingress, employee child lifecycle, durable
session binding, attach/new/load, and the Hermes backend definition. Reuse the employee-keyed
registry lifecycle where its ownership contract fits; extract the ownership mechanism rather than
coupling ACP to Hermes raw frames. No websocket or Svelte files.

Acceptance includes lifecycle, crash/rebind, stale-generation rejection, typed Hermes-shaped replay,
load capture, overload/slow-consumer behavior, and shutdown tests against scripted ACP children.

### ACP-02 — turn broker, compaction, and reverse services

Implement active-turn ownership, FIFO queue, send-now cancellation, declared steer strategies,
normalized compaction, permission broker, and capability-gated filesystem/terminal services. No
routes or frontend files.

Before implementation, the ticket must freeze the one child-death queue recovery rule and exact
permission timeout/attached-browser ownership rule. Acceptance covers every receipt transition,
cancel race, queue single-delivery, Hermes explicit and automatic compaction capture, permission
exactly-once settlement, path confinement, terminal cleanup, and shutdown.

### ACP-03 — typed browser state and Panels-native Svelte pane

Implement the pinned reducer corrections, Panels transport/controller, and the restrained component
inventory. Use fixture websocket streams only; do not wire backend composition. Frontend tests cover
live and replayed thought, refresh, tools, line diffs, terminal state, plan replacement, usage,
commands, persistent activity, compaction states, queue chips and receipts, explicit mid-turn choices,
permission options, reconnect gaps, and unknown updates.

This ticket can be planned in parallel with ACP-01/02 after ACP-00 is frozen because its files do not
overlap. Its visual acceptance is Panels-native restraint plus the observed Zed legibility, not an
acp-ui restyle.

### ACP-04 — Hermes vertical cutover

Compose one conversation hub and one websocket route, replace the Chief and Ticket conversation
panes with the ACP pane, and implement `AcpStepGateway` so automatic steps and humans share the same
employee ACP child. Attach and refresh use typed load. `new_conversation`, cancellation, images,
commands, permissions, and status cross the real route.

The cutover replaces the active path directly; it does not add a third long-lived feature flag.
Legacy code remains physically present but unreachable until proof. Route-level ownership tests
assert one child/session owner and no canonical cache invalidations from conversation traffic.

The step cutover is accepted only if the existing `EmployeeStepRunner` correctness model remains
green through the new gateway:

- `on_session_key` performs the existing compare-and-swap claim before the ACP child/session is
  published as the Ticket's binding, and a lost claim cannot run or settle another Ticket;
- the product worker-turn record is attached to exactly that session and remains visibility/runtime
  settlement state only—its database row is never sent to the worker as context;
- complete, errored, interrupted, busy, child-death, and lost-claim outcomes settle both the worker
  turn and Ticket status exactly once;
- restart recovery uses the stored ACP session with `require_existing_session` and cannot mint a
  replacement silently;
- shutdown interrupts the matched active ACP prompt within the existing deadline and leaves no
  Ticket stranded at `agent_running_step`;
- worker permission/clarification resolution validates the active worker-turn session against the
  Ticket's current employee session before answering.

### ACP-05 — Hermes dogfood and correction slice

Run the real Panels UI through computer use against Hermes. Exercise at minimum:

- live thought followed by browser refresh with thought still collapsed and typed;
- tool, line diff, plan, terminal, usage, command palette, and image prompt;
- queue, queued-message editing/clearing if supported, send-now interruption, and Hermes steer;
- explicit compact plus an automatic-compression fixture, live compacting status, and a plain
  completed boundary with no backend context;
- each permission option, waiting status, reject, disconnect, cancel, and child-death cleanup;
- child crash/restart, reconnect, new conversation, Chief, Ticket, and automatic employee step.

Record screenshots/observations and convert every correction into a focused acceptance test before
the fix. The slice is done only when a user can identify every state transition without inspecting
logs.

### ACP-06 — legacy deletion and documentation

Only after ACP-05 passes, the orchestrator first writes
`orchestration/acp-migration/chat-ownership-classification.md`. It inventories every `chat/`
contract, table, migration, event kind, API route, resource, image helper, clarification path, and
runtime caller, and marks each **delete**, **retain as product worker-step settlement**, or **move to
conversation/files** with named destination tests. ACP-06 cannot be cut until that artifact has an
independent review and no unclassified use remains.

Then delete the manual raw-frame transports, relay/correlation/translator,
neutral vocabulary/routes/tee/pool-step gateway, legacy `minds/gateway.py` conversation transport,
`SharedGateway`/legacy session-manager conversation code, `neutralPane.ts`, `ChiefNeutralPane`,
`ChatPanel`, route/capability branches, legacy API helpers/types/resources/events, and obsolete tests.

Delete human conversation transport. Retain or move only the explicitly classified product
worker-step settlement and managed-image responsibilities. They cannot read or replay transcript,
submit a worker prompt, or act as a second conversation client. Remove obsolete config flags and
dependencies. Update the system docs in the same change. Prove by repository search and boundary
tests that there is exactly one conversation route, client, transcript reducer, and child/session
composition path, and that each retained chat symbol has only its classified callers.

### ACP-07 — Codex backend definition

Add the pinned Codex command/environment/capability definition and backend-specific compaction/steer
strategy only where required. Add one generic Ticket `employee_backend` selection whose allowed keys
come from the registered definitions. Each Worker type declares a `default_employee_backend`; a new
Ticket starts with that value already selected, and the Kickoff stage lets the user override it before
Kickoff approval. The accepted Ticket value is the backend used for its durable conversation binding,
Ticket chat, and every Automatic Employee step. Pass the common conformance suite and prove both a
real computer-use Ticket conversation and one actual Automatic Employee step through
`EmployeeStepRunner`. A definition file or test-only substitution without that operable selection
path is incomplete. The control is generic and restrained; no backend-specific UI branching.

### ACP-08 — Claude backend definition

Add the pinned Claude command/environment/capability definition. Register it through the same
Ticket/Worker-type backend-assignment seam, pass the common conformance suite with the exact adapter's
truthful reverse capabilities (`filesystem=False`, `terminal=False`, `permission=True`), and prove
both a real computer-use Ticket conversation and one actual Automatic Employee step through
`EmployeeStepRunner`. Claude's internal Bash remains visible typed tool output; it is not mislabeled as
a Panels reverse terminal. Again, one registration point and no backend-specific UI; a definition file
alone is not a functional worker backend.

### ACP-09 — removed from delivery

The owner removed Gemini from this program on 2026-07-20. Do not implement, register, gate, test, or
document Gemini as a Panels backend in this migration.

### ACP-10 — final audit and verification

Independently review the settled code against the owner brief, this program, and the ACP contracts;
spot-check ordered ingress, session binding, turn cancellation/queue single delivery, permission
settlement, compaction capture, and planning/runtime composition. Address or explicitly refute every
finding. Run the complete computer-use matrix once more, then one canonical `./verify` on the frozen
tree and retain its full output and digest. Update `PROGRESS.md`, `decisions.md`, and `docs/` to the
single ACP end state.

## Integration order and concurrency

1. ACP-00 is serial and blocks all implementation.
2. After its contracts pass independent review, ACP-01 and ACP-03 may be planned/implemented in
   parallel because their files are disjoint. ACP-02 begins only once ACP-01's child lifecycle plan
   is frozen; integration remains ACP-01 then ACP-02 then ACP-03.
3. ACP-04 is serial composition work.
4. ACP-05 must pass before ACP-06 deletes anything.
5. ACP-07 and ACP-08 may implement their distinct backend definitions in parallel after ACP-06. The
   shared Worker-type default, Ticket Kickoff selection, persistence, and registration changes are one
   serial generic slice; each backend then proves its own real Ticket and Automatic Employee step.
   ACP-09 has no work.
6. ACP-10 runs after all settled changes. No agent runs `./verify` concurrently with another writer.

## Proof ledger

Each ticket directory records:

- frozen contract and acceptance tests;
- implementation plan;
- full independent plan review and dispositions;
- implementation report;
- full independent diff review and dispositions;
- focused test evidence.

`./verify` remains the sole repository-completeness claim. Computer use supplies the additional
experiential claim required by this brief. Neither substitutes for the other.
