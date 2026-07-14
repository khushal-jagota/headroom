# AD05 implementation plan — canonical human Chat ingress

## Outcome and boundary

Human Chat has one write entrance after this ticket:

```text
browser or hosted Chief caller
  -> POST /api/chat/{entity_id}/turns
     or POST /api/messages/chief
  -> planner.chat.service.start_human_turn
  -> planner.chat.service._run_human_turn
  -> GatewayAdapter.stream
  -> the entity's real Hermes session
  -> Panels chat_turns/chat_messages mirror
```

The old JSON `POST .../send`, SSE `POST .../stream`, and JSON
`POST .../command` routes are removed. Their service functions, synchronous
gateway methods, result dataclasses, browser SSE parser, tests, and live documentation
are removed with them. There is no redirect, alias, wrapper, compatibility response,
or second admission path.

This plan deliberately does not refactor `start_human_turn`, `_run_human_turn`,
session ownership, transcript settlement, history merging, or employee delivery.
Those are AD06/AD09 boundaries. `GatewayAdapter.stream` is not the deleted HTTP
stream: it is the normalized Hermes observation transport already consumed by
`_run_human_turn`, and it survives unchanged.

## Frozen declarations

### Public HTTP boundary

Keep these human-ingress declarations and their current request/response behavior:

```text
POST /api/chat/{entity_id}/turns
  body: StartChatTurnBody = {
    text: string;
    mode: "message" | "command";
    image_references?: string[];
  }
  response: the created ChatTurn

POST /api/messages/chief
  body: exactly {"text": <nonblank string>}
  response: the created ChatTurn
```

Keep `GET /state`, `POST /pause`, `GET /history`, `GET /status`, image upload,
and `GET /api/chat/commands`. They are state/control/catalogue reads or attachment
intake, not alternate human message ingress.

Delete the route functions and decorators for `/send`, `/stream`, and `/command`.
Valid requests to each deleted route must return 404. Remove `_sse`,
`StreamingResponse`, the SSE JSON/iterator plumbing, and imports used only by those
routes. Do not alter `ChatTurnRequest` or the frontend `StartChatTurnBody`.

### Service boundary

Keep this declaration unchanged:

```python
def start_human_turn(
    conn_factory: Callable[[], sqlite3.Connection],
    gateway: GatewayAdapter,
    entity_id: str,
    text: str,
    mode: str,
    now: int,
    now_fn: Callable[[], int] | None = None,
    *,
    image_references: Sequence[str] = (),
    db_path: str | Path | None = None,
) -> ChatTurn: ...
```

Keep `_run_human_turn` and its `gateway.stream(...)` calls unchanged except for any
import/docstring cleanup forced by deletion. Delete the top-level
`planner.chat.service.send`, `planner.chat.service.stream`, and
`planner.chat.service.run_command` functions in full. Their duplicated direct
message writes, key persistence, exception translation, and settlement logic are
not moved elsewhere.

### Gateway contract

`GatewayAdapter` keeps exactly the human transport and non-ingress capabilities it
still needs:

```python
class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def history(self, session_key: str | None, entity_id: str) -> ChatHistory: ...
    def stream(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        on_session_key: Callable[[str], None] | None = None,
        image_paths: tuple[Path, ...] = (),
    ) -> Iterator[ChatStreamChunk]: ...
    def interrupt(self, session_key: str, entity_id: str) -> None: ...
    def catalog(self) -> CommandCatalog: ...
```

Delete `GatewayAdapter.send` and `GatewayAdapter.run_command` and the corresponding
methods from `EchoGatewayAdapter`, `OfflineGatewayAdapter`, `RealGatewayAdapter`,
`EntityRoutingGateway`, and `SharedGateway`.

Keep the `ChatStreamChunk` field shape exactly as it is:

```python
@dataclass(frozen=True)
class ChatStreamChunk:
    type: str
    text: str = ""
    reply_text: str = ""
    session_key: str = ""
    kind: str = "assistant"
    activity: ChatActivityObservation | None = None
```

Rewrite its comment/docstring to say that it is a normalized gateway observation
consumed by the server-owned human turn (`session`, `activity`, `token`, `done`), not
an SSE chunk. Delete `ChatSendResult` and `CommandRunResult`; do not replace them with
new synchronous result shapes.

