# ACP-03 implementation plan

## Boundary and dependency state

This slice builds the unmounted browser subject only. It consumes Python-emitted fixture websocket
streams and adds no route, backend composition, database state, product-resource invalidation, or
legacy-pane edit. The browser remains a Panels websocket client; it never becomes an ACP JSON-RPC
client. The implementation stays inside the paths allowed by `contract.md`, does not change a
dependency version or `web/package-lock.json`, and does not touch the Hermes checkout.

ACP-00a's `terminal_state` correction is a prerequisite and is treated as frozen here. The browser
will import its exact `TerminalOutputResponse` from the ACP SDK, retain terminal output as tool state,
and never turn it into assistant text.

### Steer capability disposition

ACP-00b adds required `supportsSteer` to the existing connection payload. Retain that exact boolean in
browser connection state and use it as the only source for the separate mid-turn Steer control. The
implementation must not parse `connection.detail`, inspect a backend key, or treat an advertised
`/steer` command as capability evidence. Queue and Send Now remain common broker choices.

## Files and ownership

Create the following framework-free modules:

- `web/src/lib/acp/panelsTransport.ts` — injected socket, closed Panels-envelope validation, and
  typed send/open/close/error callbacks.
- `web/src/lib/acp/conversationState.ts` — the sole pure conversation transition and immutable
  snapshot types.
- `web/src/lib/acp/conversationController.ts` — socket lifetime, sequence/generation admission,
  actions, reconnect, subscriptions, and disposal.
- `web/src/lib/acp/lineDiff.ts` — a separately testable Myers line-diff algorithm. This extra helper
  earns its file because diffing is non-trivial pure logic and does not belong in Svelte markup or
  the conversation state machine.

Create exactly the nine contracted Svelte components under `web/src/components/acp/`. Do not add a
generic content, usage, command-palette, queue-chip, or compaction component. Modify only the donor
`sessionStore.ts` and `UPSTREAM.md`, the ACP fixture/test/support files named below, and the `test`
script in `web/package.json`.

## Donor correction: one transcript implementation, not two

`conversationState.ts` will not copy the donor's message/tool/plan algorithms and will not use the
global Zustand store as hidden mutable state. Refactor the existing helpers in
`web/src/vendor/acp-components-core/src/store/sessionStore.ts` into exported pure functions while
retaining the donor store API as a thin delegate for compatibility:

- export `SessionData` and `createSessionData`;
- export immutable helpers to append content, append thought, upsert/patch a tool, replace the one
  plan part, and toggle thought/tool expansion;
- take the already-selected message ID and timestamp as arguments instead of calling `Date.now()` or
  `generateId()` inside the pure helpers;
- make a new thought part explicitly `{ expanded: false }`, and never reuse the content-part helper
  for thought;
- concatenate only adjacent unannotated text blocks in the same content part. Image, audio,
  resource, thought, tool, and plan parts terminate the text run, so later text starts a new block or
  part in its true wire position;
- preserve a tool's existing `content`, `locations`, and `expanded` value when a
  `tool_call_update` omits those keys; a present `content`/`locations` field replaces the collection,
  with explicit `null` normalized to an empty collection;
- assign `expanded: false` to every new tool, including active tools. A user toggle is preserved
  through later patches;
- store the stable plan-part owner/message ID in `SessionData`. The first `plan` adds one typed plan
  part; every later full snapshot replaces that part and `SessionData.plan`, including an empty
  snapshot, instead of appending another message.

`conversationState.ts` is then the only dispatcher over `SessionUpdate.sessionUpdate`; it calls the
pure donor helpers. The retained Zustand actions are primitives, not another ACP-envelope reducer,
and production ACP code never imports the donor singleton.

Update `UPSTREAM.md` in the same change. Keep the upstream commit and original hash table, add the
post-correction SHA-256 for `src/store/sessionStore.ts`, and enumerate the exact local changes above:
pure exported helpers/injected identity and time, closed thought default, text-run flush, stable plan
replacement, partial tool reconciliation, and expansion preservation. No other vendored file is
changed.

