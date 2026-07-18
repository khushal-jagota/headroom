# Hermes relay S2a — neutral vocabulary + Hermes translator implementation plan

Built on landed S1 (`hermes-relay-s1-foundation`, commit `052a968`). Bound by
`orchestration/tickets/hermes-relay-s2a-neutral-translator/contract.md`. Adds a **neutral
downstream mode** to the S1 relay plus a Hermes translator, a mediated-command surface, a
transcript-mirror tee consumer, and the S1-trigger revisits (bounded queues,
final-frame-vs-death). Adds nothing the contract does not ask for.

The three contract-vs-reality collisions this plan raised were ruled by the orchestrator and the
contract was amended (2026-07-18). This plan is revised to the amended contract:
- **Scope (ruled `D-only-free-hermes-features`, 2026-07-18 — supersedes the earlier model/command
  design below):** the neutral surface is the necessary conversation set (attach/history, send,
  clarify-answer, approval-response, interrupt, event stream) PLUS `compact` (→ `session.compress`)
  and `list catalog` (→ `commands.catalog`, payload AS-IS). Model selection AND generic mediated
  command execution are CUT as nice-to-haves (stock seams recorded in the contract, not built): there
  is no `SetModelRequest`, no `model.options`, no `RunCommandRequest`/`command.dispatch`, no
  `mediated_command_catalog.py`, no typed/normalized model/catalog result events. The catalog result
  is a single payload-as-is `CatalogResultEvent`.
- **Send-while-running:** a mid-turn send is LEGAL — stock `prompt.submit` queues (and by default
  interrupts) rather than rejecting (`_handle_busy_submit`, `server.py:8420-8425`). No synthetic busy
  error; the native sequence flows through translation as-is. The 4009 mapping stays as a harmless
  completeness case. (Handback note for S3: the step runner's busy-guard cannot rely on Hermes
  rejecting concurrent prompts — queue-not-reject changes settlement assumptions.)
- **Tee marker:** text-only mirror via existing `planner.chat` write functions, call-only, no schema
  change, no derived marker. Distinguishing mirrored rows is S4's problem
  (`D-transcript-ownership-open`); for the Chief the cutover moment separates the eras.

The "Collisions raised and their rulings" section at the end retains the verified evidence; note its
model/command rulings were later SUPERSEDED by `D-only-free-hermes-features` (which cut those
features entirely) — retained only for the source evidence.

## 0. How the neutral mode attaches (the central seam decision)

The S1 relay routes downstream text frames through `EmployeeChildRelay.handle_downstream_message`,
discriminating on a top-level `"relay"` key with verbs `subscribe`/`request` that carry
**native** inner frames verbatim (`employee_child_relay.py:246-289`). Raw verbatim mode
(`relay:"request"`/`relay:"subscribe"`) must stay byte-for-byte unchanged — S1 tests and the tee
depend on it, and the 9-method `SESSION_LIFECYCLE_DENYLIST` stays enforced in both modes.

**Decision: the translator is a per-downstream-connection adapter that owns its own WebSocket
route and sits BETWEEN the neutral socket and the *same* `EmployeeChildRelay`, calling the
relay's existing public downstream API.** It does not modify the relay's raw path and does not add
a mode-flag branch inside `handle_downstream_message`.

Concretely, the neutral mode is a NEW route (`relay_neutral_route.py`) that:
- On connect, calls `relay.register_downstream()` to get a `DownstreamConnection` (same object raw
  mode uses) — reusing S1 fan-out, id-namespacing, denylist, tee, and death synthesis unchanged.
