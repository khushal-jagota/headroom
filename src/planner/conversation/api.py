"""The conversation system over HTTP, mounted at /api/conversation.

Every route here is a thin shell: it parses what came in, calls the one function on the
conversation system that does the thing, and serializes what came back. No route decides
anything the system decides — a fate is reported exactly as the system returned it, and a
refusal is a successful reply carrying a refusal, not an HTTP error, because a refused
delivery is an answer rather than a broken request.

Reading a conversation is two doors and they fit together. ``/events`` hands back the rows
after a position. ``/tail`` replays those same rows and then keeps going live. Opening a
conversation, reloading it, and opening it on a second device are all the same act: say
which row you have and take everything after it.
"""

from __future__ import annotations

import asyncio
import json
from base64 import b64decode
from binascii import Error as BinasciiError
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    ModelThinkingFrame,
    PermissionAskedEventPayload,
    ToolCallProgressFrame,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.live_tail import ConversationLiveTail, ConversationTailItem
from planner.conversation.message_content import (
    MessageContent,
    MessageImage,
    MessagePiece,
    MessageText,
)
from planner.conversation.message_files import (
    ConversationMessageFiles,
    MessageFileMissing,
)
from planner.conversation.snapshot import (
    BackendSnapshot,
    BackendSnapshotService,
    BackendUpdateResult,
)
from planner.conversation.storage import ConversationStore, StoredConversationEvent
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.sse import HEARTBEAT_FRAME, register_open_stream_closer

# The two things a tail carries, told apart by name so a browser never has to guess which
# it is holding: one is a row that is in the record, the other is gone once it is drawn.
COMMITTED_EVENT_STREAM_NAME = "conversation-event"
LIVE_FRAME_STREAM_NAME = "conversation-frame"

router = APIRouter()


# --- what the routes are given ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConversationRuntime:
    """The conversation system as one running thing, built once for the process."""

    store: ConversationStore
    system: SqliteProcessConversationSystem
    live_tail: ConversationLiveTail
    backend_snapshots: BackendSnapshotService
    message_files: ConversationMessageFiles
    sse_heartbeat_ms: int

    async def shutdown(self) -> None:
        """Let the watchers go first, then stop the system's children.

        A tail is idle nearly all the time and only ends when its browser leaves, so a
        server that waited for them to finish on their own would never finish at all.
        """
        self.live_tail.close_all_subscriptions()
        await self.system.shutdown()


def build_conversation_runtime(
    *,
    db_path: str,
    db_busy_timeout_ms: int,
    sse_heartbeat_ms: int,
    backend_child_factories: Mapping[ConversationBackendKey, BackendChildFactory],
) -> ConversationRuntime:
    """Compose the conversation system, its record, the files it keeps, and the tail."""
    store = ConversationStore(db_path, busy_timeout_ms=db_busy_timeout_ms)
    live_tail = ConversationLiveTail()
    message_files = ConversationMessageFiles(db_path)
    return ConversationRuntime(
        store=store,
        system=SqliteProcessConversationSystem(
            store=store,
            backend_child_factories=backend_child_factories,
            message_files=message_files,
            live_tail=live_tail,
        ),
        live_tail=live_tail,
        backend_snapshots=BackendSnapshotService(),
        message_files=message_files,
        sse_heartbeat_ms=sse_heartbeat_ms,
    )


def _runtime(request: Request) -> ConversationRuntime:
    runtime = getattr(request.app.state, "conversation", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="the conversation system is unavailable")
    if not isinstance(runtime, ConversationRuntime):  # pragma: no cover - composition error
        raise HTTPException(status_code=503, detail="the conversation system is unavailable")
    return runtime


Runtime = Annotated[ConversationRuntime, Depends(_runtime)]


# --- what the routes are sent -------------------------------------------------------------


class StartConversationBody(BaseModel):
    """A start request as JSON. Everything but the id may be left out and take its floor."""

    conversation_id: str
    backend_key: ConversationBackendKey | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    role_text: str | None = None
    identity_environment_variables: dict[str, str] | None = None
    workspace_folder: str | None = None
    access: ConversationAccess | None = None


class SentTextPiece(BaseModel):
    piece: Literal["text"]
    text: str


class SentImagePiece(BaseModel):
    """A picture, with its bytes. This is the only door bytes come in through.

    They ride with the message they belong to rather than going through an upload of
    their own, so there is no half-sent message: either the picture and the words arrive
    together or nothing does. The server keeps the bytes and the record names what it
    kept, so ``data`` appears here and nowhere else.
    """

    piece: Literal["image"]
    data: str
    media_type: str
    file_name: str | None = None