## Immutable browser state and pure transition

### Snapshot shape

`conversationState.ts` exports a readonly `ConversationSnapshot` with these named sections:

- attached `employeeId` and an optional admitted cursor containing `entityKind`, `entityId`,
  `acpSessionId`, `bindingGeneration`, and `sequence`;
- `session: SessionData`, whose `messages`, `ToolCallState`, plan, usage, and available commands use
  only donor/SDK types;
- `timeline`, a closed reference union—not a parallel message type—of `messageId`,
  `compactionBoundaryId`, `deliveryClientMessageId`, and `protocolRejectionSequence`;
- current activity, connection state/detail/`supportsSteer`, a recoverable connection error, and
  persistent protocol-rejection state;
- typed non-transcript metadata: current mode ID, exact current config options, and the latest exact
  `SessionInfoUpdate`;
- compactions keyed by `boundaryId`, each wrapping the frozen payload plus UI-only `expanded`;
- delivery receipts keyed by `clientMessageId`, FIFO queue snapshot, and optimistic-human metadata
  keyed by `clientMessageId` and pointing to a donor message ID;
- permissions keyed by Panels `requestId`, preserving the frozen request and a UI-only
  `submittingOptionId`;
- terminal snapshots keyed by `terminalId`;
- reducer-only fallback bookkeeping: current turn ordinal, current typed role, fallback segment
  ordinal, and active missing-ID group.

All arrays, records, maps, donor messages, parts, and tools are replaced on write. The reducer keeps
internal donor `SessionData`; it is never the public session value. After each transition, build and
cache a recursively readonly/frozen `ConversationSnapshot` projection: clone/freeze plain SDK/donor
values and readonly arrays, and project `pendingToolCalls` to a frozen keyed record rather than
exposing its mutable `Map`. `snapshot()` and subscriptions return that cached projection. Compile-time
readonly proof plus a runtime cast-and-mutate test must show a consumer cannot alter controller state
or a later snapshot. Panels-only timeline rows contain only their named key—never generic `data`—and
resolve the current typed value from its keyed section at render time.

### Reducer inputs and injected dependencies

Define one closed `ConversationTransition` union:

- `{ kind: "server_envelope"; envelope }` after controller admission;
- local connection/open/close/browser-envelope-error transitions;
- optimistic prompt, thought/tool/compaction disclosure toggles, and permission-response-pending;
- session reset and disposal cleanup.

`reduceConversationState(previous, transition, dependencies)` is pure. Dependencies are
`now(): number` and `fallbackId(turnOrdinal, role, segmentOrdinal): string`. Tests inject fixed time
and the canonical `fallback-${turn}-${role}-${segment}` formatter. Production composition can inject
`Date.now`; neither donor helpers nor Svelte invent IDs.

### Message grouping and ordered parts

For each typed user/message/thought chunk:

1. Derive typed role `user` for `user_message_chunk`, otherwise `agent`. Thought remains a distinct
   part even though its grouping role is `agent`.
2. On a role change, clear the active missing-ID group. Entering `user` from another role advances
   the turn ordinal; an agent-first replay initializes turn one. An optimistic local human prompt
   performs the same turn advance.
3. If ACP supplies `messageId`, use that exact value as the donor message key and clear the active
   fallback. Repeated chunks with the same exact ID target the same donor message.
4. If it omits `messageId`, reuse the active fallback only while the typed role remains the same and
   no tool/plan boundary occurred. Otherwise increment the segment ordinal and call the injected
   fallback-ID function. Thought followed directly by agent message therefore shares the agent
   message but remains a separate thought part.
5. Reduce every chunk immediately. Adjacent unannotated text can coalesce in the last content block;
   no text buffer survives a non-text content block or another typed part.