- Wraps that connection in a `NeutralDownstreamSession` (translator) that:
  - **Inbound (pane→relay):** parses a neutral *request* off the neutral socket, translates it to
    one or more native JSON-RPC frames, and injects each by constructing the S1
    `{relay:"request", employee_entity_id, frame}` envelope and calling
    `relay.handle_downstream_message(conn, envelope_text)` — so every native forward still flows
    through the S1 denylist and id-namespacing untouched. Subscription is done once via a
    synthesized `{relay:"subscribe", ...}` on attach.
  - **Outbound (relay→pane):** drains `conn.outbound` (the queue the relay puts both fanned-out
    child events AND this connection's correlated RPC responses onto), translates each native frame
    to a neutral event, and writes neutral JSON to the socket. Correlated native responses to
    translator-issued native requests (e.g. `commands.catalog`, `session.history`, `session.compress`)
    are consumed by the translator itself and turned into the matching neutral event, not blindly
    echoed.
    **How the two are told apart (load-bearing, verified against `deliver_child_frame`,
    `employee_child_relay.py:367-411`):** when the translator issues a native request through the
    `{relay:"request", frame:{id:<D>, ...}}` envelope, the relay registers a `PendingForward` keyed
    to THIS `conn` and, when the child answers, restores the downstream id `<D>` and enqueues the
    response to ONLY this `conn.outbound` (the correlated branch, lines 381-401 — NOT fanned out to
    other subscribers). Genuine child events are uncorrelated and fanned out to subscribers (lines
    392-408). So the translator assigns each RPC it issues a downstream id from its OWN monotonic
    counter and keeps a small `pending_translator_rpcs: dict[downstream_id, RpcKind]`; a frame
    arriving on `conn.outbound` with `id` in that dict is its own RPC result (consume + translate to
    the matching neutral event, drop the entry), everything else is a child event to translate. This
    is exactly the id-correlation the S1 relay already performs — the translator is just the
    downstream that issued the request, so no relay change is needed.

Why an adapter over the connection rather than a mode branch inside the relay: the relay's raw
contract (verbatim frames, id rewrite, denylist, fan-out, tee) is exactly what the translator
needs underneath; re-implementing routing would duplicate S1's concurrency-critical core. The
translator interprets *native payload semantics* (which the S1 relay deliberately never does), so
it belongs OUTSIDE the relay, layered on its public seam. This keeps `core = zero module-specific
conditionals` (PRINCIPLES core/module contract): the relay gains no `if neutral:`.

The neutral socket's outbound must be drained by the translator, not the raw writer loop. Because
`register_downstream()` hands back a `conn` whose `outbound` is a plain `asyncio.Queue`, the
neutral route runs its own writer loop reading `conn.outbound`, translating each item, then
`websocket.send_text`. (S1's `relay_route.relay_downstream_websocket` sends `conn.outbound` items
verbatim; the neutral route is a sibling that translates instead. Both share
`register_downstream`/`unregister_downstream`.)

## 1. Module / file layout under `src/planner/hermes_backend/`

All NEW files. Framework-free neutral vocabulary lives in a contracts module that imports nothing
Hermes-specific (PRINCIPLES pure-logic + contracts-first). The translator may import Hermes frame
names.

| File | One-line purpose |
|---|---|
| `neutral_vocabulary.py` | **Framework-free** typed neutral event + request dataclasses and their round-trippable `to_wire()`/`from_wire()` serialization. Imports only `dataclasses`, `enum`, `typing`, `json`, `__future__` — nothing under `planner.hermes_backend.hermes_frame_translation`, `planner.minds`, or any Hermes name. This is the ACP-shaped contract future Claude/Codex translators implement. |
| `hermes_frame_translation.py` | **Pure functions**: `native_frame_to_neutral_event(employee_entity_id, frame) -> NeutralEvent` (frame→event table §3) and `neutral_request_to_native_frames(request) -> list[NativeRequestPlan]` (request→RPC table §3). No I/O, no relay, no sockets — unit-testable with zero mocks. Imports `neutral_vocabulary` + Hermes params-type/method string constants defined here. |
| `neutral_downstream_session.py` | The per-connection translator/adapter (`NeutralDownstreamSession`): binds one `DownstreamConnection`, drives inbound neutral-request translation into S1 `relay:"request"` envelopes, outbound native→neutral translation, history-snapshot-on-attach, catalog listing (payload as-is), and per-request correlation of translator-issued native RPCs. Holds the bounded outbound policy (§6). (No `mediated_command_catalog.py` — generic command execution is cut per `D-only-free-hermes-features`.) |
| `relay_neutral_route.py` | The neutral FastAPI WS route (sibling to `relay_route.py`): accept → `register_downstream` → construct `NeutralDownstreamSession` → run its writer + receive loops → `unregister_downstream`. Same auth posture and teardown discipline as `relay_route.py`. |
| `transcript_mirror_tee.py` | The `RelayTeeObserver` implementation (mirror tee consumer §5): observes turn completions/failures, appends transcript rows write-behind off the conversation path via a worker thread that owns its own DB connection, swallows+logs failures. Calls `planner.chat.data` write functions only. |
| `neutral_relay_config.py` | The isolated configuration module for the neutral-relay tunables (PRINCIPLES: "Anything intended to be tuned later ... lives in an isolated configuration module, not inline"): `NEUTRAL_OUTBOUND_MAX_FRAMES`, `TOOL_PREVIEW_MAX_CHARS`, and the tee's work-queue bound. Named `Final` constants only; no logic. |

Config surface for tuning (PRINCIPLES: tunable limits live in an isolated configuration module, not
inline): the neutral outbound queue bound (`NEUTRAL_OUTBOUND_MAX_FRAMES`), the tool preview bound
(`TOOL_PREVIEW_MAX_CHARS`), and the tee work-queue bound live in `neutral_relay_config.py`. Modules
import them from there — none are inline literals. (Finding #9 from the plan review: PRINCIPLES is
explicit that tunables live in an isolated module; a two-line "not worth a file" judgment does not
override a standing rule.)

## 2. The full typed neutral vocabulary (`neutral_vocabulary.py`)

Design rules enforced mechanically: every event carries `employee_entity_id: str`; a discriminated
union serialized with a `kind` tag; round-trippable via `to_wire()`/`from_wire()`; nothing
Hermes-specific imported. Frozen dataclasses; explicit fields only (no speculative fields — every
field below is demanded by the contract's enumerated list or by a verified native payload it must
carry).

### Events (server → pane)

Every event dataclass has `employee_entity_id: str` as its first field. `kind` is a
`NeutralEventKind` enum value; the wire form is
`{"neutral":"event","kind":<value>, "employee_entity_id":..., ...fields}`.

| Dataclass | Fields (beyond `employee_entity_id`) | Contract line / native source |
|---|---|---|
| `TurnStartedEvent` | — | `message.start` |
| `AssistantTextDeltaEvent` | `text: str` | `message.delta {text}` |
| `ThinkingDeltaEvent` | `text: str` | `thinking.delta`/`reasoning.delta {text}` |
| `ToolActivityEvent` | `tool_id: str`, `tool_name: str`, `phase: ToolPhase` (`started`/`progress`/`completed`), `preview: str` (bounded, see below) | `tool.start`/`tool.complete` |
| `AgentQuestionEvent` | `request_id: str`, `prompt_text: str`, `choices: tuple[str, ...]` | `clarify.request {question, choices, request_id}` |
| `ToolApprovalRequestEvent` | `request_id: str`, `summary: str` | `approval.request {request_id, ...}` |
| `TurnCompletedEvent` | `final_text: str` | `message.complete {text}` with `status=="complete"` |
| `TurnFailedEvent` | `reason: TurnFailureReason`, `detail: str` | `error {message}`; `message.complete` with `status in {error, interrupted}`; native `4009` (see §3 + collisions) |
| `SessionTitledEvent` | `title: str` | `session.title {title}` |
| `HistorySnapshotEvent` | `messages: tuple[NeutralHistoryMessage, ...]` | translated `session.history {messages}` (NOT `session.resume` — see history note §3) |
| `ChildResetEvent` | — | S1 synthesized `{"relay":"event","type":"child_reset"}` |
| `CatalogResultEvent` | `payload_json: str` | the result of `commands.catalog` delivered PAYLOAD AS-IS (ruled `D-only-free-hermes-features` 2026-07-18 — one read, the runtime's payload verbatim, NOT normalized). `payload_json` is the native `commands.catalog` `result` serialized. The S2b pane renders its picker from this payload; picking a SKILL inserts the skill's trigger into the composer as ordinary message text (skills are model-side; no execution machinery). This is the ONLY listing result — model options are CUT. |
| `PassthroughEvent` | `native_type: str`, `payload_json: str` | any UNKNOWN native `params.type` EVENT frame with no dedicated neutral event — opaque-but-visible, never dropped. Reserved for unknown native frames only. |

Supporting types (also in `neutral_vocabulary.py`, all framework-free):
- `class ToolPhase(enum.Enum)`: `started`, `progress`, `completed`.
- `class TurnFailureReason(enum.Enum)`: `agent_error`, `busy_already_running`, `interrupted`,
  `child_reset`. `busy_already_running` maps the legacy native-4009 case (a COMPLETENESS mapping,
  amended contract — stock relay children queue-not-reject on a mid-turn send, so 4009 is not the
  normal busy path but the mapping is kept and testable); `interrupted` maps a `message.complete
  status=="interrupted"` (which a queued mid-turn send's default interrupt of the prior turn
  produces); `child_reset` is emitted from the S1 death→`child_reset` path when a turn was in
  flight.
- `class NeutralHistoryMessage`: `role: str` (`user`/`assistant`/`tool`/`system`), `text: str`,
  `tool_name: str | None`. Mirrors the `_history_to_messages` output returned by `session.history`
  (`server.py:7822`): tool rows carry a name/context, text rows carry text.
(No normalized model/catalog sub-types: the catalog is delivered payload-as-is in `CatalogResultEvent`
per `D-only-free-hermes-features`, and model options are cut — so there is nothing to normalize.)
- `preview` bound: `TOOL_PREVIEW_MAX_CHARS` (in the neutral-relay config module, §1). `started`
  preview = the native `context` string (`tool.start payload["context"]`, `server.py:3599`);
  `completed` preview = the native `summary` when present else a bounded render of `result`
  (`tool.complete payload["summary"]`, `server.py:3625-3627`). Truncation is deterministic
  (`preview[:TOOL_PREVIEW_MAX_CHARS]`), asserted with a specific value in tests.

**The catalog result is payload-as-is; model options are cut (ruled `D-only-free-hermes-features`,
2026-07-18).** The amended contract keeps only the necessary conversation set plus `compact` and
`list catalog`, and CUTS model selection and generic mediated command execution as nice-to-haves
(stock seams recorded in the contract, not built). So: the `commands.catalog` result the translator
consumes is delivered PAYLOAD AS-IS in a dedicated `CatalogResultEvent` (a distinct typed event so it
is not confused with an unknown-native `PassthroughEvent`, but carrying the runtime's payload
verbatim — no normalization). There is NO `model.options` request/event and NO
`ModelOptionsResultEvent`. There is NO `RunCommandRequest`/`command.dispatch` mediated execution and
NO `MediatedCommandCatalog` normalizer/vetting module. The only listing is `list catalog`.

### Requests (pane → server)

Wire form `{"neutral":"request","kind":<value>, ...fields}`. `kind` is a `NeutralRequestKind` enum.

| Dataclass | Fields | Maps to (native, §3) |
|---|---|---|
| `AttachToEmployeeRequest` | `employee_entity_id: str` | synthesize `relay:"subscribe"` + issue `session.history` for the history snapshot (see history note below — NOT `session.resume`, which is denylisted and already consumed by the pool at spawn) |
| `SendMessageRequest` | `employee_entity_id: str`, `text: str`, `image_refs: tuple[str, ...]` | `image.attach`* then `prompt.submit {session_id, text}`. No model/effort fields (amended contract): stock `prompt.submit` carries neither; model selection is CUT (`D-only-free-hermes-features`). |
| `AnswerQuestionRequest` | `employee_entity_id: str`, `request_id: str`, `answer: str` | `clarify.respond {session_id, request_id, answer}` |
| `RespondToApprovalRequest` | `employee_entity_id: str`, `request_id: str`, `decision: str`, `apply_to_all: bool` | `approval.respond {session_id, request_id, choice, all}` |
| `InterruptRequest` | `employee_entity_id: str` | `session.interrupt {session_id}` |
| `CompactRequest` | `employee_entity_id: str` | `session.compress {session_id}` (`server.py:7854`; a native `command.dispatch` builtin too; rejects mid-turn `4009` natively) |
| `ListCatalogRequest` | `employee_entity_id: str` | `commands.catalog {session_id}` → `CatalogResultEvent` with the payload AS-IS |

### Serialization (area 1)

- `to_wire(event_or_request) -> dict` and `from_wire(dict) -> NeutralEvent | NeutralRequest`,
  dispatching on `neutral` + `kind`. `tuple` fields serialize to JSON lists and rebuild to tuples.
  `PassthroughEvent.payload_json` is a pre-serialized JSON string so any native payload round-trips
  without the neutral layer parsing it.
- Round-trip law asserted in tests: `from_wire(to_wire(x)) == x` for every dataclass, with a
  specific instance per kind.

### The import-graph assertion (area 1, mechanical)

A test asserts the transitive import set of `planner.hermes_backend.neutral_vocabulary` contains no
module whose name starts with `planner.hermes_backend.hermes_frame_translation`, `planner.minds`,
or `tui_gateway`. Concretely: import the module, and assert its own module-level imports are a
subset of `{dataclasses, enum, typing, __future__, json}`. Same "completeness test scans and FAILS
on drift" discipline S1 used for `KNOWN_TUI_GATEWAY_SESSION_METHODS`.

## 3. Translator tables (`hermes_frame_translation.py`)

### Frame → event (native `params.type` → neutral event)

Dispatch on `frame["params"]["type"]` for native event frames
(`{"jsonrpc":"2.0","method":"event","params":{"type":..., "session_id":..., "payload":{...}}}`,
verified at `server.py:1143-1147`). Employee identity is supplied by the relay's per-frame
`employee_entity_id` label (the translator already knows which child produced the frame — `conn`
is subscribed per employee), so identity rides EVERY event regardless of payload.

| Native `params.type` | Neutral event | Payload fields read |
|---|---|---|
| `message.start` | `TurnStartedEvent` | (payload may be absent — `server.py:8890` emits id-less) |
| `message.delta` | `AssistantTextDeltaEvent` | `payload.text` |
| `thinking.delta` | `ThinkingDeltaEvent` | `payload.text` |
| `reasoning.delta` | `ThinkingDeltaEvent` | `payload.text` |
| `reasoning.available` | `ThinkingDeltaEvent` | `payload.text` (thinking finished; folded into thinking-delta per S0 table — no separate "thinking finished" event in the contract's enumerated list) |
| `tool.start` | `ToolActivityEvent(phase=started)` | `payload.tool_id`, `payload.name`, `payload.context` |
| `tool.generating` | `ToolActivityEvent(phase=progress)` | `payload.name` only (`server.py:3861` emits `{name}`, no tool_id/context — `tool_id`/`preview` are `""`); this is the `progress` phase the contract names |
| `tool.complete` | `ToolActivityEvent(phase=completed)` | `payload.tool_id`, `payload.name`, `payload.summary`/`payload.result` |
| `clarify.request` | `AgentQuestionEvent` | `payload.request_id`, `payload.question`, `payload.choices` |
| `approval.request` | `ToolApprovalRequestEvent` | `payload.request_id`, redacted `payload.command`/summary |
| `message.complete` (`status=="complete"` or absent) | `TurnCompletedEvent` | `payload.text` |
| `message.complete` (`status in {"error","interrupted"}`) | `TurnFailedEvent(reason=agent_error/interrupted)` | `payload.text`, `payload.status` |
| `error` | `TurnFailedEvent(reason=agent_error)` | `payload.message` |
| `session.title` | `SessionTitledEvent` | `payload.title` |
| `session.info` | `PassthroughEvent(native_type="session.info", payload_json)` | S0-inventoried metadata frame (`s0-spike.md:34`); no dedicated neutral event in the contract's enumerated list, so it maps to passthrough EXPLICITLY (tested as an exact row, not left to the fallthrough) |
| `status.update` | `PassthroughEvent(native_type="status.update", payload_json)` | lifecycle/compaction status (`server.py:1165-1178`); explicit passthrough row |
| S1 `{"relay":"event","type":"child_reset"}` | `ChildResetEvent` | (synthesized by `deliver_child_death`) |
| **any other `params.type`** | `PassthroughEvent(native_type, payload_json)` | whole `payload` serialized opaque — nothing silently dropped (the general fallthrough; the two rows above are called out explicitly because they are named in the S0 inventory) |

Notes grounded in source: `message.complete` payload is `{text, usage, status, ...}`
(`server.py:9137-9147`); `status` distinguishes complete/interrupted/error. `tool.complete` carries
`summary` (`server.py:3625-3627`). `clarify.request` payload is `{question, choices, request_id}`
— `question`/`choices` from the `_block` call at `server.py:3889-3890`, `request_id` injected by
`_block` at `server.py:2039`.

### Request → native RPC sequence (neutral request → exact native frames)

Each produces an ordered list of `NativeRequestPlan(method, params_builder)`; the session injects
each as its own `relay:"request"` envelope (so the S1 denylist + id-namespacing apply). `session_id`
in native params is the pane's live session for that child — see the live-session-id bootstrap note
below (do NOT rely on "cache from first frame": an already-live idle child emits no frame on attach,
so the session_id would never arrive).

**Live-session-id bootstrap (corrected).** On `AttachToEmployeeRequest` the translator issues
`session.active_list {}` (`server.py:6057-6092`; NOT on the S1 denylist) and reads the sole live
session's `id`. The pool spawns exactly one child per employee holding exactly one session
(one-child-one-session, `D-child-per-employee`), so `session.active_list` on that child returns
exactly one entry whose `id` is the live `session_id` — deterministic, no frame-wait. The
translator caches that id and uses it in every subsequent native request's `session_id`. (Frames on
the stream also carry `params.session_id` and can refresh the cache after a respawn, but the bootstrap
does not depend on a frame arriving.) A respawn (child reset) invalidates the cached id; the next
attach re-bootstraps.

| Neutral request | Native RPC sequence |
|---|---|
| `AttachToEmployeeRequest` | (1) synthesize `{relay:"subscribe", employee_entity_ids:[id]}`; (2) bootstrap the live `session_id` via `session.active_list` (see live-session-id note below — the child has exactly one live session, so its sole entry gives the id without waiting for a frame); (3) issue `session.history {session_id}` for the history snapshot — see history note below. NO attach-time `commands.catalog`: with the vet cache cut (`D-only-free-hermes-features`), the catalog is a single on-demand `ListCatalogRequest`; fetching it at attach would emit an unsolicited `CatalogResultEvent` for no reason. |
| `SendMessageRequest` | The session FIRST resolves each `image_ref` server-side to a child-openable ABSOLUTE path (see image-ref note below — contract lines 75-79), then: for each resolved path `image.attach {session_id, path}` (`server.py:9417-9424`, param is `path`); then `prompt.submit {session_id, text}`. No model/effort (amended contract). A mid-turn send is legal — stock queues rather than rejects; translation emits nothing synthetic. An unresolvable ref → `TurnFailedEvent`, and NO native frame reaches the child. |
| `AnswerQuestionRequest` | `clarify.respond {session_id, request_id, answer}` (`_respond(...,"answer")`, `server.py:10165-10167`). |
| `RespondToApprovalRequest` | `approval.respond {session_id, request_id, choice, all}` (`server.py:10186-10203`: reads `choice`, `all`). |
| `InterruptRequest` | `session.interrupt {session_id}` (`server.py:8101`). |
| `CompactRequest` | `session.compress {session_id}` (`server.py:7854`). Result confirms compaction; a native mid-turn `4009 session busy` surfaces as `TurnFailedEvent(reason=busy_already_running, detail=<native message>)` (not masked — `D-native-turn-concurrency`). |
| `ListCatalogRequest` | `commands.catalog {session_id}` (`server.py:11622`) → translator emits `CatalogResultEvent(payload_json=<native result serialized>)`, payload AS-IS (no normalization). |

**Cut request kinds are rejected, never reach the child (amended contract area 3).** The vocabulary
carries NO `set model`, `run command`, or `list model options` request. If a downstream sends an
envelope naming a cut/unknown request kind, `from_wire` rejects it and the session answers with a
neutral error; nothing is forwarded to the child. Raw `slash.exec`/`cli.exec` from downstream stay
denied by the S1 `SESSION_LIFECYCLE_DENYLIST` exactly as in raw mode.

**Image-reference resolution before `image.attach` (contract lines 75-79; STEP-4 finding #7).** A
`SendMessageRequest`'s `image_refs` are Panels-managed WEB-RELATIVE references
(`/files/chats/<entity>/...`, minted by the upload endpoint, `files/api.py`) — a child process cannot
open them. Before emitting any native frame, the `NeutralDownstreamSession` resolves each ref to a
child-openable ABSOLUTE filesystem path using the PUBLIC framework-free seam
`planner.files.logic.paths.resolve_chat_file(db_path, entity_id, relative_path) -> ChatFile`
(`files/logic/paths.py:61`) — the same resolution the legacy path performs pre-gateway
(`chat/service.py::_resolve_turn_image`, which we MIRROR but do not import — `chat/` is call-only).
`entity_id` is the `employee_entity_id`; `db_path` is threaded through composition →
`relay_neutral_downstream_websocket` → `NeutralDownstreamSession.__init__`. The parse/validate mirrors
`_resolve_turn_image`: reject a ref whose path is not `/files/chats/<entity_id>/…`, unquote, and
canonical-check. **On ANY resolution failure the WHOLE send is rejected:** emit
`TurnFailedEvent(reason=agent_error, detail=…)` and emit NO native frame (not the `image.attach`s, not
the `prompt.submit`). Resolution lives in the SESSION (it needs `db_path`+entity + I/O), not the pure
`hermes_frame_translation.py` (which stays framework-free) — the session passes resolved absolute
paths into `neutral_request_to_native_frames`.

Native RPC responses (to translator-issued requests like `commands.catalog`/`session.history`/
`session.compress`) come back correlated on `conn.outbound` and are consumed by the translator
(matched by the downstream request id it assigned), turned into the relevant neutral event (catalog
result payload-as-is / history snapshot / compact confirmation). They are not forwarded raw to the
pane.

**History-on-attach source (corrected — do NOT use `session.resume`).** The contract says history
"comes from the pool child's durable session ... never from Panels DB." The pool ALREADY issues
`session.resume`/`session.create` at spawn and consumes that response inside
`_create_or_resume_session` (`employee_child_pool.py:287-313`), and `session.resume` is on the S1
`SESSION_LIFECYCLE_DENYLIST` (`employee_child_relay.py:45`) — so a downstream (the translator)
CANNOT re-issue it, and the spawn-time messages are not available to a later attach. The correct
seam is `session.history {session_id}` (`server.py:7803-7823`): it is NOT on the denylist, and it
returns `{count, messages: _history_to_messages(history)}` sourced from the child's own live session
+ its DB-backed conversation (`db.get_messages_as_conversation`), i.e. the child's durable session —
NOT Panels' `chat_messages`. So `AttachToEmployeeRequest` issues `session.history`, translates the
`messages` (same `_history_to_messages` shape as `session.resume`) into `HistorySnapshotEvent`, and
the contract's "durable session, never Panels DB" holds. `session_id` is the child's live session id
the translator has cached from the frame stream.

## 4. Catalog listing + denylist + cut-request rejection (no `mediated_command_catalog.py`)

Under `D-only-free-hermes-features`, generic mediated command EXECUTION is CUT. There is no
`RunCommandRequest`, no `command.dispatch` from the neutral surface, and NO `mediated_command_catalog.py`
module (it is removed from the layout and allowlist). What remains is a single READ — `list catalog` —
plus the standing denylist and the rejection of cut request kinds.

- **`list catalog` (payload as-is).** `ListCatalogRequest` → the translator issues one
  `commands.catalog {session_id}` (`server.py:11622`) and delivers the native `result` PAYLOAD AS-IS in
  a `CatalogResultEvent(payload_json=...)`. No normalization, no cache, no vetting — the S2b pane renders
  its picker from the payload, and picking a SKILL inserts the skill's trigger into the composer as
  ordinary message text (skills are model-side; there is no execution machinery here). The `/model`
  entry may appear in the payload; that is harmless — there is no request that would dispatch it, and
  the pane simply does not offer it as a runnable command.
- **`compact` (`session.compress`).** `CompactRequest` → `session.compress {session_id}`
  (`server.py:7854`; also a native `command.dispatch` builtin `compress/compact`). Its result confirms
  compaction; a native mid-turn `4009 session busy` surfaces as
  `TurnFailedEvent(reason=busy_already_running, detail=<native message>)` — the native rejection is not
  masked (`D-native-turn-concurrency`).
- **Denylist unchanged.** The pane never sends `slash.exec`/`cli.exec`; those verbs from any downstream
  stay denied by the S1 `SESSION_LIFECYCLE_DENYLIST` (`employee_child_relay.py:43-55`). The neutral
  vocabulary has no request that maps to them.
- **Cut/unknown request kinds are rejected, never reach the child.** A downstream envelope naming a cut
  kind (a `set model` or `run command` shape) or any unknown kind is rejected by `from_wire`, and the
  session answers with a neutral error (`TurnFailedEvent(reason=agent_error, detail=<"unrecognized
  request kind: ...">)`); NO native frame is emitted. Tested (§8 area 3) with the exact rejection and a
  no-native-frame assertion.

## 5. The transcript-mirror tee consumer (`transcript_mirror_tee.py`)

- Implements the S1 `RelayTeeObserver` Protocol (`relay_tee.py:17-24`):
  `observe(*, employee_entity_id, direction, frame)`. Registered by passing it in
  `EmployeeChildRelay(..., tee_observers=(mirror,))` at composition (§9).
- **Direction/frames it acts on:** the tee must observe BOTH directions to reconstruct a turn: the
  user text is on the `FROM_DOWNSTREAM_TO_CHILD` `prompt.submit` frame (`params.text`), and the
  final assistant text is on the `FROM_CHILD_TO_DOWNSTREAM` `message.complete` (`payload.text`).
  Turn boundaries: `message.start` opens, `message.complete`/`error` closes. **Scope =
  completed/failed turns only** (contract §6). A failed turn (native `error` or `message.complete
  status=error/interrupted`) records the user text + the failure.
- **Write-behind mechanics:** `observe` NEVER touches SQLite — it only enqueues a small settled-turn
  record onto an internal bounded `queue.Queue` and returns. A single background worker thread
  drains that queue; the worker OPENS ITS OWN `sqlite3` connection against the DB path (the same path
  the app uses, NOT the request connection), owns it for the worker's lifetime, and CLOSES it on
  shutdown — the connection is created, used, and closed all on the worker thread. This is required
  because the repo's connection factory uses default SQLite thread affinity (no
  `check_same_thread=False`, `db.py:204`): a connection opened at construction on the composition
  thread and written from the worker thread would raise `ProgrammingError`. The worker opens a short
  write transaction per settled turn and calls the chat writers. Any exception is caught and logged
  (`logging`), NEVER re-raised — and it can't reach `observe` anyway, which already returned. A
  mirror failure never perturbs the live stream — the live stream runs on `conn.outbound`, a
  different object the tee never touches.
- **Exact `planner.chat.data` functions called (call-only):** `record_message(conn, entity_id,
  role=..., text=..., now=..., turn_id=...)` for the user text and the final assistant text. This
  is the minimal call-only seam for the two visible messages.
- **No durable row marker (amended contract).** The store has no source column and `chat_turns`
  `origin`/`mode` CHECK constraints admit no new value without a schema change, which is outside
  this ticket. The tee mirrors TEXT ONLY — completed/failed turns' user text and final assistant
  text. Distinguishing mirrored rows is S4's problem, where transcript ownership lands
  (`D-transcript-ownership-open`); for the Chief the cutover moment itself separates the eras. The
  tee does NOT mint a special turn_id prefix or any derived marker — that fragility was rejected in
  favor of deferring the distinction to S4.

## 6. Bounded queues + overload policy (area 7)

S1 left both the per-child outbound `queue.Queue` and per-downstream `asyncio.Queue` unbounded,
explicitly deferred to S2 "when real browser consumers arrive" (S1 plan). S2a is that point (the
neutral pane is a real consumer in S2b, and S2a's route is what it will connect to).

**Concrete decision — bound the REAL backpressure queue (`conn.outbound`), policy =
disconnect-the-slow-consumer. `NEUTRAL_OUTBOUND_MAX_FRAMES` in `neutral_relay_config.py`.**

Finding #4 from the plan review was correct: the queue that actually backs up when a browser is
slow is `conn.outbound`, which `register_downstream()` creates UNBOUNDED
(`employee_child_relay.py:187`) and into which the relay fan-out writes via `put_nowait`
(`_safe_put`/`_enqueue_text`, `employee_child_relay.py:467-477`). Bounding a SECOND queue downstream
of it would leave the real backpressure point unbounded — it does not satisfy the contract's
"bounded downstream queues". So the bound must apply to `conn.outbound` itself.

**Adapter-only mechanism (no S1 file change).** The neutral route, immediately after
`register_downstream()` and BEFORE any frame can flow, replaces `conn.outbound` with a bounded
queue object it owns:
- The relay reads `conn.outbound` FRESH on every enqueue (`conn.outbound.put_nowait(text)`,
  verified `employee_child_relay.py:469,475`) and on every drain (`conn.outbound.get()`,
  `relay_route.py:37`) — it never caches the queue reference. So swapping the attribute on the
  connection the route owns is safe and needs no relay change.
- The replacement is a small `OverflowSignallingQueue(asyncio.Queue)` with `maxsize =
  NEUTRAL_OUTBOUND_MAX_FRAMES`. Its `put_nowait` catches its own `QueueFull` and sets a
  `self.overflowed = True` flag (the relay already wraps `put_nowait` in `except Exception: pass`,
  so a raise would be silently swallowed and the frame dropped — the flag is what makes overload
  OBSERVABLE instead of a silent lossy drop). The neutral route's writer loop checks
  `conn.outbound.overflowed` after each `send_text` (and a bounded waker so it notices even when
  the socket is fully stuck): once set, the route **closes the downstream connection** and
  `unregister_downstream(conn)` runs in the `finally` — the disconnect-slow-consumer policy. On
  reconnect the pane re-attaches and re-syncs history from the durable session (`session.history`) —
  the S1 death-recovery model. Rationale for disconnect over drop-oldest: dropping neutral events
  mid-turn silently corrupts the pane's turn state (a dropped `message.complete` leaves a turn
  eternally open); a clean disconnect + re-attach is recoverable and honest.
- The per-child transport outbound (`raw_frame_transport._outbound`) stays as S1 built it: bounding
  upstream-to-child egress is out of scope (a wedged child is a child-death concern already handled
  by shutdown). S2a bounds the downstream-facing consumer S1 deferred — `conn.outbound` — which is
  the real one.
- **Revisiting S1's two D-s1-concurrency-scope trigger-bound limitations:** (a) unbounded queues →
  resolved by bounding `conn.outbound` with the overflow-signal + disconnect policy above, tested
  by driving overflow THROUGH the relay fan-out (not a private second queue) and asserting the
  connection closes + `unregister_downstream` runs (§8 area 7). (b) final-frame-vs-death → §7.

## 7. Final-frame-vs-death ordering (area 7)

S1 documented that a write-side death signaled from the outbound-writer thread can retire the
`ChildBinding` before a last stdout frame from the separate reader thread is delivered, and
deferred it with the rationale: death = child-reset + respawn; the downstream re-syncs from durable
session state on the next request, so lossless frame ordering across a death is not a contract
promise.

**Position: explicitly RE-ACCEPT, with rationale, grounded in D-s1-concurrency-scope — do not add
an ingress sequencer in S2a.** Rationale: the neutral translator emits `ChildResetEvent` on death
(from the S1 synthesized `child_reset` frame), and the pane's contract behavior on child reset is
to re-attach and re-sync history from the durable session (`session.history` messages) — which is
loss-tolerant by construction. A last dropped `message.delta` before a death is superseded by the
re-synced durable transcript. Adding the per-child ingress sequencer S1 sketched would change S1
relay internals (forbidden — the contract requires S1 public behavior preserved) for a case whose
loss is already recovered. This is re-accepted, not silently inherited, and is asserted by a test:
a death interleaved with a final stdout frame yields a `ChildResetEvent` and the session is marked
reset (not that the final frame arrived). Noted for `decisions.md`.

## 8. RED-first test ordering across the seven named acceptance areas

All under `tests/unit/`, fakes only (via the injectable `SpawnFn`/fake gateway from
`planner.minds.fake`), no real Hermes, no `pytest.skip`/`skipif` (verify's preflight skip-scan
fails the whole run). Every test runs unconditionally or hard-fails. Assertions state specific
values.

**Build order = area order (each area's tests written RED first, then its module until green):**

### Area 1 — Vocabulary contracts → `tests/unit/test_hermes_backend_neutral_vocabulary.py`
- `test_every_event_kind_round_trips` — one specific instance per event dataclass (all of them,
  INCLUDING the nested-type carrier `HistorySnapshotEvent` with a `NeutralHistoryMessage` tuple, and
  `CatalogResultEvent` with its payload-as-is `payload_json`); assert `from_wire(to_wire(x)) == x` and
  assert the exact wire dict for at least
  `AssistantTextDeltaEvent(employee_entity_id="ticket_e1", text="hi")` →
  `{"neutral":"event","kind":"assistant_text_delta","employee_entity_id":"ticket_e1","text":"hi"}`.
- `test_every_request_kind_round_trips` — same for all request dataclasses (attach, send, answer,
  approval, interrupt, compact, list-catalog), with `image_refs`/`choices` tuples round-tripping to
  tuples.
- `test_vocabulary_module_imports_nothing_hermes_specific` — the mechanical import-graph assertion
  (§2).

### Area 2 — Translation events → `tests/unit/test_hermes_backend_translation_events.py`
- `test_each_native_type_maps_to_its_neutral_event` — for each row in §3's frame→event table,
  build the native frame and assert the exact neutral event (kind + fields). MUST include the three
  tool phases explicitly: `tool.start`→`phase=started`, `tool.generating`→`phase=progress`
  (asserting `tool_name` from `payload.name`, `tool_id==""`, `preview==""`), `tool.complete`→
  `phase=completed`.
- `test_s0_inventory_kinds_have_explicit_rows` — for the S0-inventoried metadata kinds that map to
  passthrough (`session.info`, `status.update`), assert the EXACT `PassthroughEvent(native_type=...,
  payload_json=...)` — proving they are explicit rows, not accidental fallthrough. Covers the
  contract's "each native frame kind from the S0 inventory maps to its neutral event".
- `test_unknown_native_type_becomes_passthrough` — `ev("some.future.kind","sid",{"x":1})` →
  `PassthroughEvent(native_type="some.future.kind", payload_json='{"x": 1}')`; assert nothing
  dropped.
- `test_every_event_carries_employee_identity` — assert `.employee_entity_id == "ticket_e1"` for a
  representative sweep including a payload-less `message.start` and the passthrough.
- `test_mixed_burst_preserves_order` — feed `message.start, thinking.delta, message.delta,
  tool.start, message.complete`; assert the neutral events arrive in that exact order (drives the
  full `NeutralDownstreamSession` outbound loop against a fake gateway).

### Area 3 — Translation requests → `tests/unit/test_hermes_backend_translation_requests.py`
- `test_each_request_produces_mapped_native_sequence` — for each request, assert the exact ordered
  native method list. `SendMessageRequest` with two `image_refs` →
  `["image.attach","image.attach","prompt.submit"]`; assert each `image.attach` `path` and the
  `prompt.submit` `text`; assert `prompt.submit` params are EXACTLY `{session_id, text}` (no
  model/effort key present — the amended-contract negative assertion).
- `test_answer_maps_to_clarify_respond_with_request_id_and_answer` — assert `clarify.respond` params
  `{session_id, request_id, answer}` exact.
- `test_approval_maps_to_approval_respond_with_choice_and_all` — assert `approval.respond` params
  `{session_id, request_id, choice, all}` exact.
- `test_send_message_carries_no_model_field` — assert `dataclasses.fields(SendMessageRequest)`
  contains no `model`/`effort` (construction-level guarantee that no per-message model rides
  `prompt.submit`).
- `test_compact_maps_to_session_compress` — `CompactRequest` → EXACTLY `session.compress {session_id}`.
- `test_compact_mid_turn_rejection_surfaces_as_failure` — a fake gateway scripted to answer
  `session.compress` with `4009 session busy`; assert the translator emits
  `TurnFailedEvent(reason=busy_already_running, detail=<native message>)` — native rejection surfaced,
  not masked (D-native-turn-concurrency).
- `test_mid_turn_send_is_legal_and_translates_as_is` — with the child's session already `running`,
  a `SendMessageRequest` still emits the normal `prompt.submit` sequence (NO synthetic busy error
  produced by the translator); assert the emitted native frames equal the not-running case. The
  amended contract's queue-not-reject legality.
- `test_legacy_4009_on_prompt_maps_to_busy_already_running_completeness_case` — a fake gateway
  scripted to answer `prompt.submit` with error `(4009, "...")`; assert the translator emits
  `TurnFailedEvent(reason=busy_already_running)`, distinct from `agent_error`. Kept as the harmless
  completeness mapping (amended contract), not the normal busy path.

### Area 3 (continued) — Catalog + denylist + cut-request rejection → `tests/unit/test_hermes_backend_catalog_and_denylist.py`
(Contract area 3 also covers list-catalog, cut-request rejection, and the denylist — these live in
their own test file but belong to acceptance area 3; there is no separate mediated-commands area.)
- `test_list_catalog_delivers_payload_as_is` — `ListCatalogRequest` → translator issues
  `commands.catalog {session_id}`; a scripted native result
  `{"categories":[{"name":"Configuration","pairs":[["/compact","Compress context"]]}], "skill_count":0}`
  comes back correlated on `conn.outbound`; assert the translator emits EXACTLY
  `CatalogResultEvent(payload_json=<that result serialized verbatim>)` — payload as-is, NO
  normalization, NOT a `PassthroughEvent`, and the raw JSON-RPC frame is not forwarded.
- `test_cut_request_kind_is_rejected_and_never_reaches_child` — feed the session a downstream
  envelope naming a CUT kind (a `set_model`/`run_command`-shaped wire object) and an unknown kind;
  assert each yields a neutral `TurnFailedEvent(reason=agent_error, detail=<"unrecognized request
  kind: ...">)` AND no native frame is emitted to the child. Proves the cut surface is closed.
- `test_raw_slash_exec_and_cli_exec_still_denied_by_s1_denylist` — inject a raw
  `{relay:"request", frame:{method:"slash.exec"}}` and a `cli.exec` through the relay; assert each
  is answered with `RELAY_LIFECYCLE_DENIED_CODE` and never reaches the child (proves S2a did not
  relax S1's denylist — drives `handle_downstream_message` directly).

### Area 4 — History → `tests/unit/test_hermes_backend_history_snapshot.py`
- `test_attach_yields_translated_durable_session_messages` — a fake gateway whose `session.history`
  (NOT `session.resume`) returns `{count:2, messages:[{role:"user",content:"q"},
  {role:"assistant",content:"a"}]}`; on `AttachToEmployeeRequest` assert the translator issued
  `session.history {session_id}` (and did NOT issue the denylisted `session.resume`), and assert a
  `HistorySnapshotEvent` with `NeutralHistoryMessage(role="user",text="q",...)` then assistant, in
  order, sourced via the pool child's session (NOT from Panels DB).

### Area 5 — Mirror tee → `tests/unit/test_hermes_backend_transcript_mirror.py`
- `test_completed_turn_appends_matching_transcript_rows` — drive a completed turn (user
  `prompt.submit` FROM_DOWNSTREAM + `message.start`/`message.complete text="done"` FROM_CHILD)
  through the tee against an in-memory SQLite with the chat schema; after the background worker
  drains, assert `chat_messages` has the user text and the assistant final text `"done"` via
  `record_message`. Uses a barrier/`wait` on the worker (like S1's `_Collector.wait_frames`), never
  a sleep.
- `test_failed_message_complete_appends_user_and_assistant_text` — a turn ending in
  `message.complete(status="error", text="partial answer before failure")`; assert BOTH the exact
  user text row AND the exact assistant text row `"partial answer before failure"` are appended, in
  order (a failed `message.complete` DOES carry `text`, `server.py:9137-9147` — the test must assert
  the assistant row, not just the user row, or it would silently pass while dropping required text).
- `test_native_error_frame_appends_user_text_only` — a turn ending in a bare `error` frame (no
  `message.complete`, so no final assistant text exists); assert the user text row is present and NO
  assistant row is written (there is nothing to write). This is the distinct no-assistant-text case.
  (Text-only mirror throughout; no durable failure marker — the amended contract mirrors text only,
  S4 owns the distinction.)
- `test_mirror_write_failure_never_perturbs_live_stream` — make the tee's DB write raise (closed
  connection); assert `observe` returns normally, the failure is logged, and a parallel live
  `conn.outbound` fan-out for the same employee is unaffected. The contract's "off the conversation
  path, failures logged and never surfaced" assertion.

### Area 6 — S1 revisit → `tests/unit/test_hermes_backend_neutral_overload.py`
- `test_slow_neutral_consumer_is_disconnected_at_bound` — register a downstream, install the bounded
  `conn.outbound` (as the route does), and drive `> NEUTRAL_OUTBOUND_MAX_FRAMES` frames THROUGH THE
  RELAY FAN-OUT (`deliver_child_frame` for a subscribed employee, i.e. the real backpressure path)
  while the socket's `send_text` is blocked; assert `conn.outbound.overflowed` is set at exactly the
  bound, the route closes the connection, and `unregister_downstream(conn)` ran. Drives the REAL
  queue (finding #4), not a private second queue. Exact bound asserted.
- `test_final_frame_vs_death_reaccepted_position` — interleave a death with a trailing stdout
  frame; assert a `ChildResetEvent` is delivered and the session is marked reset; documents §7's
  re-accepted behavior (the final delta is not promised).

### Config-gate touch (new file, do NOT modify S1 test files)
- `tests/unit/test_hermes_backend_neutral_route_gate.py` asserts the neutral route
  accepts-then-closes 1013 when the backend is off, and that `relay_neutral_route` composes only
  under the flag (same posture as S1's gate test, new file).

## 9. Bounded file allowlist

The implementer may create/modify EXACTLY these; nothing else.

**New source (all under `src/planner/hermes_backend/`):**
- `src/planner/hermes_backend/neutral_vocabulary.py`
- `src/planner/hermes_backend/hermes_frame_translation.py`
- `src/planner/hermes_backend/neutral_downstream_session.py`
- `src/planner/hermes_backend/relay_neutral_route.py`
- `src/planner/hermes_backend/transcript_mirror_tee.py`
- `src/planner/hermes_backend/neutral_relay_config.py`

**New tests (all under `tests/unit/`):**
- `test_hermes_backend_neutral_vocabulary.py`
- `test_hermes_backend_translation_events.py`
- `test_hermes_backend_translation_requests.py`
- `test_hermes_backend_catalog_and_denylist.py` (catalog listing payload-as-is; compact; cut-request rejection; slash.exec/cli.exec still denied)
- `test_hermes_backend_history_snapshot.py`
- `test_hermes_backend_transcript_mirror.py`
- `test_hermes_backend_neutral_overload.py`
- `test_hermes_backend_neutral_route_gate.py`

**Composition/wiring edits S2a genuinely needs (named, minimal, justified):**
- `src/planner/hermes_backend/composition.py` — construct the `TranscriptMirrorTee` and pass it as
  `tee_observers=(mirror,)` into the `EmployeeChildRelay(...)` construction. Justification: S1 wired
  `RelayTeeObserver` as a seam but registered NO product consumer (`relay_tee.py:1-2`;
  `composition.py` constructs the relay with no observers). Registering the mirror is the ONLY way
  the contract's "first product consumer of S1's seam" gets wired, and composition.py is already the
  sole import site for the relay/pool. This is additive; it does not change the relay's public
  behavior.
- `src/planner/core/server.py` — add the neutral WS route registration (path e.g.
  `/api/relay/neutral`) alongside the existing `/api/relay` route, gated identically (backend on +
  flag). Justification: S1 added `/api/relay` here for the raw route; the neutral route is its
  sibling and must be reachable for S2b. Same minimal edit shape S1 made (route registration only;
  server.py imports neither pool nor relay directly — composition stays in composition.py).

**Explicitly NOT in the allowlist (hard constraints):**
- Nothing under `src/planner/minds/`, `src/planner/runtime/`.
- No modification to `src/planner/chat/` — chat is CALL-ONLY; the tee imports and calls
  `planner.chat.data` functions but changes no file there.
- No modification to any S1 file's behavior: `employee_child_relay.py`, `relay_tee.py`,
  `raw_frame_transport.py`, `employee_child_pool.py`, `relay_route.py` are READ/reuse only.
  (`composition.py` and `server.py` get additive wiring only.)
- No S1 test file modified.
- No `src/planner/core/db.py` schema change (see collision #3).

## Collisions raised and their rulings (evidence retained for the record)

All three were ruled by the orchestrator and the contract was amended (2026-07-18). The verified
evidence is kept here; the RULING line states what the amended contract now says.

**Turn concurrency is native (owner ruling D-native-turn-concurrency, 2026-07-18).** Panels invents
NO busy semantics anywhere. Hermes's defaults govern (queue / default-interrupt / steer) — stock
`prompt.submit` on a running session QUEUES the prompt and by default INTERRUPTS the live turn
(`_handle_busy_submit`, `server.py:8420-8425`). **Binding on this plan:** the translator and its
tests treat a mid-turn send exactly like any other send — NO error state, NO admission gate, NO
send-gating of any kind is added at any layer (not in `NeutralDownstreamSession`, not in the
request translation, not in a test's expected behavior). The `SendMessageRequest` translation emits
the same `image.attach*` + `prompt.submit` sequence whether or not a turn is running. The legacy
4009→`busy_already_running` mapping stays ONLY as a completeness case for a scripted 4009 reply — it
is never produced by the translator itself. (The earlier S3 handback flag about a step-runner
busy-guard is resolved by this ruling: no admission gate is rebuilt in S3 either; only
discovery-loop dispatch bookkeeping stays Panels-owned.)

> SUPERSEDED for model/command scope by `D-only-free-hermes-features` (2026-07-18): collisions #1 and
> #6 below were ruled to build model-switch + typed catalog result events; the owner then CUT model
> selection and generic command execution entirely. The source evidence in #1/#3/#6 remains accurate
> and is retained; the RULINGS in #1 and #6 are moot (model-switch and typed listing events are not
> built). #2 (turn concurrency) and #3 (history-on-attach `session.history`) stand.

**Collision #1 — per-prompt model/effort has no stock `prompt.submit` seam.**
RULING (SUPERSEDED — see banner): `SendMessageRequest` loses `model`/`effort` (option a). Model
selection = `model.options` (list) + the catalog-vetted `/model` switch through the mediated command
surface — mid-session,
per-session, fully stock. Hermes persists the switch to the session row and restores it on resume
(`server.py:1701`); `/model` is a real `COMMAND_REGISTRY` command (`hermes_cli/commands.py:134`) so
it vets through the catalog. No new vocabulary. (I had proposed dropping the picker; the orchestrator
correctly identified the mid-session `/model` path that keeps it.)
Evidence: `prompt.submit` reads only `session_id`, `text`, `truncate_before_user_ordinal`
(`server.py:8407-8458`) — no `model`/`reasoning_effort` (verified: grep of the handler body 8407-
8850 returns no model/effort read). The ONLY stock per-session model/effort override is at
`session.create` (`server.py:5184-5197`, reads `params["model"]`, `params["reasoning_effort"]`,
`params["fast"]`), which the pool issues ONCE at spawn and which is on the S1 denylist. There is no
`session.model`/`model.set` RPC; `config.set key="model"` (`server.py:10216`) is a global profile
write. So "send message → prompt.submit (with per-prompt model/effort when given)" cannot be
satisfied against stock Hermes without a Hermes-side change (`D-stock-hermes-only` forbids it).
Options: (a) drop `model`/`effort` from `SendMessageRequest` for S2a (matches stock reality;
cleanest); (b) keep the fields, emit them on `prompt.submit` anyway (stock silently ignores unknown
params — a silent no-op, violating "everything earns its existence"); (c) model/effort becomes a
`session.create`-time-only choice owned by the pool at spawn (not per-prompt). **Recommendation:
(a).** Needs a ruling: the contract's request vocabulary and area-3 test literally name per-prompt
model/effort.

**Collision #2 — the distinct busy/4009 reason may be unreachable for relay children.**
RULING: keep-and-note (option a). A mid-turn send is legal in the vocabulary — no synthetic busy
error; translate the native sequence as-is. 4009→`busy_already_running` stays as a completeness
mapping. The queue-not-reject reality is carried forward to S3 (see the S3 note above).
Evidence: `prompt.submit`'s normal busy path does NOT return 4009 — it queues via
`_handle_busy_submit` (`server.py:8420-8425`). The only `prompt.submit` 4009 is the lazy/watch
"subagent still running" case (`server.py:8432`), which fires only for `session.get("lazy")`
sessions. Pool relay children are created via `session.create {source:"panels-relay"}` (not
lazy/watch), so this 4009 likely never fires for them normally. The translator can still MAP
4009→`busy_already_running` (correct when it arrives), and area-3's test scripts the 4009 reply
directly, so the mapping is testable. Options: (a) keep the mapping (harmless, covers the case),
note that stock relay children queue-not-reject on busy; (b) additionally surface a neutral "queued"
signal. **Recommendation: (a)**, with the queueing reality recorded in decisions.md. Low stakes.

**Collision #3 — the tee's S4 marker cannot be set through call-only chat writers without a schema
change.**
RULING: text-only mirror (option c). Mirror completed/failed user + final-assistant text via
`record_message`; NO durable marker, NO derived turn_id prefix. Distinguishing mirrored rows is
S4's problem (`D-transcript-ownership-open`); for the Chief the cutover moment separates the eras.
Evidence: `record_message` writes `chat_messages(entity_id, turn_id, role, text, created_at)` — no
origin/source column (`data.py:116-152`; `db.py` chat_messages). `chat_turns.origin` is
`CHECK (origin IN ('human','worker','system'))` and `mode` is
`CHECK (mode IN ('message','command','worker_step'))` (`db.py:112-113`), so `start_turn`/
`finish_turn` cannot stamp a `panels-relay` origin. The `RELAY_SESSION_SOURCE="panels-relay"` label
is the *Hermes session source* (`employee_child_pool.py:40,305`), NOT a Panels chat column. So
there is no call-only way to mark a mirrored row so S4 can find it. Options: (a) S4 identifies
relay-mirrored rows structurally (e.g. a distinct `turn_id` prefix the tee mints via `new_id`) — no
schema change, but "find and re-decide" relies on a derived property; (b) add a nullable
`source`/`origin_source` column to `chat_turns` (a `db.py` migration change — OUTSIDE the S2a
allowlist and touching chat, needs the orchestrator to widen scope or cut a separate ticket); (c)
defer the marker to S4 (the contract cites `D-transcript-ownership-open`, and S4 is where transcript
ownership lands per the redesign plan) and have S2a mirror the text only. **Recommendation: (c) for
S2a** — mirror completed/failed user + final-assistant text via `record_message`, leave the durable
marker to S4's transcript-ownership ruling. Forcing a marker now requires either a schema change
(out of scope) or a fragile derived property. Sharpest collision; not improvised.
