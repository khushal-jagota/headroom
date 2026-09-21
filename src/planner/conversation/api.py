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
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from planner.conversation.backend_state import (
    BackendStateStore,
    resolve_usage_model_scopes,
)
from planner.conversation.backend_usage import (
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageService,
    BackendUsageWindow,
    production_backend_usage_service,
)
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    HeldPromptPromotionMode,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
    backend_supports_steer,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    HeldPromptsChangedFrame,
    ModelThinkingFrame,
    PermissionAskedEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallProgressFrame,
    UserInputAnswer,
    UserInputRequestedEventPayload,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.file_validation import (
    MAX_CONVERSATION_MESSAGE_FILE_BYTES,
    validated_file_media_type,
)
from planner.conversation.image_validation import (
    MAX_CONVERSATION_IMAGE_BYTES,
    MAX_CONVERSATION_MESSAGE_IMAGE_BYTES,
    validated_image_media_type,
)
from planner.conversation.live_tail import ConversationLiveTail, ConversationTailItem
from planner.conversation.message_content import (
    MessageContent,
    MessageFile,
    MessageImage,
    MessagePiece,
    MessageText,
    message_content_json_entries,
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
from planner.conversation.storage import (
    ConversationRecord,
    ConversationStore,
    StoredConversationEvent,
)
from planner.conversation.system import SqliteProcessConversationSystem
from planner.conversation.voice_transcription import (
    VoiceTranscriptionFailed,
    VoiceTranscriptionUnconfigured,
    transcribe_conversation_audio,
)
from planner.core.authctx import RequestContext, request_context, require_owner
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.core.response_compression import answers_with_an_event_stream
from planner.core.sse import HEARTBEAT_FRAME, register_open_stream_closer

Ctx = Annotated[RequestContext, Depends(request_context)]

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
    backend_usage: BackendUsageService
    message_files: ConversationMessageFiles
    database_path: str
    sse_heartbeat_ms: int
    backend_state: BackendStateStore | None = None

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
        backend_usage=production_backend_usage_service(),
        message_files=message_files,
        database_path=db_path,
        sse_heartbeat_ms=sse_heartbeat_ms,
        backend_state=BackendStateStore(db_path, busy_timeout_ms=db_busy_timeout_ms),
    )


def _runtime(request: Request) -> ConversationRuntime:
    runtime = getattr(request.app.state, "conversation", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="the conversation system is unavailable")
    if not isinstance(runtime, ConversationRuntime):  # pragma: no cover - composition error
        raise HTTPException(status_code=503, detail="the conversation system is unavailable")
    return runtime


Runtime = Annotated[ConversationRuntime, Depends(_runtime)]