6. A tool or plan update first clears the missing-ID group. It appends its typed part to the current
   agent message when one exists, or creates one injected synthetic agent message and one timeline
   reference. A later missing-ID agent chunk starts a new segment after that boundary.

Live and replay streams use this identical path. Assistant-text evidence is collected only from
`content` parts of agent messages; thought parts can never enter it.

### ACP update fold

Use one exhaustive switch in `conversationState.ts`:

- `user_message_chunk` / `agent_message_chunk` call donor content helpers;
- `agent_thought_chunk` calls only the donor thought helper with `expanded: false`;
- `tool_call` upserts by `toolCallId`; `tool_call_update` patches only own fields, never creates a
  second ID, and creates a calm unsupported row if an update names no existing tool;
- `plan` replaces the one full plan snapshot/part;
- `available_commands_update` replaces the exact SDK command array;
- `usage_update` replaces current usage;
- `current_mode_update` replaces the typed current mode ID; `config_option_update` replaces exact
  config options through the pure donor helper; `session_info_update` replaces the latest exact typed
  partial metadata. They create no transcript row/status and never become prose. The unstable
  `plan_update` / `plan_removed` variants remain visibly **Unsupported agent content** for this slice;
  they do not mutate the canonical full-snapshot plan.

The reducer remains exhaustive at compile time with a `never` check. A runtime discriminator that
escaped the shallow SDK boundary is also represented as unsupported state, never assistant text.

### Panels envelope fold

- `activity` replaces current activity and never clears a persistent protocol/gap error.
- `context_compaction` upserts one keyed row. `compacting`, `compacted`, and `failed` update that
  same row; completed/failed rows default closed and preserve an explicit user toggle.
- `delivery_receipt` upserts one stable receipt row. Accepted/queued/started/interrupted/rejected are
  rendered from the exact current receipt. Rejected optimistic text remains in its donor message.
- `queue_snapshot` atomically replaces the ordered queue with no local reorder or merge.
- `permission_request` upserts only its matching `requestId` and retains option order/kind/labels.
  `permission_outcome` removes/settles only that ID. Connection close/reset clears local blocking
  permission UI so a dead request cannot remain actionable; a later replayed request can reopen it.
- `human_echo` locates `clientMessageId`. If optimistic metadata exists, replace/confirm that donor
  message's exact prompt blocks and do not append. If it does not, create one donor user message and
  reference. A matching receipt updates metadata without adding a second message.
- `terminal_state` replaces the latest snapshot for `terminalId` because ACP's
  `TerminalOutputResponse.output` is already the output captured so far. Preserve output,
  `truncated`, optional SDK `exitStatus`, and `active | released` exactly.
- `protocol_update_rejected` appends one keyed disclosure row and sets the persistent fixed status
  **Agent sent an unsupported update** plus the display-safe reason. It never adds a donor message.
- `connection` updates orientation. A reset uses the controller's dedicated reset transition before
  this payload is reduced. Its required `supportsSteer` replaces the current capability exactly.

Raw tool input/output uses a pure `safeDisplayText(unknown)` helper: strings remain verbatim;
otherwise use indented JSON, falling back to a fixed **Unsupported agent content** label if safe
serialization fails. No `innerHTML` is used.

## Transport algorithm

`createPanelsTransport` takes `{ url, socketFactory, onOpen, onEnvelope, onInvalidEnvelope, onClose,
onError }` and returns `{ open, send, close }`. The socket interface contains only `send`, `close`,
and the four event callbacks so tests can use a small fake.

- `open()` constructs one socket once; controller reconnect creates a fresh transport instance.
- Incoming data must be a string. Parse once to `unknown`; binary, invalid JSON, or invalid envelope
  calls `onInvalidEnvelope` and never calls `onEnvelope`.
