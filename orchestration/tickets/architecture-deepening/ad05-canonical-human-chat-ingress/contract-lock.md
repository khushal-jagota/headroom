# AD05 contract lock

The orchestrator generated this lock after the corrected implementation plan passed independent review.
Implementation may delete and rewire the reviewed allowlist, but it must not retain a compatibility route,
service, result shape, synchronous adapter method, browser SSE helper, or second human Chat admission path.

## Public ingress contract

Human Chat enters through exactly:

- `POST /api/chat/{entity_id}/turns`, using the unchanged `ChatTurnRequest` and frontend
  `StartChatTurnBody` shape; and
- `POST /api/messages/chief`, accepting exactly one nonblank `text` field and calling the same
  `start_human_turn` service in message mode.

`POST /api/chat/{entity_id}/send`, `/stream`, and `/command` are absent and return 404. State, pause,
history, status, image upload, and command-catalogue reads remain because none is alternate message ingress.

`planner.chat.service.start_human_turn` is the only human message/command admission service. The parallel
top-level `send`, `stream`, and `run_command` functions are deleted in full; their persistence, transcript,
settlement, and error paths are not moved behind new names.

## Gateway observation contract

`GatewayAdapter` contains exactly the existing `status`, `history`, `stream`, `interrupt`, and `catalog`
capabilities. Its human transport remains:

```python
def stream(
    self,
    session_key: str | None,
    entity_id: str,
    text: str,
    mode: str,
    on_session_key: Callable[[str], None] | None = None,
    image_paths: tuple[Path, ...] = (),
) -> Iterator[ChatStreamChunk]: ...
```

`GatewayAdapter.send` and `GatewayAdapter.run_command` are absent from the protocol and from Echo, Offline,
Real, entity-routing, Shared, and test gateway implementations. `ChatSendResult` and `CommandRunResult` are
absent. No tuple, dataclass, mapping, or private helper recreates either synchronous result abstraction.

`ChatStreamChunk` remains the normalized gateway observation consumed by the server-owned turn and keeps
exactly these six fields:

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

This gateway stream is not the deleted HTTP SSE/service stream.

## Adapter behavior

`EchoGatewayAdapter.stream` directly owns its deterministic message and command behavior, including call
recording, fake key mint/reuse, callback timing, history, aliases, skills, `/compress`, display commands,
roles, busy errors, delay, and session/token/done observations. It does not delegate through a new
synchronous result helper. Offline and Real retain only their stream error implementations.

`EntityRoutingGateway.stream` remains the only human-write router and preserves Chief/Ticket routing plus
the image-path call form. `SharedGateway.stream` remains the only live human Hermes operation. The
synchronous `SharedGateway.send`, `run_command`, `_submit_human_and_drain`, and
`_drain_accepted_submission` paths are deleted; employee `run_ticket_step` and `GatewayChild.send` remain
unrelated transport paths.

The `/new` helper returns only the new stored session key:

```python
def _start_new_chat_session(
    self,
    child: GatewayChild,
    on_session_key: Callable[[str], None] | None,
) -> str: ...
```

Literal command-mode `/new` emits a session observation, the token `New session started.`, and a system
done observation carrying the same key and text. The next ordinary message uses the already-bound live
session without resume, recreation, or replay. `/new` with arguments takes the ordinary command stream.

## Preservation and deletion proof

The RED/static contract test owns the retired URLs solely as 404 assertions and uses AST/class/path-specific
checks for deleted services, result classes, adapter methods, HTTP SSE code, and browser helpers. It must
not ban unrelated `GatewayChild.send`, CLI HTTP sends, fake-process sends, or the ordinary verb “send”.

Canonical message, command, skill, display, validation, authorization, entity, busy/offline, key
persistence/rotation/race, Panels state, pause/follow-up, Chief shell, employee separation, and `/new`
behavior are migrated to `/turns -> start_human_turn -> GatewayAdapter.stream` proofs. The complete existing
unit and browser image suites remain unchanged, outside the edit allowlist, and run in full as preservation
contracts. AD06 owns deeper turn concentration; AD09 owns Panels Chat versus Employee history.

Any need to change these declarations or cross the corrected plan's bounded implementation allowlist returns
to the orchestrator before work continues.