### Adapter implementation disposition

- `EchoGatewayAdapter.stream` absorbs the deterministic behavior currently split
  across its `send` and `run_command` methods. In message mode it records `calls`,
  mints/reuses the fake key, invokes `on_session_key`, appends the fake user/assistant
  history, and emits session/token/done chunks. In command mode it records
  `command_calls`, preserves alias, skill, `/compress`, display-command, role, busy,
  history, delay, and chunk behavior directly. It must not call a new synchronous
  helper that recreates either deleted result contract.
- `OfflineGatewayAdapter` retains one `stream` implementation that raises the same
  `gateway_offline` error; its synchronous methods disappear.
- `RealGatewayAdapter` remains the registry placeholder with the unchanged stream
  signature; its synchronous placeholder methods disappear.
- `EntityRoutingGateway.stream` remains the only human-write router and still selects
  the Chief gateway by entity id, including the image-path call form. Its synchronous
  forwarding methods disappear.
- `SharedGateway.stream` remains the sole human session operation. Delete
  `SharedGateway.send`, `SharedGateway.run_command`, `_submit_human_and_drain`, and
  `_drain_accepted_submission`, which exist only to produce synchronous results.
  Keep `_stream_human_consequence`, `_stream_command`, `_stream_done`, context
  acknowledgement, activity normalization, and accepted-consequence ownership.

Freeze the `/new` helper to the smallest surviving return value:

```python
def _start_new_chat_session(
    self,
    child: GatewayChild,
    on_session_key: Callable[[str], None] | None,
) -> str: ...
```

It creates and binds one new session, calls `on_session_key`, and returns only the
stored session key. The exact `mode == "command" and text == "/new"` branch in
`SharedGateway.stream` emits:

1. `ChatStreamChunk(type="session", session_key=<new key>)`;
2. a token containing `New session started.`; and
3. a system-kind done chunk with the same text and key.

`/new` with any arguments does not match that branch. It resumes/creates normally
and goes through `_stream_command` and Hermes command dispatch. The next ordinary
message after literal `/new` uses the already-bound live session and must not resume,
recreate, or replay prior input.

### Browser boundary

Keep `startChatTurn`, `uploadChatImage`, and `pauseChatTurn`. Delete `SseEvent`,
`parseSseChunk`, and `streamChat` from `web/src/lib/api.ts`. `ChatPanel.svelte`
already calls only `startChatTurn`; it is inspected but unchanged. No EventSource,
fetch-reader, or compatibility helper replaces the deleted parser.

## Production deletion trace

1. `chat/api.py`: delete three route shells and SSE formatting; canonical turn,
   hosted Chief, state/history/pause/status/images/catalogue routes remain.
2. `chat/service.py`: delete three parallel services and their result imports;
   canonical turn continues to own admission, background execution, gateway stream
   consumption, visible turn writes, and settlement.
3. `chat/contracts.py` and `core/adapters/base.py`: delete synchronous results and
   protocol methods; relabel the surviving chunk for internal gateway observation.
4. Fake/real/routing/shared adapters: remove every implementation of the deleted
   methods; move only fake behavior required to keep `stream` tests deterministic.
5. `SharedGateway.stream`: keep message and command modes, exact `/new`, model-backed
   skills, display commands, image attachment, key callback/rotation, and safe
   activity/token/done emission. This is the live transport; it is not deleted or
   renamed.
6. `web/src/lib/api.ts`: remove the unused browser SSE entrance. The browser's only
   write remains `startChatTurn`.

Employee `run_ticket_step`, return-for-revision delivery, `GatewayChild.send` (the
JSON-RPC subprocess transport), CLI `http.send`, and test fake-process `send` methods
are unrelated names and remain.

## Test-first migration and exact legacy-test disposition

### New RED contract guard

Add `tests/unit/test_chat_ingress_contract.py` before production deletion. Its first
run must fail against the old routes and identifiers. It will:

- build the app and assert valid POSTs to `/send`, `/stream`, and `/command` each
  return 404, while `/turns` and `/messages/chief` remain registered;
- parse the affected Python modules with `ast` and assert the three deleted
  top-level service functions, the two result classes, and `send`/`run_command`
  methods on the six named gateway classes are absent;