- Validate exact outer keys and primitives: wire version `1`, one of the eleven frozen
  discriminators, non-empty identity strings, entity kind, positive integer generation/sequence,
  and an object payload. Reject unknown outer fields.
- Validate every Panels-owned payload's exact keys, literals, and required scalar/array fields.
  For SDK-owned values (`SessionNotification`, prompt, permission request/response, terminal output)
  validate only the containing Panels fields, object presence, required discriminator/session
  identity, and the properties the reducer must safely read. Do not copy or deep-redeclare the SDK
  generated union. Inner prompt/notification session IDs must equal the outer session.
- `send(action)` serializes only the imported frozen `BrowserAction` union and returns
  `{ ok: true } | { ok: false; reason: string }`; it never throws for an unopened/closed socket.
  There are no JSON-RPC methods or backend conditionals.
- Transport never schedules reconnects. It reports closure/error exactly once for its socket epoch;
  controller owns deterministic timers.

## Controller algorithm

### Construction and public API

`createConversationController` takes employee ID, transport factory, required injected
`reconnectDelayMs`, deterministic timer functions, time/ID dependencies, and optional initial state.
It exposes:

- `attach()`, `snapshot()`, `subscribe(listener)`, and `dispose()`;
- `prompt(blocks, deliveryChoice)`, `cancelActive()`, `cancelQueued(clientMessageId)`,
  `newConversation()`, and `respondToPermission(requestId, optionId)`;
- `setThoughtExpanded`, `setToolExpanded`, and `setCompactionExpanded` local UI actions.

The pane supplies content blocks; the controller injects the admitted `acpSessionId` into the exact
SDK `PromptRequest`. Client-message IDs come from an injected factory. Controller methods never
upload files or call a legacy API.

### Attach, identity, generation, and sequence

1. A newly constructed controller has no cursor. `attach()` opens one socket. On open it sends
   `{ type: "attach", employeeId }` with no last-seen values.
2. An in-page reconnect retains the last admitted cursor and sends both
   `lastSeenBindingGeneration` and `lastSeenSequence`. At most one reconnect timer and one current
   socket epoch may exist.
3. Before a cursor exists, only a matching-employee `connection: reset` may establish entity,
   session, generation, and sequence. Any other first envelope fails closed, publishes a browser
   connection error, closes that socket, and reconnects without a guessed cursor.
4. For the current generation, require exact employee/entity/session identity and
   `sequence === last + 1`. Duplicate/lower sequences and lower generations are ignored without
   reducing. A wrong employee/entity/session is a fail-closed connection error.
5. A forward sequence gap publishes persistent **Conversation updates were missed. Reconnecting…**,
   marks the socket epoch blocked, closes it, and reconnects from the last admitted cursor. Later
   callbacks from the bad epoch are ignored.
6. A higher generation is accepted only when the envelope is `connection: reset` and its
   `resetBindingGeneration` equals the outer generation. Its positive sequence becomes the new
   cursor, session-scoped state is cleared exactly once, and pending permission UI is rejected
   locally. A higher-generation non-reset fails closed. A same-generation reset is admitted only at
   the exact next sequence and performs the same one reset, supporting a controlled reload without
   inventing a generation.
7. After admission, the single server-envelope transition writes the admitted cursor and payload
   state atomically, then publishes once. No rejected envelope can advance the snapshot cursor.

### Actions and acknowledgements

- `prompt` first creates one optimistic donor user message keyed by `clientMessageId`, with delivery
  state pending, then sends the exact action. Idle sends use `normal`; active-turn sends use the
  explicit selected `steer | send_now | queue` value. Local send failure marks that same row rejected
  and keeps its text.
- A matching human echo or receipt updates the same optimistic record. It never appends a duplicate.
- `cancelQueued(id)` sends only `{ type: "cancel", queuedClientMessageId: id }`; the chip stays until
  the next authoritative queue snapshot. `cancelActive()` sends cancel without that field.
