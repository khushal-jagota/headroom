# ACP-03 — typed browser state and Panels-native conversation pane

Contract-scoped frontend ticket for `orchestration/acp-migration/plan.md`. ACP-00 is the frozen wire,
SDK-type, fixture, and donor foundation. Read the owner brief, `research.md`, the reviewed program,
ACP-00's contract/reviews, `PRINCIPLES.md`, `AGENTS.md`, and the frontend skill before planning. This
ticket builds against fixture websocket streams only. It does not add or change a backend route,
compose an ACP child, mount the pane in a product route, edit the legacy panes, or delete anything.

## Outcome

Panels has one framework-free browser controller/reducer for the frozen ACP websocket envelope and
one unmounted Svelte conversation pane that renders every required typed state. Live and load-replayed
thought remain typed, collapsed, and absent from assistant text. Tools, line diffs, terminal output,
plan, usage, commands, compaction, queue/send receipts, permission, reconnect gaps, and unsupported
updates are legible. The visual language remains Panels: restrained, cardless, typographic, and based
only on existing design tokens. acp-ui contributes behavior only; none of its styling is copied.

## Visual, content, and interaction thesis

- **Visual thesis:** the existing Panels chat rail with stronger typed hierarchy—ordinary messages
  stay cardless; thought and completed tools are compact disclosure rows; only a permission request
  becomes a prominent blocking inset.
- **Content plan:** transcript is the primary surface, a single persistent status line provides
  orientation, the composer owns commands/delivery/queue, and secondary tool/plan/usage detail stays
  progressively disclosed. There is no hero, dashboard grid, inspector, usage ring, or explanatory
  banner.
- **Interaction thesis:** fast disclosure toggles reveal thought/tool detail; explicit
  mid-turn Steer/Send Now/Queue choices produce visible receipts; reconnect/error transitions and
  queue changes are apparent without decorative motion. Existing focus-visible and reduced-motion
  conventions apply.

Zed is the affordance/legibility bar. Panels tokens, typography, spacing, markdown, managed previews,
right-rail geometry, and `ChatComposer` behavior are the presentation source. Do not widen this into a
route redesign or use the frontend skill's landing-page/imagery defaults on this operational pane.

## Frozen type ownership

- `web/src/lib/acp/contracts.ts` remains the only Panels envelope/action declaration. ACP
  `SessionNotification`, content, tool, plan, usage, command, prompt, and permission types stay
  imported from `@agentclientprotocol/sdk==1.2.1`.
- Browser transcript/message/tool state uses the pinned `acp-components/core` donor types. Do not
  introduce a parallel `ChatMessage`, flattened event vocabulary, generic `data`, or browser JSON-RPC
  client.
- Corrections to the vendored donor source are permitted only when this contract names them. Record
  every modified upstream file and exact correction in `web/src/vendor/acp-components-core/UPSTREAM.md`.
  Do not vendor additional donor React/client/platform files.
- The fixture server envelope is the boundary. Runtime validation must fail closed on invalid common
  envelope fields/discriminators. ACP payloads already passed the strict server validator; the
  browser may use the SDK's exported types but must not copy or deep-redeclare its generated schemas.

## Framework-free modules

Implement under `web/src/lib/acp/`:

1. **`panelsTransport.ts`.** A minimal injected WebSocket adapter opens the one Panels socket, sends
   only frozen `BrowserAction` JSON, parses text messages, validates the closed Panels envelope, and
   reports open/close/error. It has no ACP JSON-RPC methods, resource-cache imports, route logic, or
   backend-name knowledge. Tests use a fake socket and deterministic timers.
2. **`conversationState.ts` (or an equally descriptive single reducer module).** One pure state
   transition owns transcript grouping, tools, plan, usage, commands, activity, compaction entries,
   delivery receipts, queue snapshot, permission state, connection/error state, and optimistic human
   echo. It reuses/corrects the donor state helpers rather than implementing a second reducer beside
   them. Time and fallback-ID generation are injected for deterministic tests.
3. **`conversationController.ts`.** Own attach/reconnect, generation/sequence admission, gap handling,
   optimistic sends, acknowledgements, delivery actions, queue cancellation, new conversation,
   permission response, subscriptions, and disposal. It does not call legacy chat/commands APIs,
   upload files, or mutate canonical resource caches.

The controller exposes one immutable snapshot/subscription interface consumed by Svelte. Svelte does
not fold wire events itself. Internal donor `Map`/mutable arrays are never exposed: the public
snapshot is a cached recursively readonly/frozen projection with records/readonly arrays at
collection boundaries. Consumer mutation cannot alter controller state or a later snapshot.

## Reducer corrections and typed transcript rules

- `agent_thought_chunk` always creates/extends a thought part, never agent-message content. Thought
  disclosure defaults closed for live and replay updates and remains closed while streaming.
- `agent_message_chunk` and `user_message_chunk` group by exact ACP `messageId` when supplied.
  Missing-ID fallback is deterministic, scoped to one turn and role, and resets at typed
  role/tool/plan boundaries. Live and replay use the same rule.