- assert `GatewayAdapter.stream` still has the exact frozen signature and
  `ChatStreamChunk` still has the exact six fields;
- assert `chat/api.py` contains no retired route decorator, `StreamingResponse`,
  `_sse`, or `text/event-stream` machinery;
- assert `web/src/lib/api.ts` contains no `streamChat`, `parseSseChunk`, or retired
  `/stream` URL; and
- scan the three live docs and built JavaScript for old routes/result/helper names,
  without scanning historical orchestration files or banning the unrelated ordinary
  verb `send`.

The guard itself is the only test allowed to spell the retired URLs, solely as 404
assertions. Static checks are class/path-specific so they do not delete
`GatewayChild.send`, CLI HTTP `send`, WebSocket test transports, or ordinary prose.

### `tests/unit/test_chat_seed.py`

- Keep history, state, Chief shell, active-turn, activity, pause/follow-up, status,
  and history-rotation tests that do not use retired ingress.
- Delete `test_chat_send_echo_persists_key_and_event`; the canonical
  `test_chat_state_records_server_owned_human_turn` is extended to assert the stored
  key and exactly one `chat_session_created` event after settlement.
- Change `test_chat_history_reads_full_fake_trace` setup from two `/send` calls to
  two `/turns` message calls, waiting for each turn to settle before the history
  read. The expected four Hermes history lines and one reused key stay exact.
- Remove the four HTTP-SSE tests. Move their only non-SSE contract—key persistence
  before the first token—into the existing blocking canonical-turn test by asserting
  the Ticket key and turn key before release. Command role is covered by canonical
  command tests; offline settlement is covered below.
- Replace the legacy second-send/day/Chief/missing-entity/malformed-day/status tests
  with canonical `/turns` equivalents only where they prove a surviving boundary:
  retain one second-message key-reuse proof, one parameterized Ticket/day/top-level
  entity proof, and one parameterized missing/invalid entity proof. Keep the hosted
  Chief shell test as its narrow public boundary. Do not retain duplicate response-
  shape assertions for deleted result objects.
- Migrate lost first-write race, worker-claim-before-prompt, stale-key rotation,
  unchanged Ticket control status, running-worker rejection, offline, and gateway-
  busy cases to `start_human_turn`/`/turns` with stream-only fake gateways. Wait on
  the durable `chat_turns` row/events rather than expecting a synchronous gateway
  result. Preserve the exact no-prompt/no-key/no-event assertions where admission
  rejects synchronously, and exact persisted winner/rotated keys where the background
  stream reaches its callback.
- Add the canonical integration proof for literal `/new`: POST command mode through
  `/turns`, wait for the system line and fresh stored key, then POST an ordinary
  message and prove the same live handle is used. Add `/new title` through `/turns`
  and prove it takes ordinary command dispatch. Both use real `SharedGateway` with a
  scripted fake child, not a synchronous adapter method.
- Remove `send`/`run_command` stubs from custom gateways; give stream fakes the full
  frozen signature, including optional `image_paths` where protocol conformance is
  checked.

### `tests/unit/test_chat_commands.py`

- Keep the four command-catalogue tests unchanged.
- Rehome skill, alias, display command, `/compress`, key reuse, key rotation,
  worker-running, missing Ticket, direct-only, offline, and busy proofs onto
  `/turns` with `mode="command"`. Assertions read settled Panels state, stored keys,
  events, and scripted stream calls rather than deleted `CommandRunResult` JSON.
- Consolidate validation with the canonical turn body contract: missing/non-string/
  blank text, invalid mode, and command images are asserted on `/turns`; do not keep
  the old `{command: ...}` body tests.
- Replace the two direct `service.run_command` race/rotation fakes with stream-only
  fakes used through the canonical service. Delete their result-type imports and
  synchronous result assertions.
- Do not duplicate the route-level `/new` cases added to `test_chat_seed.py`; this
  file keeps command catalogue and ordinary command/skill semantics.

### Other required unit files

- `tests/unit/test_authctx_routes.py`: rename the Chat authorization test and point
  both requests at `/turns` with `{text: "hi", mode: "message"}`. Agent remains
  `agent_forbidden`; unattributed ingress returns a running message-mode `ChatTurn`,
  not a deleted echo result.