- `newConversation()` sends the frozen action and waits for a reset; it does not clear the current
  transcript optimistically.
- `respondToPermission` accepts only a currently pending request and an exact option ID from its
  ordered agent options. It marks only that request/option pending to disable double submission and
  waits for the matching outcome to remove it.

### Close, invalid input, and disposal

Socket close/error changes connection orientation and clears stale permission buttons, then schedules
one reconnect unless disposed. Invalid JSON/envelope uses the separate recoverable status
**Conversation data could not be read. Reconnecting…**, closes the epoch, and is never sent to the
reducer as an agent update. Gap/invalid status stays during replacement attach/replay and clears only
when `connection: ready` is admitted at the exact next sequence on that replacement socket epoch;
blocked/late epochs cannot clear it. `protocol_update_rejected` remains persistent and distinguishable
with its frozen agent status.

`dispose()` sets a permanent flag before closing, clears the reconnect timer, detaches all transport
callbacks, clears subscribers and local permission submission state, and prevents every late socket
or timer callback from reducing or publishing. Public actions become no-ops/typed local failures.
There are no unresolved action promises; any internal receipt waiters introduced during
implementation must be settled with a disposed result and tested.

## Component responsibilities, props, and events

### `AcpConversationPane.svelte`

Props: `controller: ConversationController`, `employeeLabel: string`. The passed controller's
lifetime is transferred to the pane: mount subscribes and calls `attach`; destroy unsubscribes and
disposes. It owns no URL or backend selection.

It renders the existing `chat-panel` / `chat-thread` geometry, one `ConversationStatus`, one
`TranscriptView`, the current `PermissionPrompt` inset, and one `AcpComposer`. It forwards controller
methods and the snapshot connection's exact `supportsSteer` as props; it never interprets envelopes.

Copy the proven `ChatPanel` follow-scroll behavior exactly: existing 48px near-bottom threshold,
upward wheel/scroll leaves follow mode, reaching the bottom re-enters it, and a **Latest** button
restores it. A render revision covering message parts, tools, terminal snapshots, compaction,
disclosures, and status schedules one post-`tick` scroll only when following. Toggling a disclosure
while scrolled back must preserve the reader's scroll position.

### `TranscriptView.svelte`

Props: `timeline`, `session`, `compactions`, `receipts`, `protocolRejections`, and `terminalStates`;
events: `onThoughtExpanded(messageId, partIndex, expanded)`, `onToolExpanded(toolCallId, expanded)`,
and `onCompactionExpanded(boundaryId, expanded)`.

Resolve timeline references against current state. Render user content with existing `chat-u`, agent
text as cardless `chat-a`, and typed parts in original order. Text always uses `MarkdownBlock`.
Resource links use `targetFromHref` + `FilePreview`; embedded text uses `MarkdownBlock`; image/audio
blocks use safe typed media elements. A future or malformed content discriminator produces an
**Unsupported agent content** row. Compaction and protocol errors are inline disclosure rows, not
messages.

### `ThoughtView.svelte`

Props: exact donor thought part, `messageId`, `partIndex`; event: `onExpanded(expanded)`. Render a
native button labelled **Thinking**, `aria-expanded`, `aria-controls`, and a stable controlled region.
Default comes from reducer state and is always closed for live/replay/streaming thought. Thought text
uses `MarkdownBlock` only inside the open region; no thought string is passed to an assistant-content
renderer.

### `ToolCallCard.svelte`

Props: `tool: ToolCallState`, `terminalStates`; event: `onExpanded(expanded)`. Despite the retained
contract name, presentation is a compact disclosure row, not a visual card. The closed label includes
typed title, kind, and textual status. Completed/failed/new calls are closed; updates preserve user
state.

The open region renders typed `content`, delegates diffs to `DiffView`, and for a terminal reference
looks up the keyed `terminal_state`: terminal ID, captured output, running/exited/signal/released,
and truncation text. Raw input/output has a second explicit disclosure using `safeDisplayText`.
Unknown content is the calm fixed unsupported row.