- Adjacent unannotated text blocks may concatenate for rendering. A non-text update flushes the text
  run first, so tool/plan/thought ordering cannot leap ahead of visible text.
- `plan` is a full snapshot. The current plan and its one transcript part are replaced, not appended
  on every update. Plan status/priority remain typed.
- Tool calls reconcile by `toolCallId`; updates replace only fields present while preserving existing
  content/locations and UI-only expansion. Completed/failed tools default closed. Active tool state
  stays compact and readable. Raw input/output is shown only in an explicit disclosure with safe text
  serialization.
- Tool `content` renders typed content blocks; `diff` renders real line-by-line old/new content with
  additions/deletions legible without color alone; `terminal` renders terminal ID plus accumulated
  output/exit state supplied in typed updates. Unsupported raw/tool content produces a calm visible
  **Unsupported agent content** row, never silent omission and never assistant prose.
- Usage is current session/turn state in the status line, not a ring or standalone dashboard widget.
- Stable `current_mode_update`, `config_option_update`, and `session_info_update` replace typed
  non-transcript session metadata. Normal Hermes metadata never creates unsupported content,
  assistant prose, or a protocol-rejection status. Truly unknown/partial updates still fail closed;
  unstable plan-delta variants may remain visibly unsupported while the full-snapshot `plan` path is
  canonical in this ticket.
- Available commands replace the session command list from
  `available_commands_update`. The palette never calls `/api/chat/commands`, `_acp/skills/list`, or a
  hard-coded backend catalog. Stop and New conversation remain lifecycle controls outside the list.
- Context compaction creates one stable non-interactive boundary keyed by `boundaryId`; live
  compacting, completed, and failed states update that row. It shows only lifecycle and the trigger;
  an exact failure reason is inline when present. Backend context is never rendered or expandable.
- Permissions are keyed by Panels `requestId`, preserve exact ordered agent options/labels/kinds,
  and settle/remove only from the matching outcome. Disconnect/reset/cancel outcomes clear stale
  blocking UI. The browser never invents a permission scope or writes a Ticket proposal.
- Human echo is keyed by `clientMessageId`. A later matching server echo/receipt updates the existing
  optimistic row rather than duplicating it. Rejection remains visible and does not erase the text.
- Queue snapshots replace queue state. Queued chips show the exact pending prompt in FIFO order and
  allow only the frozen cancel action. Receipt state makes accepted/queued/started/interrupted/
  rejected delivery legible.

## Sequence, reset, reconnect, and errors

- The controller admits only the attached employee/session identity. Same-generation sequence must
  increase exactly by one; duplicate, lower, wrong-identity, and stale-generation events cannot
  mutate transcript state.
- A higher binding generation is accepted only through a matching `connection: reset` envelope. It
  clears transcript/session-scoped state once, rejects pending permission UI, and then admits the new
  generation. A new generation arriving through any other event fails closed.
- A sequence gap sets persistent visible connection/error status, stops reducing later events, closes
  the bad socket, and reconnects with the last admitted generation/sequence. It never guesses across
  the gap or converts unknown payload to text.
- Browser refresh starts an empty controller and sends `attach` with no last-seen values. In-page
  reconnect retains the last admitted generation/sequence and sends them on attach. Server fixture
  replay then rebuilds the same typed thought/tool/plan state without flattening.
- `protocol_update_rejected` preserves the fixed status **Agent sent an unsupported update** plus the
  display-safe reason in a disclosure. It is not an assistant message. Invalid browser-side envelope
  JSON uses a separate calm connection error and is never reduced.
- Dispose closes the socket, cancels reconnect timers, settles local pending callbacks, and prevents
  late socket events from publishing another snapshot.
- Gap/invalid-envelope errors are recoverable connection state, distinct from persistent protocol
  rejection. They remain through reconnect/replay and clear only when a `connection: ready` envelope
  is admitted contiguously on the replacement socket epoch. A ready envelope on the failed epoch or a
  new gap cannot clear them; protocol rejection is unaffected.
- The separate mid-turn Steer control is enabled only from the connection payload's required
  `supportsSteer` boolean. Queue and Send Now remain common; command-catalog entries and backend names
  are never capability evidence.

## Svelte component inventory

Create only components that carry distinct behavior:

```text
web/src/components/acp/
  AcpConversationPane.svelte
  TranscriptView.svelte
  ThoughtView.svelte
  ToolCallCard.svelte       # tool state + terminal variants
  DiffView.svelte
  PlanView.svelte
  PermissionPrompt.svelte
  AcpComposer.svelte        # command search + queue chips + delivery choices
  ConversationStatus.svelte # activity + usage + compaction/error orientation
```

Merge or omit a listed child when the plan proves it has no independent state/behavior. Do not add
components for usage, command palette, compaction row, queue chip, or generic content unless tests
prove separation is necessary.

- `AcpConversationPane` receives an already-created controller/employee label; it does not build a
  production URL or choose a backend. It follows the existing chat-thread scroll rule: autoscroll
  only while the reader is following; disclosure/live updates preserve real scrollback.