type SentPiece = SentTextPiece | SentImagePiece


class SendBody(BaseModel):
    """A send as JSON.

    ``content`` is the message: a run of pieces, which for an ordinary message is one
    piece of written words. There is no separate ``text`` field — one door in, and a
    message is the same kind of thing however much is in it.

    ``sender_message_id`` and ``sent_at_unix_milliseconds`` are the sender's own two facts
    about this message, kept on its row exactly as they arrive. A browser mints both before
    it sends so that it can draw the message straight away and still recognise it when the
    record hands it back. Both are optional: a sender that mints neither sends what it
    always sent.
    """

    content: list[SentPiece]
    sender_label: str
    mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free
    model_change: str | None = None
    reasoning_effort_change: str | None = None
    sender_message_id: str | None = None
    sent_at_unix_milliseconds: int | None = None


class PermissionAnswerBody(BaseModel):
    ask_id: str
    option_id: str


# --- starting, reading, writing -----------------------------------------------------------


@router.post("/conversations", status_code=201)
async def start_conversation(body: StartConversationBody, runtime: Runtime) -> dict[str, Any]:
    """Create a conversation. The id is the caller's, and it is once-only."""
    request = _start_request(body)
    try:
        await runtime.system.start_conversation(request)
    except ConversationAlreadyStarted as already_started:
        raise HTTPException(
            status_code=409, detail=f"conversation {body.conversation_id} already exists"
        ) from already_started
    except ValueError as invalid:
        raise HTTPException(status_code=422, detail=str(invalid)) from invalid
    return await _conversation_view(runtime, body.conversation_id)


@router.get("/conversations/{conversation_id}")
async def read_conversation(conversation_id: str, runtime: Runtime) -> dict[str, Any]:
    """What this conversation is, what it is doing, and what it is waiting on.

    This plus the rows after a position is the whole of opening a conversation, whether
    for the first time, after a reload, or on a second device.
    """
    return await _conversation_view(runtime, conversation_id)


@router.get("/conversations/{conversation_id}/events")
async def read_conversation_events(
    conversation_id: str, runtime: Runtime, after: int = 0
) -> dict[str, Any]:
    await _require_conversation(runtime, conversation_id)
    events = await runtime.store.read_events_after(conversation_id, after)
    return {"events": [_event_json(event) for event in events]}


@router.get("/conversations/{conversation_id}/tail")
async def tail_conversation(
    conversation_id: str, runtime: Runtime, after: int = 0
) -> StreamingResponse:
    """Replay the rows after a position, then keep going live.

    The watch is registered before a single row is read back, so anything committed while
    the replay is being read is already waiting rather than lost. The replay's last row is
    the high-water mark, and anything at or below it that arrives on the watch has already
    been sent — so the join has no hole in it and nothing arrives twice.
    """
    await _require_conversation(runtime, conversation_id)
    return StreamingResponse(
        _tail_stream(runtime, conversation_id, after), media_type="text/event-stream"
    )


@router.post("/conversations/{conversation_id}/send")
async def send_into_conversation(
    conversation_id: str, body: SendBody, runtime: Runtime
) -> dict[str, Any]:
    """Send a message in, and report the fate the system gave it.

    A refusal comes back as a fate on a successful reply. It is what happened to this
    message, not a complaint about the request.

    The bytes a picture or a sound arrived with are kept before anything is sent, so the
    message that reaches the system names files that are already there. A message that is
    then refused leaves those files behind unnamed, which costs a few bytes on disk and
    loses nothing.
    """
    try:
        content = await _kept_message_content(runtime, conversation_id, body.content)
        fate = await runtime.system.send(
            conversation_id,
            content,
            sender_label=body.sender_label,
            mode=body.mode,
            model_change=body.model_change,
            reasoning_effort_change=body.reasoning_effort_change,
            sender_message_id=body.sender_message_id,
            sent_at_unix_milliseconds=body.sent_at_unix_milliseconds,
        )
    except ValueError as invalid:
        raise HTTPException(status_code=422, detail=str(invalid)) from invalid
    return _fate_json(fate)