### `DiffView.svelte`

Props: `path`, optional `oldText`, and `newText`. It calls `lineDiff.ts` and renders old/new line
numbers plus a visible blank/`−`/`+` marker. Every row has an accessible Context/Deleted/Added label;
addition/deletion remains understandable with colors disabled. `oldText == null` is a new file, so
all new lines are additions.

`lineDiff.ts` implements deterministic Myers shortest-edit-script diff: split on newline while
preserving a final empty line, advance the furthest `x` for each diagonal at edit distance `d`, store
the trace, and backtrack with deletion-before-addition tie breaking. Coalesce equal lines as context;
never compare whole blocks or paint the entire old/new files red/green.

### `PlanView.svelte`

Prop: exact `readonly PlanEntry[]`. Render the one current snapshot as a compact ordered list. Each
row prints status and priority as text as well as any subtle token color, so no state is color-only.
There is no plan history or duplicate plan card.

### `PermissionPrompt.svelte`

Props: exact keyed pending request and `submittingOptionId`; event:
`onSelect(requestId, optionId)`. Render one prominent blocking inset with the tool title and ordered
agent options. Each option is a real button with its exact label, `data-permission-kind`, pending and
disabled state. Do not auto-focus the first action, invent scope, collapse options to approve/deny,
or call a Ticket writer. The group is labelled/described and status changes are announced politely.

### `AcpComposer.svelte`

Props: `commands`, `queue`, `receipts`, `activityState`, `supportsSteer`, `employeeLabel`; events:
`onPrompt(contentBlocks, choice)`, `onCancelActive()`, `onCancelQueued(clientMessageId)`, and
`onNewConversation()`.

Wrap the existing `ChatComposer` rather than copying its slash matching, image selection, draft, and
keyboard mechanics. Adapt only the current ACP `AvailableCommand[]` into its structural catalog:
prefix each advertised name with `/` for display/send, preserve exact description, use no skills or
hard-coded backend entries, and never import the legacy command API/resource catalog. Transform
selected image `File[]` into exact ACP image content blocks in-browser; there is no upload/API call.
Stop and New conversation are separate lifecycle buttons outside that catalog.

When activity is active, show Steer / Send Now / Queue as three real `aria-pressed` delivery buttons
above the composer. Queue is the visible initial selection, matching the observed Zed behavior; the
selected label remains visible when sending, and the keyed receipt confirms the actual result. Steer
is disabled with calm explanatory text when `supportsSteer` is false; no inference is allowed. Idle
sends are `normal` and hide the mid-turn selector.

Render queue chips in authoritative FIFO order with the exact text blocks and typed non-text labels
from each pending prompt; each chip has only Cancel. Show the latest keyed accepted/queued/started/
interrupted/rejected receipt in an `aria-live="polite"` line. No queued prompt is silently cleared.

### `ConversationStatus.svelte`

Props: connection, activity, usage, active compactions, pending permission, and persistent error.
Render exactly one persistent textual `role="status"` line. Precedence is persistent protocol/gap/
browser error, connecting/loading/closed, waiting for permission, compacting, current activity, then
idle. Append `used / size tokens` and optional exact cost in quiet text. There is no usage ring,
status card, gradient, icon row, or second accent.

## Visual, token, and accessibility rules

All ACP-specific CSS remains local to the nine new components and uses only variables already in
`assets/tokens.css`. Reuse existing `chat-*`, markdown, file-preview, composer, and right-rail classes
where their behavior fits. Add no hex/rgb/hsl value, fixed spacing/radius/font/duration, gradient,
shadow, decorative animation, or copied acp-ui/React class. Ordinary messages remain cardless; only
the permission request uses a bordered raised inset. Tool/thought/compaction use plain rows and
hairline structure.