def _require_mutable_conversation(runtime: ConversationRuntime, conversation_id: str) -> None:
    """Reject writes to a Ticket's past conversation.

    A conversation with no Ticket association belongs to another surface, such as the
    Chief, and remains mutable. A Ticket association makes the Ticket's active pointer
    authoritative.
    """
    conn = connect(runtime.database_path)
    try:
        row = conn.execute(
            "SELECT ticket_conversations.ticket_id, tickets.conversation_id "
            "FROM ticket_conversations JOIN tickets "
            "ON tickets.id = ticket_conversations.ticket_id "
            "WHERE ticket_conversations.conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is not None and row["conversation_id"] != conversation_id:
        raise HTTPException(
            status_code=409,
            detail="the conversation is not the Ticket's active conversation",
        )


def _require_unassociated_send_conversation(
    runtime: ConversationRuntime, conversation_id: str
) -> None:
    """Keep the raw send primitive outside every employee-owned conversation."""
    conn = connect(runtime.database_path)
    try:
        owned = conn.execute(
            "SELECT 1 FROM ticket_conversations WHERE conversation_id = ? "
            "UNION ALL SELECT 1 FROM agents WHERE conversation_id = ? LIMIT 1",
            (conversation_id, conversation_id),
        ).fetchone()
        identity_row = conn.execute(
            "SELECT identity_environment_variables FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    finally:
        conn.close()
    identity = (
        {}
        if identity_row is None
        else dict(json.loads(str(identity_row["identity_environment_variables"])))
    )
    if owned is not None or identity.get("PLAN_ACTOR") in {
        "worker",
        "chief",
        "sprint_item_supervisor",
    }:
        raise HTTPException(
            status_code=409,
            detail="employee conversations accept messages only through Send Message",
        )


# --- what the routes are sent -------------------------------------------------------------


class StartConversationBody(BaseModel):
    """A start request as JSON. The id and the model are asked for; the rest may be left
    out and take its floor."""

    conversation_id: str
    model: str
    backend_key: ConversationBackendKey | None = None
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


class SentFilePiece(BaseModel):
    """A document or data file, with bytes that stop at the intake boundary."""

    piece: Literal["file"]
    data: str
    media_type: str
    file_name: str


type SentPiece = SentTextPiece | SentImagePiece | SentFilePiece


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
    mode: PromptDeliveryMode = PromptDeliveryMode.queue
    model_change: str | None = None
    reasoning_effort_change: str | None = None
    sender_message_id: str | None = None
    sent_at_unix_milliseconds: int | None = None


class OwnerSendBody(BaseModel):
    """A message to a Ticket's worker or to the Chief, as JSON.

    The same message a ``SendBody`` carries, said to somebody who may not have a
    conversation yet. It lives here because this module is where the shape a message
    arrives in is decided, and there is one of those.

    ``conversation_id`` is which conversation this message is for. Absent says the sender
    has none — and this message is what brings one into being.

    ``backend_key``, ``model`` and ``reasoning_effort`` are what the message says it runs
    under, and an absent one is not a choice. To a conversation that does not exist yet
    they are what to create it on; to one that does they are what to move it onto. A
    backend is only ever the first, because a backend is not something a conversation can
    be moved to.
    """

    conversation_id: str | None = None
    backend_key: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    content: list[SentPiece]
    sender_label: str
    mode: PromptDeliveryMode = PromptDeliveryMode.queue
    sender_message_id: str | None = None
    sent_at_unix_milliseconds: int | None = None


# The audio a voice note may arrive as. WebM/Opus is what a recording browser produces;
# the rest are what other recorders on the allowed platforms hand over.
VOICE_AUDIO_MEDIA_TYPES = frozenset({"audio/webm", "audio/mp4", "audio/ogg", "audio/wav"})

# The provider's own ceiling on one clip (Groq refuses larger files), applied to the
# decoded bytes before anything is kept or sent.
MAX_VOICE_AUDIO_BYTES = 25 * 1024 * 1024

# How much of a finished tool call's output a public read carries. The thread draws a
# one-line summary from this field and shows the output itself only when a reader opens
# the fold, so an open that carried every past tool call's whole output paid for text
# nobody had asked to see. The record keeps the whole text, and the fold asks for it by
# the row's own position.
#
# The figure comes from drawing every stored tool call line twice, whole and capped:
# 1 KB carries a fifth of the largest conversation's tool output and moves 11 lines of
# 80,070, each one a row whose subject was read out of more than 1 KB of output. Larger
# caps save little, because most of the weight is in rows of a few kilobytes.
PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS = 1024


def _canonical_voice_audio_media_type(value: str) -> str | None:
    """Return the allowlisted base type from a browser's full MIME value.

    Mobile MediaRecorder implementations can append codec parameters, such as
    ``audio/webm;codecs=opus``. Those parameters describe the same allowlisted
    container and do not belong in the stored file's canonical media type.
    """
    base_type = value.partition(";")[0].strip().lower()
    return base_type if base_type in VOICE_AUDIO_MEDIA_TYPES else None


class VoiceTranscriptionBody(BaseModel):
    """Fresh browser-held voice bytes to turn into words.

    There is no stored-file alternative. The browser keeps the clip through a failed
    request and sends the same bytes again on retry.
    """

    model_config = ConfigDict(extra="forbid")

    audio: str
    media_type: str = "audio/webm"


class PermissionAnswerBody(BaseModel):
    ask_id: str
    option_id: str


class UserInputQuestionAnswerBody(BaseModel):
    answers: list[str]


class UserInputAnswerBody(BaseModel):
    request_id: str
    answers: dict[str, UserInputQuestionAnswerBody]


class PromoteHeldPromptBody(BaseModel):
    mode: HeldPromptPromotionMode


class AdvanceOwnerReadBody(BaseModel):
    through_sequence: int


class ModelEnablementBody(BaseModel):
    enabled: bool


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


@router.post("/conversations/{conversation_id}/owner-read")
async def advance_owner_read(
    conversation_id: str,
    body: AdvanceOwnerReadBody,
    runtime: Runtime,
    ctx: Ctx,
) -> dict[str, int]:
    require_owner(ctx)
    if body.through_sequence < 0:
        raise HTTPException(status_code=422, detail="through_sequence must be non-negative")
    record = await runtime.store.advance_owner_read_through_sequence(
        conversation_id, body.through_sequence
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"no conversation {conversation_id}")
    return {"owner_read_through_sequence": record.owner_read_through_sequence}


# One page of a conversation's record is capped here, so a reader walking backwards
# through a long one asks a bounded question every time.
MAXIMUM_EVENT_PAGE = 100


@router.get("/conversations/{conversation_id}/events")
async def read_conversation_events(
    conversation_id: str,
    runtime: Runtime,
    after: int | None = None,
    before: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """This conversation's record, forwards from a position or backwards from its end.

    ``after`` reads on from where a caller got to. ``limit``, with an optional ``before``,
    reads the other direction: the last events, then the page before those. A reader who
    is catching up wants the first; a reader who has just arrived wants the second.
    """
    if limit is not None and (limit < 1 or limit > MAXIMUM_EVENT_PAGE):
        raise PlannerError(
            ErrorCode.validation,
            f"limit must be between 1 and {MAXIMUM_EVENT_PAGE}",
            {"limit": limit},
        )
    if before is not None and limit is None:
        raise PlannerError(
            ErrorCode.validation, "before needs a limit: it reads backwards", {"before": before}
        )
    if before is not None and after is not None:
        raise PlannerError(
            ErrorCode.validation,
            "a page reads one direction: after, or before",
            {"after": after, "before": before},
        )
    record = await _require_conversation(runtime, conversation_id)
    reads_backwards = after is None
    if reads_backwards and limit is not None:
        events = await runtime.store.read_latest_events(
            conversation_id, before_sequence=before, limit=limit
        )
        # Reading back from the end, more to come means older rows before this page.
        has_more = bool(events) and events[0].sequence > 1
    else:
        # Reading on from a position, more to come means rows after this page. One row
        # past the limit answers that without a second query, and is not returned.
        events = await runtime.store.read_events_after(conversation_id, after or 0)
        has_more = limit is not None and len(events) > limit
        if limit is not None:
            events = events[:limit]
    return {
        "events": [_public_event_json(event, backend_key=record.backend_key) for event in events],
        "has_more": has_more,
    }


@router.get("/conversations/{conversation_id}/events/{sequence}/detail")
async def read_conversation_event_detail(
    conversation_id: str, sequence: int, runtime: Runtime
) -> dict[str, Any]:
    """The whole output of one finished tool call.

    An open carries a capped copy of this field. A reader who opens the fold asks here
    for the rest of it, so the bytes follow what somebody chose to look at.
    """
    record = await _require_conversation(runtime, conversation_id)
    event = await runtime.store.read_event(conversation_id, sequence)
    if event is None or not isinstance(event.payload, ToolCallFinishedEventPayload):
        raise HTTPException(
            status_code=404,
            detail=(f"conversation {conversation_id} has no finished tool call at {sequence}"),
        )
    return {
        "detail": _readable_tool_call_detail(event.payload.detail, backend_key=record.backend_key)
    }


@router.get("/conversations/{conversation_id}/tail")
@answers_with_an_event_stream
async def tail_conversation(
    conversation_id: str, runtime: Runtime, after: int = 0
) -> StreamingResponse:
    """Replay the rows after a position, then keep going live.

    The watch is registered before a single row is read back, so anything committed while
    the replay is being read is already waiting rather than lost. The replay's last row is
    the high-water mark, and anything at or below it that arrives on the watch has already
    been sent — so the join has no hole in it and nothing arrives twice.
    """
    record = await _require_conversation(runtime, conversation_id)
    return StreamingResponse(
        _tail_stream(runtime, conversation_id, record.backend_key, after),
        media_type="text/event-stream",
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
    _require_mutable_conversation(runtime, conversation_id)
    _require_unassociated_send_conversation(runtime, conversation_id)
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
    return delivery_fate_json(fate)


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


@router.post("/voice-transcriptions")
async def transcribe_voice_note(body: VoiceTranscriptionBody, request: Request) -> dict[str, str]:
    """Turn browser-held audio into words without touching a conversation.

    This route does not resolve, create, link, or store a conversation. A failed request
    leaves the sole copy with the browser, which retries by sending the same bytes again.
    """
    if _canonical_voice_audio_media_type(body.media_type) is None:
        raise HTTPException(
            status_code=422, detail=f"unsupported voice media type {body.media_type}"
        )
    contents = _decoded_voice_audio(body.audio)
    config = request.app.state.config
    try:
        transcript = await transcribe_conversation_audio(
            contents,
            base_url=config.voice_transcription_base_url,
            model=config.voice_transcription_model,
            api_key=config.voice_transcription_api_key,
        )
    except VoiceTranscriptionUnconfigured as unconfigured:
        raise HTTPException(status_code=503, detail=str(unconfigured)) from unconfigured
    except VoiceTranscriptionFailed as failed:
        raise HTTPException(status_code=502, detail=str(failed)) from failed
    return {"transcript": transcript}


@router.post("/conversations/{conversation_id}/interrupt", status_code=204)
async def interrupt_conversation(conversation_id: str, runtime: Runtime) -> Response:
    """Stop the running turn. What was held behind it runs from here."""
    _require_mutable_conversation(runtime, conversation_id)
    await runtime.system.interrupt(conversation_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/kill", status_code=204)
async def kill_conversation(conversation_id: str, runtime: Runtime) -> Response:
    """Stop the running turn and throw away everything waiting behind it.

    This is what pressing New uses: an interrupt alone would free the agent and let the
    held messages run, which is exactly what starting again must not do.
    """
    _require_mutable_conversation(runtime, conversation_id)
    await runtime.system.kill(conversation_id)
    return Response(status_code=204)


@router.delete("/conversations/{conversation_id}/held-prompts/{held_prompt_id}")
async def discard_held_prompt(
    conversation_id: str, held_prompt_id: str, runtime: Runtime
) -> dict[str, bool]:
    """Throw away one message that is waiting, and say whether there was one to throw.

    A held message has reached no backend, so this reaches none either: it comes out of
    the queue and is written down as discarded. Finding nothing is an ordinary outcome —
    a held message runs the moment the agent frees up — so it is a false rather than an
    error.
    """
    _require_mutable_conversation(runtime, conversation_id)
    discarded = await runtime.system.discard_held_prompt(conversation_id, held_prompt_id)
    return {"discarded": discarded}


@router.post("/conversations/{conversation_id}/held-prompts/{held_prompt_id}/promote")
async def promote_held_prompt(
    conversation_id: str,
    held_prompt_id: str,
    body: PromoteHeldPromptBody,
    runtime: Runtime,
) -> dict[str, Any]:
    """Claim one waiting message and deliver it now in the selected mode."""
    _require_mutable_conversation(runtime, conversation_id)
    fate = await runtime.system.promote_held_prompt(conversation_id, held_prompt_id, body.mode)
    if fate is None:
        return {"promoted": False}
    return {"promoted": True, **delivery_fate_json(fate)}


@router.post("/conversations/{conversation_id}/permission-answers")
async def answer_permission_ask(
    conversation_id: str, body: PermissionAnswerBody, runtime: Runtime
) -> dict[str, bool]:
    """Give the backend the option a person chose, and say whether it landed.

    Not landing is an ordinary outcome — the ask was answered already, or its turn has
    since ended — so it is a false rather than an error.
    """
    _require_mutable_conversation(runtime, conversation_id)
    landed = await runtime.system.answer_permission_ask(
        conversation_id, body.ask_id, body.option_id
    )
    return {"landed": landed}


@router.post("/conversations/{conversation_id}/user-input-answers")
async def answer_user_input(
    conversation_id: str, body: UserInputAnswerBody, runtime: Runtime
) -> dict[str, bool]:
    """Give the backend the complete answer map for one question request."""
    _require_mutable_conversation(runtime, conversation_id)
    landed = await runtime.system.answer_user_input(
        conversation_id,
        body.request_id,
        tuple(
            UserInputAnswer(question_id=question_id, answers=tuple(answer.answers))
            for question_id, answer in body.answers.items()
        ),
    )
    return {"landed": landed}


# --- the backends on this machine ---------------------------------------------------------


@router.get("/backends")
async def read_backends(runtime: Runtime, refresh: bool = False) -> dict[str, Any]:
    """What each backend is right now. Probed when asked, then kept until asked again."""
    snapshots = await runtime.backend_snapshots.snapshots(refresh=refresh)
    return {"backends": [_snapshot_json(snapshot, runtime.backend_state) for snapshot in snapshots]}


@router.post("/backends/refresh")
async def refresh_backends(runtime: Runtime) -> dict[str, Any]:
    """Refresh provider usage and resolve it against the current backend catalogues."""
    usage_refresh = asyncio.gather(
        *(runtime.backend_usage.refresh(backend_key) for backend_key in ConversationBackendKey)
    )
    try:
        # Schedule provider reads before catalogue acquisition. A usage request must not
        # wait for version, identity, model-catalogue, or update-advisory probes.
        await asyncio.sleep(0)
        snapshots = await runtime.backend_snapshots.snapshots()
        refreshed = await usage_refresh
    finally:
        if not usage_refresh.done():
            usage_refresh.cancel()
        with suppress(asyncio.CancelledError):
            await usage_refresh
    by_backend = {snapshot.backend_key: snapshot for snapshot in snapshots}
    resolved = tuple(
        resolve_usage_model_scopes(result, by_backend[result.backend_key]) for result in refreshed
    )
    if runtime.backend_state is not None:
        for result in resolved:
            if result.outcome is BackendUsageOutcome.succeeded and result.observed_at is not None:
                runtime.backend_state.keep_successful_usage(result)
    return {
        "backends": [_snapshot_json(snapshot, runtime.backend_state) for snapshot in snapshots],
        "usage_outcomes": [_usage_outcome_json(result) for result in resolved],
    }


@router.put("/backends/{backend_key}/models/{model_id:path}/enablement")
async def put_model_enablement(
    backend_key: ConversationBackendKey,
    model_id: str,
    body: ModelEnablementBody,
    runtime: Runtime,
) -> dict[str, Any]:
    """Set whether a current catalogue model is offered by new-model pickers."""
    if not model_id or model_id != model_id.strip():
        raise HTTPException(status_code=422, detail="model_id must be trimmed text")
    snapshot = await runtime.backend_snapshots.snapshot(backend_key)
    if model_id not in {model.model_id for model in snapshot.available_models}:
        raise HTTPException(status_code=404, detail="model is not in the backend catalogue")
    if runtime.backend_state is None:
        raise HTTPException(status_code=503, detail="backend state is unavailable")
    runtime.backend_state.write_model_enablement(backend_key, model_id, body.enabled)
    return {"backend_key": str(backend_key), "model_id": model_id, "enabled": body.enabled}


@router.post("/backends/{backend_key}/update")
async def update_backend(backend_key: ConversationBackendKey, runtime: Runtime) -> dict[str, Any]:
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
) -> ConversationRecord:
    record = await runtime.store.read_conversation(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no conversation {conversation_id}")
    return record


async def _conversation_view(runtime: ConversationRuntime, conversation_id: str) -> dict[str, Any]:
    record = await runtime.store.read_conversation(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no conversation {conversation_id}")
    return {
        "conversation_id": record.conversation_id,
        "backend_key": str(record.backend_key),
        "supports_steer": backend_supports_steer(record.backend_key),
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
        # The conversation's composer catalog, as its backend last reported it. It comes
        # off the conversation rather than off a running child, so it is there when a
        # person opens the conversation to write the first message.
        "composer_catalog": [
            {
                "kind": str(entry.kind),
                "display_text": entry.display_text,
                "insertion_text": entry.insertion_text,
                "description": entry.description,
                "argument_hint": entry.argument_hint,
            }
            for entry in record.composer_catalog
        ],
        "latest_sequence": record.latest_sequence,
        "owner_read_through_sequence": record.owner_read_through_sequence,
        "is_running": await runtime.system.is_running(conversation_id),
        "held_prompts": [
            {
                "held_prompt_id": held.held_prompt_id,
                **message_content_json_entries(held.content),
                "sender_label": held.sender_label,
                "sender_message_id": held.sender_message_id,
                "sent_at_unix_milliseconds": held.sent_at_unix_milliseconds,
                "sender": (
                    None
                    if held.sender is None
                    else {"kind": held.sender.kind.value, "id": held.sender.id}
                ),
                "recipient": (
                    None
                    if held.recipient is None
                    else {"kind": held.recipient.kind.value, "id": held.recipient.id}
                ),
                "queue_reason": str(held.queue_reason),
            }
            for held in await runtime.system.held_prompts(conversation_id)
        ],
        "pending_permission_ask": await _pending_permission_ask(runtime, conversation_id),
        "pending_user_input": await _pending_user_input(runtime, conversation_id),
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


async def _pending_user_input(
    runtime: ConversationRuntime, conversation_id: str
) -> dict[str, Any] | None:
    """The pending request, rebuilt from its durable row after a refresh."""
    waiting = await runtime.system.pending_user_input_request_ids(conversation_id)
    if not waiting:
        return None
    events = await runtime.store.read_events_after(conversation_id, 0)
    for event in reversed(events):
        payload = event.payload
        if isinstance(payload, UserInputRequestedEventPayload) and payload.request_id in waiting:
            return {
                "request_id": payload.request_id,
                "questions": [
                    {
                        "question_id": question.question_id,
                        "header": question.header,
                        "question": question.question,
                        "options": [
                            {
                                "label": option.label,
                                "description": option.description,
                            }
                            for option in question.options
                        ],
                        "multi_select": question.multi_select,
                        "allow_other": question.allow_other,
                    }
                    for question in payload.questions
                ],
            }
    return None


async def _kept_message_content(
    runtime: ConversationRuntime, conversation_id: str, sent: list[SentPiece]
) -> MessageContent:
    return await conversation_message_content(runtime.message_files, conversation_id, sent)


async def conversation_message_content(
    message_files: ConversationMessageFiles, conversation_id: str, sent: list[SentPiece]
) -> MessageContent:
    """The message as the record will hold it, with the bytes already kept.

    This is the one place the two shapes meet. What arrives carries bytes, because that is
    how a browser hands a picture over; what the record holds names the file those bytes
    were kept as, because a row is read a thousand times and bytes belong beside it. Only
    the arriving shape ever carries data, and it stops here.

    It takes the files rather than the whole runtime because the owner-scoped send doors
    live in another module and need exactly this and nothing else.
    """
    # Prove every attachment before keeping any. A malformed later piece must reject
    # the whole request without leaving an earlier piece behind as an unnamed managed file.
    validated_images: list[tuple[bytes, str]] = []
    validated_files: list[tuple[bytes, str]] = []
    total_image_bytes = 0
    total_file_bytes = 0
    for piece in sent:
        if isinstance(piece, SentImagePiece):
            contents = _decoded_image(piece.data)
            total_image_bytes += len(contents)
            if total_image_bytes > MAX_CONVERSATION_MESSAGE_IMAGE_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail="a conversation message's images are too large",
                )
            try:
                media_type = validated_image_media_type(contents)
            except ValueError as invalid:
                raise HTTPException(status_code=422, detail=str(invalid)) from invalid
            validated_images.append((contents, media_type))
        elif isinstance(piece, SentFilePiece):
            contents = _decoded_file(piece.data)
            total_file_bytes += len(contents)
            if total_file_bytes > MAX_CONVERSATION_MESSAGE_FILE_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail="a conversation message's files are too large",
                )
            try:
                media_type = validated_file_media_type(contents, piece.file_name)
            except ValueError as invalid:
                raise HTTPException(status_code=422, detail=str(invalid)) from invalid
            validated_files.append((contents, media_type))

    pieces: list[MessagePiece] = []
    image_index = 0
    file_index = 0
    for piece in sent:
        match piece:
            case SentTextPiece():
                pieces.append(MessageText(text=piece.text))
            case SentImagePiece():
                contents, media_type = validated_images[image_index]
                image_index += 1
                kept = await message_files.keep(conversation_id, contents, media_type=media_type)
                pieces.append(
                    MessageImage(
                        stored_file_id=kept.stored_file_id,
                        media_type=media_type,
                        file_name=piece.file_name,
                    )
                )
            case SentFilePiece():
                contents, media_type = validated_files[file_index]
                file_index += 1
                kept = await message_files.keep(conversation_id, contents, media_type=media_type)
                pieces.append(
                    MessageFile(
                        stored_file_id=kept.stored_file_id,
                        media_type=media_type,
                        file_name=piece.file_name,
                        byte_count=kept.byte_count,
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
        raise HTTPException(status_code=422, detail="a piece's data is not base64") from not_bytes


def _decoded_image(data: str) -> bytes:
    """Decode one bounded image without trusting its media-type claim."""
    maximum_encoded_length = 4 * ((MAX_CONVERSATION_IMAGE_BYTES + 2) // 3)
    if len(data) > maximum_encoded_length:
        raise HTTPException(status_code=422, detail="a conversation image is too large")
    return _decoded(data)


def _decoded_file(data: str) -> bytes:
    """Decode one bounded document or data file without a large intermediate value."""
    maximum_encoded_length = 4 * ((MAX_CONVERSATION_MESSAGE_FILE_BYTES + 2) // 3)
    if len(data) > maximum_encoded_length:
        raise HTTPException(status_code=422, detail="a conversation message's files are too large")
    contents = _decoded(data)
    if len(contents) > MAX_CONVERSATION_MESSAGE_FILE_BYTES:
        raise HTTPException(status_code=422, detail="a conversation message's files are too large")
    return contents


def _decoded_voice_audio(data: str) -> bytes:
    """Decode one bounded voice clip, refusing anything past the provider's ceiling."""
    maximum_encoded_length = 4 * ((MAX_VOICE_AUDIO_BYTES + 2) // 3)
    if len(data) > maximum_encoded_length:
        raise HTTPException(status_code=422, detail="a voice clip is too large")
    contents = _decoded(data)
    if len(contents) > MAX_VOICE_AUDIO_BYTES:
        raise HTTPException(status_code=422, detail="a voice clip is too large")
    return contents


def _public_event_json(
    event: StoredConversationEvent, *, backend_key: ConversationBackendKey
) -> dict[str, Any]:
    payload = json.loads(conversation_event_payload_to_canonical_json(event.payload))
    if isinstance(event.payload, ToolCallFinishedEventPayload):
        readable = _readable_tool_call_detail(event.payload.detail, backend_key=backend_key)
        carried = (
            None if readable is None else readable[:PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS]
        )
        payload["detail"] = carried
        if carried is not None and readable is not None and len(carried) < len(readable):
            # A short output and a shortened one read the same, so the row says which
            # this is. The fold asks for the whole text only when there is more of it,
            # and the flag is written only on the rows it is true for, because the
            # bytes an open carries are the point of the cap.
            payload["detail_capped"] = True
    return {
        "conversation_id": event.conversation_id,
        "sequence": event.sequence,
        "kind": str(event.kind),
        "payload": payload,
        "created_at": event.created_at,
    }


def _readable_tool_call_detail(
    detail: str | None, *, backend_key: ConversationBackendKey
) -> str | None:
    """What a reader may be shown of a finished tool call, before any cap.

    Old Claude rows can hold image blocks with no readable text in them. There is
    nothing in one to show, so no public read carries it — not the events read, not the
    tail, and not the route the fold asks on.
    """
    if backend_key is ConversationBackendKey.claude and (
        _is_legacy_claude_image_only_detail(detail)
    ):
        return None
    return detail


def _is_legacy_claude_image_only_detail(detail: str | None) -> bool:
    """Whether old Claude detail is only base64 image blocks and has no readable text."""
    if detail is None:
        return False
    try:
        blocks = json.loads(detail)
    except (TypeError, ValueError):
        return False
    if not isinstance(blocks, list) or not blocks:
        return False
    return all(_is_claude_base64_image_block(block) for block in blocks)


def _is_claude_base64_image_block(block: object) -> bool:
    if (
        not isinstance(block, dict)
        or set(block) != {"type", "source"}
        or block.get("type") != "image"
    ):
        return False
    source = block.get("source")
    if (
        not isinstance(source, dict)
        or set(source) != {"type", "media_type", "data"}
        or source.get("type") != "base64"
    ):
        return False
    media_type = source.get("media_type")
    data = source.get("data")
    return (
        isinstance(media_type, str)
        and media_type.startswith("image/")
        and isinstance(data, str)
        and bool(data)
    )


def delivery_fate_json(fate: object) -> dict[str, Any]:
    match fate:
        case PromptDeliveryStarted():
            return {"fate": "started"}
        case PromptDeliveryQueued(queue_position=position):
            return {"fate": "queued", "queue_position": position}
        case PromptDeliveryInjected():
            return {"fate": "injected"}
        case PromptDeliveryRefused(refusal_reason=reason):
            return {"fate": "refused", "refusal_reason": str(reason)}
        case PromptDeliveryUncertain():
            return {"fate": "uncertain"}
        case _:  # pragma: no cover - the fate type is closed
            raise AssertionError(f"unknown delivery fate {fate!r}")


def _snapshot_json(
    snapshot: BackendSnapshot, backend_state: BackendStateStore | None = None
) -> dict[str, Any]:
    identity = snapshot.identity
    advisory = snapshot.update_advisory
    cached_usage = None if backend_state is None else backend_state.read_usage(snapshot.backend_key)
    enabled_by_model = {
        model.model_id: (
            True
            if backend_state is None
            else backend_state.model_is_enabled(snapshot.backend_key, model.model_id)
        )
        for model in snapshot.available_models
    }
    default_model_id = snapshot.default_model_id
    if default_model_id is not None and not enabled_by_model.get(default_model_id, True):
        default_model_id = None
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
                "enabled": enabled_by_model[model.model_id],
            }
            for model in snapshot.available_models
        ],
        "reasoning_effort_options": list(snapshot.reasoning_effort_options),
        "default_model_id": default_model_id,
        "default_reasoning_effort": snapshot.default_reasoning_effort,
        "update_advisory": (
            None
            if advisory is None
            else {
                "install_method": str(advisory.install_method),
                "update_command": (
                    None if advisory.update_command is None else " ".join(advisory.update_command)
                ),
                "latest_version": advisory.latest_version,
                "update_available": advisory.update_available,
                "detail": advisory.detail,
            }
        ),
        "diagnoses": list(snapshot.diagnoses),
        "cached_usage": (
            None
            if cached_usage is None
            else {
                "observed_at": _datetime_json(cached_usage.observed_at),
                "windows": [_usage_window_json(window) for window in cached_usage.windows],
            }
        ),
    }


def _update_result_json(result: BackendUpdateResult) -> dict[str, Any]:
    return {
        "outcome": str(result.outcome),
        "detail": result.detail,
        "output_tail": result.output_tail,
    }


def _usage_result_json(result: BackendUsageResult) -> dict[str, Any]:
    return {
        "backend_key": str(result.backend_key),
        "outcome": str(result.outcome),
        "detail": result.detail,
        "observed_at": None if result.observed_at is None else _datetime_json(result.observed_at),
        "windows": [_usage_window_json(window) for window in result.windows],
    }


def _usage_outcome_json(result: BackendUsageResult) -> dict[str, Any]:
    return {
        "backend_key": str(result.backend_key),
        "outcome": str(result.outcome),
        "detail": result.detail,
    }


def _usage_window_json(window: BackendUsageWindow) -> dict[str, Any]:
    return {
        "kind": str(window.kind),
        "used_percent": window.used_percent,
        "resets_at": _datetime_json(window.resets_at),
        "model_id": window.model_scope,
    }


def _datetime_json(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


# --- the live tail as an event stream -------------------------------------------------------


def _stream_frame(stream_name: str, payload: Mapping[str, Any]) -> str:
    return f"event: {stream_name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _live_frame_json(frame: ConversationTailItem) -> Mapping[str, Any] | None:
    match frame:
        case AgentMessageDeltaFrame(text_delta=text_delta):
            return {"frame": "agent_message_delta", "text_delta": text_delta}
        case ModelThinkingFrame():
            return {"frame": "model_thinking"}
        case HeldPromptsChangedFrame():
            return {"frame": "held_prompts_changed"}
        case ToolCallProgressFrame(tool_call_id=tool_call_id, detail=detail):
            return {
                "frame": "tool_call_progress",
                "tool_call_id": tool_call_id,
                "detail": detail,
            }
        case _:  # pragma: no cover - the frame type is closed
            return None


async def _tail_stream(
    runtime: ConversationRuntime,
    conversation_id: str,
    backend_key: ConversationBackendKey,
    after: int,
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
            yield _stream_frame(
                COMMITTED_EVENT_STREAM_NAME,
                _public_event_json(event, backend_key=backend_key),
            )
        # The subscription exists before this wake. Every new binder refreshes the
        # canonical snapshot once, even when it is empty: a last-row removal can happen
        # between the binder's view read and this subscription just as an enqueue can.
        yield _stream_frame(
            LIVE_FRAME_STREAM_NAME,
            {"frame": "held_prompts_changed"},
        )
        while True:
            try:
                item = await asyncio.wait_for(subscription.next_item(), timeout=heartbeat_seconds)
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
                yield _stream_frame(
                    COMMITTED_EVENT_STREAM_NAME,
                    _public_event_json(item, backend_key=backend_key),
                )
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