@router.get("/conversations/{conversation_id}/files/{stored_file_id}")
async def read_conversation_message_file(
    conversation_id: str, stored_file_id: str, runtime: Runtime
) -> Response:
    """The bytes of one file a message in this conversation carries.

    Both names are ids and the file is resolved under the conversation's own folder, so
    there is nothing here that reaches another conversation's files. A row is written once
    and the file it names never changes, so this is cached hard: the id is the version.
    """
    await _require_conversation(runtime, conversation_id)
    try:
        contents = await runtime.message_files.read(conversation_id, stored_file_id)
    except (MessageFileMissing, OSError) as gone:
        raise HTTPException(status_code=404, detail="no such file") from gone
    media_type = await runtime.message_files.media_type_of(conversation_id, stored_file_id)
    return Response(
        content=contents,
        media_type=media_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/conversations/{conversation_id}/interrupt", status_code=204)
async def interrupt_conversation(conversation_id: str, runtime: Runtime) -> Response:
    """Stop the running turn. What was held behind it runs from here."""
    await runtime.system.interrupt(conversation_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/kill", status_code=204)
async def kill_conversation(conversation_id: str, runtime: Runtime) -> Response:
    """Stop the running turn and throw away everything waiting behind it.

    This is what pressing New uses: an interrupt alone would free the agent and let the
    held messages run, which is exactly what starting again must not do.
    """
    await runtime.system.kill(conversation_id)
    return Response(status_code=204)


@router.delete("/conversations/{conversation_id}/held-prompts/{sender_message_id}")
async def discard_held_prompt(
    conversation_id: str, sender_message_id: str, runtime: Runtime
) -> dict[str, bool]:
    """Throw away one message that is waiting, and say whether there was one to throw.

    A held message has reached no backend, so this reaches none either: it comes out of
    the queue and is written down as discarded. Finding nothing is an ordinary outcome —
    a held message runs the moment the agent frees up — so it is a false rather than an
    error.
    """
    discarded = await runtime.system.discard_held_prompt(conversation_id, sender_message_id)
    return {"discarded": discarded}


@router.post("/conversations/{conversation_id}/permission-answers")
async def answer_permission_ask(
    conversation_id: str, body: PermissionAnswerBody, runtime: Runtime
) -> dict[str, bool]:
    """Give the backend the option a person chose, and say whether it landed.

    Not landing is an ordinary outcome — the ask was answered already, or its turn has
    since ended — so it is a false rather than an error.
    """
    landed = await runtime.system.answer_permission_ask(
        conversation_id, body.ask_id, body.option_id
    )
    return {"landed": landed}


# --- the backends on this machine ---------------------------------------------------------


@router.get("/backends")
async def read_backends(runtime: Runtime, refresh: bool = False) -> dict[str, Any]:
    """What each backend is right now. Probed when asked, then kept until asked again."""
    snapshots = await runtime.backend_snapshots.snapshots(refresh=refresh)
    return {"backends": [_snapshot_json(snapshot) for snapshot in snapshots]}


@router.post("/backends/{backend_key}/update")
async def update_backend(
    backend_key: ConversationBackendKey, runtime: Runtime
) -> dict[str, Any]:
    """Run this backend's update, then look again and say which of three things happened."""
    return _update_result_json(await runtime.backend_snapshots.update_backend(backend_key))


# --- turning values into JSON ---------------------------------------------------------------


def _start_request(body: StartConversationBody) -> ConversationStartRequest:
    identity_environment_variables = tuple((body.identity_environment_variables or {}).items())
    if body.role_text is None and identity_environment_variables:
        raise HTTPException(
            status_code=422,
            detail="identity environment variables belong to a role, so role_text is required",
        )
    role_materials = (
        None
        if body.role_text is None
        else ConversationRoleMaterials(
            role_text=body.role_text,
            identity_environment_variables=identity_environment_variables,
        )
    )
    return ConversationStartRequest(
        conversation_id=body.conversation_id,
        backend_key=body.backend_key,
        model=body.model,
        reasoning_effort=body.reasoning_effort,
        role_materials=role_materials,
        workspace_folder=_workspace_folder(body.workspace_folder),
        access=body.access,
    )


def _workspace_folder(typed: str | None) -> Path | None:
    """A folder as somebody typed it, turned into the one the agent will run in.

    A person writes ``~/Coding``; only a shell knows what that means, and a start request
    carries values that are already resolved. Expanding it here — where typed text becomes
    a path — is what makes the two agree. Without it the request is refused for not being
    absolute, which is true and useless.

    Anything else is left exactly as it was written. A relative folder is not made absolute
    against whatever directory this server happens to have been started in: that would be a
    guess, and being refused is the right answer to it.
    """
    return None if typed is None else Path(typed).expanduser()


async def _require_conversation(
    runtime: ConversationRuntime, conversation_id: str
) -> None:
    if await runtime.store.read_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail=f"no conversation {conversation_id}")


async def _conversation_view(
    runtime: ConversationRuntime, conversation_id: str
) -> dict[str, Any]:
    record = await runtime.store.read_conversation(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no conversation {conversation_id}")
    return {
        "conversation_id": record.conversation_id,
        "backend_key": str(record.backend_key),
        "model": record.model,
        "reasoning_effort": record.reasoning_effort,
        "workspace_folder": str(record.workspace_folder),
        "access": str(record.access),
        "role_text": record.role_text,
        # The names only: a surface shows which identity a conversation carries, and the
        # values are the identity itself and have no business coming back out.
        "identity_environment_variable_names": [
            name for name, _value in record.identity_environment_variables
        ],
        # The agent's own menu, as it last reported it. It comes off the conversation
        # rather than off a running child, so it is there for the person opening a
        # conversation to write the first message into it.
        "available_commands": [
            {
                "name": command.name,
                "description": command.description,
                "argument_hint": command.argument_hint,
            }
            for command in record.available_commands
        ],
        "latest_sequence": record.latest_sequence,
        "is_running": await runtime.system.is_running(conversation_id),
        "held_prompt_count": await runtime.system.held_prompt_count(conversation_id),
        "pending_permission_ask": await _pending_permission_ask(runtime, conversation_id),
    }


async def _pending_permission_ask(
    runtime: ConversationRuntime, conversation_id: str
) -> dict[str, Any] | None:
    """The ask that is waiting right now, whole.

    Which ask is waiting is a fact about the running turn and only the system knows it;
    what the ask says is a row and only the record has it. So the id comes from one and
    the words from the other.
    """
    waiting = await runtime.system.pending_permission_ask_ids(conversation_id)
    if not waiting:
        return None
    events = await runtime.store.read_events_after(conversation_id, 0)
    for event in reversed(events):
        payload = event.payload
        if isinstance(payload, PermissionAskedEventPayload) and payload.ask_id in waiting:
            return {
                "ask_id": payload.ask_id,
                "title": payload.title,
                "detail": payload.detail,
                "options": [
                    {
                        "option_id": option.option_id,
                        "label": option.label,
                        "option_kind": option.option_kind,
                    }
                    for option in payload.options
                ],
            }
    return None


async def _kept_message_content(
    runtime: ConversationRuntime, conversation_id: str, sent: list[SentPiece]
) -> MessageContent:
    """The message as the record will hold it, with the bytes already kept.

    This is the one place the two shapes meet. What arrives carries bytes, because that is
    how a browser hands a picture over; what the record holds names the file those bytes
    were kept as, because a row is read a thousand times and bytes belong beside it. Only
    the arriving shape ever carries data, and it stops here.
    """
    pieces: list[MessagePiece] = []
    for piece in sent:
        match piece:
            case SentTextPiece():
                pieces.append(MessageText(text=piece.text))
            case SentImagePiece():
                kept = await runtime.message_files.keep(
                    conversation_id, _decoded(piece.data), media_type=piece.media_type
                )
                pieces.append(
                    MessageImage(
                        stored_file_id=kept.stored_file_id,
                        media_type=piece.media_type,
                        file_name=piece.file_name,
                    )
                )
    return tuple(pieces)


def _decoded(data: str) -> bytes:
    """The bytes a piece arrived carrying, or a plain refusal of the request.

    Bytes that will not decode are a broken request rather than a delivery that could not
    happen, so this is the one thing about a send that is an error instead of a fate.
    """
    try:
        return b64decode(data, validate=True)
    except BinasciiError as not_bytes:
        raise HTTPException(
            status_code=422, detail="a piece's data is not base64"
        ) from not_bytes


def _event_json(event: StoredConversationEvent) -> dict[str, Any]:
    return {
        "conversation_id": event.conversation_id,
        "sequence": event.sequence,
        "kind": str(event.kind),
        "payload": json.loads(conversation_event_payload_to_canonical_json(event.payload)),
        "created_at": event.created_at,
    }


def _fate_json(fate: object) -> dict[str, Any]:
    match fate:
        case PromptDeliveryStarted():
            return {"fate": "started"}
        case PromptDeliveryQueued(queue_position=position):
            return {"fate": "queued", "queue_position": position}
        case PromptDeliveryInjected():
            return {"fate": "injected"}
        case PromptDeliveryRefused(refusal_reason=reason):
            return {"fate": "refused", "refusal_reason": str(reason)}
        case _:  # pragma: no cover - the fate type is closed
            raise AssertionError(f"unknown delivery fate {fate!r}")


def _snapshot_json(snapshot: BackendSnapshot) -> dict[str, Any]:
    identity = snapshot.identity
    advisory = snapshot.update_advisory
    return {
        "backend_key": str(snapshot.backend_key),
        "installed": snapshot.installed,
        "executable_path": snapshot.executable_path,
        "version": snapshot.version,
        "identity": (
            None
            if identity is None
            else {
                "status": str(identity.status),
                "account_label": identity.account_label,
                "detail": identity.detail,
                "login_command": identity.login_command,
            }
        ),
        "available_models": [
            {
                "model_id": model.model_id,
                "display_name": model.display_name,
                "detail": model.detail,
                "reasoning_effort_options": list(model.reasoning_effort_options),
            }
            for model in snapshot.available_models
        ],
        "reasoning_effort_options": list(snapshot.reasoning_effort_options),
        "default_model_id": snapshot.default_model_id,
        "default_reasoning_effort": snapshot.default_reasoning_effort,
        "update_advisory": (
            None
            if advisory is None
            else {
                "install_method": str(advisory.install_method),
                "update_command": (
                    None
                    if advisory.update_command is None
                    else " ".join(advisory.update_command)
                ),
                "latest_version": advisory.latest_version,
                "update_available": advisory.update_available,
                "detail": advisory.detail,
            }
        ),
        "diagnoses": list(snapshot.diagnoses),
    }


def _update_result_json(result: BackendUpdateResult) -> dict[str, Any]:
    return {
        "outcome": str(result.outcome),
        "detail": result.detail,
        "output_tail": result.output_tail,
    }


# --- the live tail as an event stream -------------------------------------------------------


def _stream_frame(stream_name: str, payload: Mapping[str, Any]) -> str:
    return f"event: {stream_name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _live_frame_json(frame: ConversationTailItem) -> Mapping[str, Any] | None:
    match frame:
        case AgentMessageDeltaFrame(text_delta=text_delta):
            return {"frame": "agent_message_delta", "text_delta": text_delta}
        case ModelThinkingFrame():
            return {"frame": "model_thinking"}
        case ToolCallProgressFrame(tool_call_id=tool_call_id, detail=detail):
            return {
                "frame": "tool_call_progress",
                "tool_call_id": tool_call_id,
                "detail": detail,
            }
        case _:  # pragma: no cover - the frame type is closed
            return None


async def _tail_stream(
    runtime: ConversationRuntime, conversation_id: str, after: int
) -> AsyncIterator[str]:
    subscription = runtime.live_tail.subscribe(conversation_id)
    heartbeat_seconds = runtime.sse_heartbeat_ms / 1000
    # A shutdown drains its connections before it ever reaches the lifespan, so a tail
    # that only closed there would hold the whole shutdown open. It is tracked in the
    # same place the change stream is, and closed by the same signal handler — from
    # another thread, so the close is handed back to this stream's own loop.
    loop = asyncio.get_running_loop()

    def close_from_another_thread() -> None:
        loop.call_soon_threadsafe(subscription.close)

    forget_closer = register_open_stream_closer(close_from_another_thread)
    try:
        replayed = await runtime.store.read_events_after(conversation_id, after)
        highest_replayed = replayed[-1].sequence if replayed else after
        for event in replayed:
            yield _stream_frame(COMMITTED_EVENT_STREAM_NAME, _event_json(event))
        while True:
            try:
                item = await asyncio.wait_for(
                    subscription.next_item(), timeout=heartbeat_seconds
                )
            except TimeoutError:
                # Nothing has happened. The comment keeps anything in between from calling
                # the connection dead, and a client that has gone makes this write fail,
                # which is how the stream finds out it has no reader.
                yield HEARTBEAT_FRAME
                continue
            if item is None:
                return
            if isinstance(item, StoredConversationEvent):
                if item.sequence <= highest_replayed:
                    # Committed while the replay was being read, so it has already been
                    # sent. This is the join, and it neither drops nor repeats a row.
                    continue
                highest_replayed = item.sequence
                yield _stream_frame(COMMITTED_EVENT_STREAM_NAME, _event_json(item))
                continue
            live_frame = _live_frame_json(item)
            if live_frame is not None:
                yield _stream_frame(LIVE_FRAME_STREAM_NAME, live_frame)
    finally:
        forget_closer()
        subscription.close()


__all__ = [
    "COMMITTED_EVENT_STREAM_NAME",
    "LIVE_FRAME_STREAM_NAME",
    "ConversationRuntime",
    "build_conversation_runtime",
    "router",
]