Every disclosure uses a native button, visible label, `aria-expanded`, and controlled region. Global
focus-visible styling remains intact; component styles must not remove outlines. Status/receipts use
polite live regions and connection/protocol failures use an alert only when newly raised. Diff signs,
plan labels, tool status, and terminal exit text prevent color-only meaning. Add no new animation;
the existing reduced-motion rules therefore need no asset edit.

## Fixture and ACP-00 conformance integration

Extend `tests/support/acp_fixture_writer.py` so Python frozen models emit these deterministic files:

- `browser-live-replay-v1.json`: reset-first live and fresh-controller replay streams with the same
  user/thought/message/tool/diff/terminal/plan/usage/command semantics, including Hermes-shaped
  missing message IDs;
- `browser-envelope-states-v1.json`: ordered coverage of all eleven server envelope discriminators,
  both explicit/automatic compaction transitions, all five receipt states, two FIFO queue snapshots,
  optimistic/matching human echo, ordered permission options and outcomes, running/exited/truncated/
  released terminals, raw content, and visible protocol rejections;
- `browser-controller-cases-v1.json`: reset, exact-next, duplicate, lower, stale generation, wrong
  identity, forward gap, higher-generation reset/non-reset, and display-safe invalid-envelope cases.

Valid values must be constructed from the Python contracts and `serialize_params`. Invalid cases are
separately named raw dictionaries and never masquerade as valid models. The fixture check compares
canonical bytes; no hand-maintained ACP union appears in JavaScript.

Add `tests/support/acp_browser_conformance.py` only as an adapter to the existing ACP-00 machinery.
It reads JSON evidence produced by the actual browser subject, constructs the existing
`ThoughtEvidence`, `MessageGroupingEvidence`, `PlanToolEvidence`, and
`ProtocolRejectionEvidence`, and calls the existing probe assertions for IDs 2, 3, 4, and 10. It
also applies ACP-00's existing `mutate_probe_evidence` once per probe and requires the corresponding
assertion to fail. It defines no new probe or lookalike assertion.

## Test files and exact assertions

### `web/tests/acp-browser-state.test.mjs`

Bundle the real production TS modules to a temporary ESM entry with the installed Vite API, then
drive fake sockets and deterministic timers through transport → controller → reducer. Assert:

- strict text/outer/payload validation, one socket, exact five outbound action shapes, and no
  reduction of invalid JSON;
- live/replay view-state equivalence, exact/stable and missing-ID grouping, thought closed and absent
  from all agent content text, text/tool/plan ordering, plan replacement, tool-ID patch behavior,
  expansion preservation, and command/usage replacement;
- terminal snapshot replacement and running/exit/signal/truncated/released state; real diff operation
  rows; raw serialization and unsupported rows;
- typed mode/config/session-info replacement without transcript/error state; activity/status
  persistence, stable explicit/automatic compaction boundaries, all receipt states,
  authoritative FIFO queue replacement/cancel, optimistic echo dedupe and visible rejection;
- exact permission order/kinds, one pending send, matching-only outcome, disconnect/reset cleanup;
- fresh attach versus reconnect cursor, duplicate/lower/stale rejection, wrong identity, sequence-gap
  blocked epoch/reconnect, successful-ready recovery clearing only recoverable errors, reset-only
  generation advance, invalid envelope distinction, immutable public snapshot projection, and late
  callback/timer silence after dispose.

### `web/tests/acp-browser-conformance.test.mjs`

Drive the same actual subject with the canonical streams, derive the four ACP-00 evidence objects
from donor messages/parts/tools/plan/protocol rows, and pass them to
`acp_browser_conformance.py`. The adapter must report the settled subject passes and each existing
targeted mutation fails only its corresponding assertion.

### `web/tests/acp-browser-components.test.mjs`