- `tests/unit/test_worker_context.py`: delete
  `test_legacy_chat_paths_persist_original_visible_text_without_gateway_history`
  and its chat-service/result imports. It exists only to compare three deleted
  paths; canonical visible-text and real Hermes delivery are already covered in
  `test_chat_seed.py` and the gateway stream tests. Keep the worker-context domain
  tests unchanged.
- `tests/unit/test_automatic_employee_step_eligibility_actions.py`: change the one
  excluded Chat write from `/send` to `/turns`, wait for its fake turn to settle,
  and keep the assertion that neither start nor settlement wakes automatic
  Employee-step discovery. Assert the created turn shape instead of `reply_text`.
- `tests/unit/test_chat_images.py`: inspect and retain the whole file unchanged as an
  image-preservation contract. Its canonical `/turns` coverage must continue to
  prove image upload/reference validation, message-only and image-only admission,
  ordered managed references, gateway `stream(..., image_paths=...)` delivery, and
  durable Panels state. Run the full file; do not replace it with a keyword-selected
  subset.
- `tests/unit/test_minds.py`:
  - make `RecordingChatGateway` stream-only and change the entity-routing proof to
    consume Chief and Ticket streams; merge/delete the redundant ordinary-router
    compatibility case;
  - retain context delivery/acknowledgement proofs through message, command, and
    image `gateway.stream` only; remove all sync/stream paired branches and deleted
    result assertions;
  - delete the literal `/new` sync test and extend the stream test with the next
    ordinary message/live-handle proof; make `/new`-with-arguments stream-only;
  - convert completed-session reopen, sibling-role survival, restart/resume,
    prepare-time detach retry, and ordered model-command tests from `send` or
    `run_command` to `stream`, preserving their exact child method sequences, key
    callbacks, prompt text, and no-replay assertions; and
  - retain all employee `run_ticket_step`, session-manager, history, interrupt,
    image, activity, and gateway-child transport tests unchanged.

### Browser tests

- `tests/e2e/test_flows_a.py`: change only the two stale comments that say
  `POST /command`; they now say the composer starts a canonical command-mode turn.
  Preserve the skill/menu, display system-line, typed command, echo, offline, and
  navigation assertions.
- `tests/e2e/test_live_chat_state.py`: no source edit is expected. Run the whole file
  to preserve navigation/remount, activity, pause, immediate follow-up, and worker
  turn behavior through the already-canonical browser path.
- `tests/e2e/test_chat_images.py`: inspect and retain the whole file unchanged as the
  browser proof that the composer uploads and previews images, starts the canonical
  turn, preserves ordered attachments and retry state, and restores the managed
  image transcript after reload. Run this complete file without a `-k` filter.

## Documentation

- `docs/chat.md`: state plainly that both ordinary messages and commands enter via
  one server-owned `/turns` request and are delivered through the gateway stream;
  the Chief endpoint is only a narrow shell over the same service. Preserve image,
  pause, `/new`, session rotation, command catalogue, and worker-running behavior.
  Keep the warning that Panels rows are a mirror, not worker delivery.
- `docs/systems.md` and `docs/systems.html`: keep Markdown/HTML synchronized. Describe
  one human-turn ingress and the separate gateway stream transport; retain the
  EmployeeStepRunner boundary and explicit Panels Chat versus employee-session
  warning. Do not describe removed routes or synchronous adapter methods as live.
- Historical orchestration and decisions remain untouched.

## Bounded changed-path allowlist

Only these paths may change during implementation:

```text
src/planner/chat/api.py
src/planner/chat/contracts.py
src/planner/chat/service.py
src/planner/core/adapters/base.py
src/planner/core/adapters/fakes.py
src/planner/core/adapters/real.py
src/planner/minds/shared_gateway.py
web/src/lib/api.ts
tests/unit/test_chat_ingress_contract.py                 # add
tests/unit/test_chat_seed.py
tests/unit/test_chat_commands.py
tests/unit/test_authctx_routes.py
tests/unit/test_worker_context.py
tests/unit/test_minds.py
tests/unit/test_automatic_employee_step_eligibility_actions.py
tests/e2e/test_flows_a.py                                # comments only
docs/chat.md
docs/systems.md
docs/systems.html
web/dist/index.html                                      # build-only, if hash changes
web/dist/assets/index-*.js                               # build-only delete/add, if changed
```