- Reuse `MarkdownBlock` for text, existing managed-preview/file-preview helpers for links/resources,
  and `ChatComposer` mechanics where their contract fits. Do not duplicate markdown rendering,
  image-file preparation, slash matching, or follow-scroll behavior.
- Ordinary human/agent messages have no cards. Thought and tool disclosures are compact rows with
  native buttons, clear labels, `aria-expanded`, focus-visible state, and closed defaults. Permission
  options are real buttons in exact order with pending/answered/disabled states.
- Status is one persistent textual line: connecting/loading/idle/thinking/working/compacting/
  waiting/interrupted/failed. Do not add decorative status cards, gradients, shadows, ornamental
  icons, or multiple accent systems.
- Use existing tokens only. New hard-coded colors, spacing scales, radii, motion durations, or fonts
  are forbidden. Any truly missing token requires orchestrator approval before implementation.

## Fixture-driven proof

Extend ACP fixtures with ordered live/replay streams for every frozen server-envelope variant and
Hermes-shaped missing-ID updates. Tests drive the actual transport → controller → reducer path, not a
hand-shaped result. Required scenarios:

- live and fresh-controller replay produce equivalent typed transcript state;
- replayed thought is closed and absent from every assistant text block;
- message fallback, text/non-text flush, plan replacement, tool reconciliation, real diff rows,
  terminal/raw unsupported state (including the typed `terminal_state` envelope), usage, and command
  replacement;
- persistent activity, explicit/automatic compaction transitions, all receipt states, FIFO queue
  replacement/cancel, optimistic echo dedupe/rejection, permission options/outcome/disconnect;
- correct attach/reconnect actions, duplicate/stale/wrong identity rejection, sequence-gap reconnect,
  reset-only generation advance, unknown/partial update status, and dispose cleanup.

The production browser subject must pass ACP-00 conformance probes 2, 3, 4, and 10. Targeted
mutations for thought flattening, fallback drift, plan append/tool-ID mismatch, and text fallback must
fail their respective assertions.

## Allowed files

- `web/src/lib/acp/**`
- `web/src/components/acp/**`
- `web/src/vendor/acp-components-core/src/store/sessionStore.ts`
- `web/src/vendor/acp-components-core/UPSTREAM.md`
- `web/tests/acp-*.test.mjs`
- `web/package.json`
- `tests/fixtures/acp/**`
- `tests/support/acp_*.py` only for fixture emission/conformance-subject wiring
- `orchestration/tickets/acp-03-browser-state-pane/**`

No dependency version changes are allowed. The plan may choose fewer files; any additional path needs
orchestrator approval and a written reason before implementation.

## Must not touch

Python production code, routes, `core/server.py`, database/schema/migrations, runtime composition,
current `chat/`, `minds/`, or `hermes_backend/`, legacy frontend clients/panes/components/tests,
product route files, `App.svelte`, `assets/app.css`, `assets/tokens.css`, existing shared component
implementations, `web/package-lock.json`, docs, config, `verify`, or the Hermes checkout. Do not mount
the ACP pane yet and do not edit an existing non-ACP test to pass.

## Named acceptance

1. Pure controller/reducer tests cover all reducer, ordering, generation, reconnect, optimistic echo,
   queue, compaction, permission, command, usage, error, and disposal rules above through fixture
   transport streams.
2. Mutation/conformance tests prove ACP-00 probes 2, 3, 4, and 10 fail for their corresponding broken
   production subject and pass for the settled implementation.
3. Component contract tests plus Svelte diagnostics/build prove the minimal inventory, closed
   disclosures, exact permission option order, persistent status, explicit delivery choices/receipts,
   queue cancellation, real line diff, terminal/unsupported-content disclosure, accessibility
   attributes, existing-token-only styling, and absence of acp-ui/React/card-grid styling.
4. Source-boundary tests prove one ACP reducer/controller, no legacy command/resource-cache/API
   imports, no copied SDK unions, no hard-coded backend command catalog, and every donor correction
   recorded in `UPSTREAM.md`.
5. Exact focused web tests, `npm --prefix web run check`, and `npm --prefix web run build` pass. One
   independent sub-agent reviews the implementation once; the orchestrator integrates ACP-03 only
   after ACP-01/02 and runs the canonical `./verify` then.

## Review and integration

One implementation sub-agent writes a concrete plan/acceptance map before source edits. A different
sub-agent performs one focused plan review against this contract, ACP-00, donor code/provenance, the
reference observations, and current Panels components. After disposition, an implementation
sub-agent works only the allowed files; a different sub-agent performs one implementation review.
Use a second review round only for a concrete unresolved blocker or a material correction.

The orchestrator spot-checks thought isolation, missing-ID fallback, sequence/reset logic, permission
settlement, donor provenance, and visual restraint. ACP-03 can be implemented in parallel with
ACP-01/02 because files are disjoint, but it integrates after ACP-01 then ACP-02. Real route and
computer-use validation remain ACP-04/05.