Compile all nine Svelte files with the installed Svelte compiler and assert the exact inventory and
contracts: `MarkdownBlock`/`FilePreview`/`ChatComposer` reuse; no route/backend construction; closed
thought/tool/compaction state; `aria-expanded`/controls; permission option keyed iteration and exact
callback; persistent status; three explicit delivery choices, receipts, queue cancel, terminal/raw
disclosure, and diff line markers/labels. Scan local styles for existing token variables and reject
hard-coded color/spacing/radius/font/duration values, gradients, shadows, card grids, acp-ui, and
React styling.

### Existing ACP contract test and source boundaries

Extend `web/tests/acp-contracts.test.mjs` rather than creating a second ownership test. Assert:

- all eleven server and five browser discriminators remain structurally sourced from
  `contracts.ts`, including exact SDK terminal output;
- exactly one ACP `SessionUpdate` switch exists in `conversationState.ts`, the controller is its only
  production caller, and no Svelte component folds wire events;
- ACP production sources contain no legacy chat/API/resource-cache/neutral-pane import or strings
  `/api/chat/commands`, `_acp/skills/list`, backend-specific catalog, JSON-RPC, acp-ui, or React;
- ACP unions remain SDK-owned and donor message/tool/plan types are imported rather than redeclared;
- only the permitted donor file differs from its upstream hash and every correction plus the local
  hash is present in `UPSTREAM.md`;
- `web/package-lock.json` is unchanged and `web/package.json` adds only the three ACP tests to the
  existing test chain.

## Acceptance-to-test map

| Named acceptance | Proof |
| --- | --- |
| 1. Pure state/controller and all ordering/lifecycle behavior | `acp-browser-state.test.mjs` over Python-emitted streams and actual fake transport |
| 2. ACP-00 probes 2/3/4/10 plus mutations | `acp-browser-conformance.test.mjs` + `acp_browser_conformance.py` calling existing assertions |
| 3. Minimal accessible restrained component inventory | `acp-browser-components.test.mjs`, Svelte diagnostics, and production build |
| 4. One reducer/controller and clean source boundaries/provenance | extended `acp-contracts.test.mjs` |
| 5. Focused gates and handoff | commands below, focused implementation review, then orchestrator integration after ACP-01/02 |

## TDD and implementation order

1. Confirm ACP-00a/00b contracts and fixtures are settled.
2. Extend the Python fixture writer and commit canonical reset-first streams; make byte comparison and
   TS structural checks fail first.
3. Add transport tests for closed validation and socket events, then implement `panelsTransport.ts`.
4. Add reducer tests in this order: message/thought grouping, text boundaries, plan, tools,
   terminal/raw/unsupported, commands/usage, compaction/receipts/queue, optimistic echo, permission.
   Refactor/correct donor helpers and provenance only as each reducer assertion demands.
5. Add controller tests for attach, cursor admission, reset, gaps, reconnect, actions, and disposal;
   implement `conversationController.ts` against the already-green pure reducer.
6. Test and implement the Myers helper before `DiffView`.
7. Add the nine Svelte components from leaves upward: Diff/Plan/Thought, Tool, Permission/Status,
   Transcript, Composer, Pane. Keep all visual rules in component-local token CSS.
8. Wire the actual browser evidence adapter to the existing ACP-00 assertions and mutation machinery.
9. Add the source-boundary/provenance checks and append the three tests to `web/package.json`.
10. Run the focused commands once the slice is settled; do not run `./verify`, mount a route, or edit
    a legacy test in this ticket.

## Focused commands

```sh
.venv/bin/python tests/support/acp_fixture_writer.py
node web/tests/acp-contracts.test.mjs
node web/tests/acp-browser-state.test.mjs
node web/tests/acp-browser-conformance.test.mjs
node web/tests/acp-browser-components.test.mjs
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
```

Record full outputs in this ticket's focused evidence. A different sub-agent performs one focused
plan review before implementation and one different sub-agent performs the implementation review.
Use a second round only if the steer disposition or a concrete implementation blocker remains.