`ChatPanel.svelte`, `web/src/lib/types.ts`, `tests/unit/test_chat_images.py`,
`tests/e2e/test_live_chat_state.py`, `tests/e2e/test_chat_images.py`, worker runtime
files, session manager files, and `PROGRESS.md`/`decisions.md` are inspected
contracts, not edit targets. If implementation appears to require changing either
image test, stop for review rather than adding it to the allowlist implicitly. The
browser helper is currently tree-shaken from the checked-in bundle, so no dist diff
is expected; nevertheless run the normal Vite build. If Vite emits a new JavaScript
hash, accept only the generated old-chunk deletion, new-chunk addition, and
`web/dist/index.html` reference change. Never hand-edit `web/dist`; CSS/font output
must remain byte-identical.

Any required path outside this list is a stop-and-review condition, not permission
to broaden the ticket.

## Implementation order and checks

1. Add the RED contract/static test and migrate the surviving behavior tests to
   `/turns`/`gateway.stream`. Confirm the new guard fails for the expected old route,
   method, class, and browser symbols.
2. Delete route, service, result-contract, protocol, adapter, SharedGateway, and
   browser SSE code in that order so Python and TypeScript failures expose every
   caller.
3. Refactor the fake stream and `/new` helper exactly as frozen above. Run the focused
   unit set until green.
4. Update docs and stale e2e comments. Run frontend checks/build and inspect generated
   output against the allowlist.
5. Run focused browser files. Inspect `git diff --check`, the changed-path list, and
   the static guard one final time. Leave the full `./verify` to the orchestrator
   after independent diff review.

Focused commands (no `./verify` in this ticket implementation cycle):

```bash
.venv/bin/pytest tests/unit/test_chat_ingress_contract.py
.venv/bin/pytest \
  tests/unit/test_chat_seed.py \
  tests/unit/test_chat_commands.py \
  tests/unit/test_authctx_routes.py \
  tests/unit/test_worker_context.py \
  tests/unit/test_minds.py \
  tests/unit/test_automatic_employee_step_eligibility_actions.py \
  tests/unit/test_chat_images.py
.venv/bin/ruff check \
  src/planner/chat src/planner/core/adapters src/planner/minds/shared_gateway.py \
  tests/unit/test_chat_ingress_contract.py tests/unit/test_chat_seed.py \
  tests/unit/test_chat_commands.py tests/unit/test_authctx_routes.py \
  tests/unit/test_worker_context.py tests/unit/test_minds.py \
  tests/unit/test_automatic_employee_step_eligibility_actions.py
.venv/bin/mypy src/ tests/typing/
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
.venv/bin/pytest tests/e2e/test_chat_images.py
.venv/bin/pytest tests/e2e/test_flows_a.py tests/e2e/test_live_chat_state.py \
  -k 'chat or slash'
git diff --check
```

The image unit and e2e files run in full. The subsequent focused browser command
selects the Chat/Slash flows in the two broader flow files only; its filter never
applies to `tests/e2e/test_chat_images.py`. The orchestrator's later single
`./verify` remains the only completeness claim.

## Delegated judgments

1. **All-day/top-level Chat coverage stays canonical.** The generic `/turns` route
   remains valid for Tickets, canonical planning days, and configured top-level
   agent ids. The Chief hosted route is not a second service path; it is a restricted
   request shell over `start_human_turn`.
2. **Asynchronous errors are asserted as durable turn outcomes.** Offline or busy
   failures that occur after a canonical turn starts are checked in `chat_turns` and
   events, not forced back into the deleted synchronous HTTP response model.
   Synchronous admission failures (bad body, forbidden actor, missing entity,
   running Ticket, competing Panels turn) keep their current HTTP errors.
3. **No replacement result type.** A tuple/dataclass shaped like
   `CommandRunResult` would preserve the deleted synchronous abstraction. Literal
   `/new` needs only a stored key; all other outputs already travel as
   `ChatStreamChunk` observations.
4. **Generated output is conditional but bounded.** `streamChat` is unused and
   tree-shaken today, so source deletion may leave the served bundle unchanged. The
   build is still required; only its JavaScript hash/index outputs may enter the diff.
