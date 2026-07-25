"""The contract between Panels' conversation system and the rest of Panels.

This module is the whole seam. The rest of Panels can start a conversation, send text
into it, interrupt it, and ask whether it is running. Nothing else crosses the boundary.

The conversation id is the identity everywhere. It is owned by the caller and it is the
only name this contract knows a conversation by. The ACP session id of the backend
process is an internal, rebindable attribute of the conversation system and appears
nowhere here.

The transcript read (fetching the events after a position, plus a live tail, and the
shape of an event record) and the concrete database schema are deferred to the real
build. They are deliberately absent from this module rather than sketched.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol


class ConversationBackendKey(StrEnum):
    """The production catalog of agent backends a conversation can run on.

    The catalog is a closed set rather than an open string because this contract states
    a per-backend capability as a fact: hermes can take text into a turn that is already
    running, codex and claude cannot. A fact stated about backends needs a closed set of
    backends to be stated about.
    """

    hermes = "hermes"
    codex = "codex"
    claude = "claude"


class ConversationAccess(StrEnum):
    """The access posture the agent runs under inside its workspace folder.

    ``full`` is the floor default and today the only posture. The backend-specific mode
    ids that implement full access belong to the conversation system's backend adapters
    and do not appear here. When a second posture is ruled it is added to this enum, and
    the ``access`` field already on the start request is what carries it.
    """

    full = "full"


# The three floor defaults. They exist so that an absent field still resolves to a
# concrete value — one system's defaults, shared by every implementation of this
# contract — not as an invitation to omit fields. Callers are expected to pass explicit
# values.
FLOOR_DEFAULT_BACKEND_KEY: Final = ConversationBackendKey.codex
FLOOR_DEFAULT_WORKSPACE_FOLDER: Final[Path] = Path.home() / "Coding"
FLOOR_DEFAULT_ACCESS: Final = ConversationAccess.full

BACKEND_KEYS_SUPPORTING_STEER: Final[frozenset[ConversationBackendKey]] = frozenset(
    {ConversationBackendKey.hermes}
)


def backend_supports_steer(backend_key: ConversationBackendKey) -> bool:
    """Whether this backend can take text into a turn that is already running.

    Steering support is a per-backend fact, not a runtime negotiation: hermes supports
    it, codex and claude do not. A steer aimed at a backend that cannot steer is refused
    with ``PromptDeliveryRefusalReason.backend_cannot_steer``.
    """
    return backend_key in BACKEND_KEYS_SUPPORTING_STEER


@dataclass(frozen=True, slots=True)
class ConversationRoleMaterials:
    """What the agent is told to be, and the identity its process runs under.

    ``role_text`` is the role the agent is given. ``identity_environment_variables`` are
    the name/value pairs its process runs with. The conversation system applies both
    without understanding them: it does not read meaning out of the role text and it
    does not interpret the variables.
    """

    role_text: str
    identity_environment_variables: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ConversationStartRequest:
    """The concrete, already-resolved values a conversation is created from.

    ``conversation_id`` is caller-owned and is the identity of this conversation
    everywhere afterwards. Every other field may be absent. An absent ``backend_key``,
    ``workspace_folder`` or ``access`` takes its floor default (codex, ``~/Coding``,
    full access), so a request carrying only a conversation id still produces a working
    conversation. ``model``, ``reasoning_effort`` and ``role_materials`` have no floor
    default: absent means the conversation is started without them, and the backend's
    own defaults apply.

    ``workspace_folder`` is the folder the agent runs in.
    """

    conversation_id: str
    backend_key: ConversationBackendKey | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    role_materials: ConversationRoleMaterials | None = None
    workspace_folder: Path | None = None
    access: ConversationAccess | None = None


@dataclass(frozen=True, slots=True)
class ResolvedConversationStart:
    """A start request with the floor defaults already applied.

    This is what a conversation is actually started with. ``backend_key``,
    ``workspace_folder`` and ``access`` are always concrete here because each has a
    floor default. ``model``, ``reasoning_effort`` and ``role_materials`` stay optional
    because none of them has one — there is nothing to fall back to, so absent stays
    absent.
    """

    conversation_id: str
    backend_key: ConversationBackendKey
    model: str | None
    reasoning_effort: str | None
    role_materials: ConversationRoleMaterials | None
    workspace_folder: Path
    access: ConversationAccess


class PromptDeliveryMode(StrEnum):
    """How a sent message should meet the agent. One parameter, three values.

    ``run_when_free`` is the default. If the agent is idle the message starts a turn
    straight away. If the agent is busy the message is held, and it runs when the agent
    frees up.

    ``send_now`` makes this message the running turn. If the agent is idle it behaves
    exactly like the default. If the agent is busy the incumbent turn is killed — its
    interruption is recorded as an event — and this message runs next, ahead of anything
    already held. If its own delivery then turns out to be impossible, the incumbent is
    dead all the same and the agent is free, so the messages that were already held run
    from that point.

    ``steer`` injects the text into the turn that is already running, without ending it.
    Whether a backend can do this is a per-backend fact: hermes can, codex and claude
    cannot. A steer is refused when no turn is running, or when the backend cannot
    steer.
    """

    run_when_free = "run_when_free"
    send_now = "send_now"
    steer = "steer"


class PromptDeliveryRefusalReason(StrEnum):
    """The genuine delivery impossibilities, and nothing else.

    ``no_such_conversation`` names a conversation id that has never been started, so
    there is nowhere for the text to go.

    ``backend_did_not_start`` names a backend process that would not spawn, so there is
    no live process to write to.

    ``session_did_not_load`` names a backend process that is alive but whose session
    would not load, so there is no valid bound session to write under.

    ``write_to_backend_failed`` names a write to the backend's wire that did not
    succeed, so the text did not reach it.

    ``no_running_turn_to_steer_into`` names a steer with no running turn to inject into.

    ``backend_cannot_steer`` names a steer aimed at a backend that cannot take text into
    a running turn.

    Busyness is not on this list and never will be: a busy agent is a message the system
    can hold, and nothing the system can hold is refused.
    """

    no_such_conversation = "no_such_conversation"
    backend_did_not_start = "backend_did_not_start"
    session_did_not_load = "session_did_not_load"
    write_to_backend_failed = "write_to_backend_failed"
    no_running_turn_to_steer_into = "no_running_turn_to_steer_into"
    backend_cannot_steer = "backend_cannot_steer"


@dataclass(frozen=True, slots=True)
class PromptDeliveryStarted:
    """The prompt request was written to the wire of a live backend process, under a
    valid bound session, before this call returned. The turn is now running.

    It does not claim the backend accepted the prompt. ACP has no acceptance
    acknowledgment: the response to a prompt request only arrives when the turn ends, so
    acceptance is not knowable at the moment the call returns and is not claimed here.

    It does not claim anything about how the turn goes. A turn's ending is an event, and
    this class carries no field that could name one.
    """


@dataclass(frozen=True, slots=True)
class PromptDeliveryQueued:
    """The message is held by the conversation system and will run when the agent frees
    up. ``queue_position`` is 1-based and counts held messages only.

    This is explicitly a conversation-system fact. The message has not reached any
    backend, and it is not claimed to have. The position is the message's place at the
    moment it was held; no later repositioning is reported.

    When a held message is eventually dequeued and delivered, that delivery has a fate
    of its own. It is recorded as events rather than returned, because the caller that
    sent it is long gone.
    """

    queue_position: int


@dataclass(frozen=True, slots=True)
class PromptDeliveryInjected:
    """The steered text actually entered the running turn's wire before this call
    returned. The turn was not ended by it and is still running.

    It does not claim the agent read or acted on the text; only that the text reached
    the wire of the turn that is running.
    """


@dataclass(frozen=True, slots=True)
class PromptDeliveryRefused:
    """The delivery was impossible, for the named reason. This text reached no backend.

    The claim is about this text and nothing else. A refused send-now has already killed
    the incumbent turn, recorded that interruption, and let the held messages run — the
    refusal says only that the send-now's own text never got anywhere.

    A refusal is only ever a genuine impossibility. It never means the system chose not
    to deliver, and it never means the agent was busy — a busy agent produces a held
    message, not a refusal.

    It does not claim anything about the conversation's future: a refused delivery says
    nothing about whether a later delivery would succeed.
    """

    refusal_reason: PromptDeliveryRefusalReason


# The fate of one delivery. Fate means it happened, never that it was attempted. Each
# member claims exactly the layer it names and no more: started means written to a live
# backend's wire, queued means held by the conversation system, injected means entered
# the running turn's wire, refused means impossible. No member carries a turn outcome,
# because a turn's ending is an event and never a return value.
type PromptDeliveryFate = (
    PromptDeliveryStarted | PromptDeliveryQueued | PromptDeliveryInjected | PromptDeliveryRefused
)


class ConversationAlreadyStarted(Exception):
    """A start request named a conversation id that already exists.

    The caller owns the id and creating a conversation is a real, once-only act.
    Quietly ignoring a second start would silently discard the second request's role
    materials and configuration, so it is an error instead.
    """


class ConversationSystem(Protocol):
    """Everything the rest of Panels can do to a conversation.

    Four operations: start one, send text into it, interrupt it, and ask whether it is
    running. There is no read of a conversation's backend or model — those are values
    the caller passed in, not questions this contract answers. The transcript read is
    deferred to the real build and is deliberately absent.

    Permissions are internal to the conversation system. They have no method here, only
    rules. When an agent asks for permission the ask always shows and always waits:
    there is no automatic answer of any kind, ever — not for any tool, not after any
    amount of time, not for any caller. The ask and the answer are both recorded as
    events. An answer lands only on an ask that is still pending on the turn that is
    live now; answering an unknown ask, an already-answered ask, or an ask whose turn
    has ended changes nothing. That bookkeeping is purely internal: deciding whether an
    answer lands consults nothing outside the conversation system.

    Every operation is async. Sending has to reach a child process over a wire before it
    can report its fate, and interrupting and reading run through the same internal
    locking, so all four are awaited.
    """

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        """Create the conversation named by the request's conversation id.

        The request carries concrete, already-resolved values: the caller-owned
        conversation id, the backend key, the model and reasoning effort, the role
        materials, the workspace folder the agent runs in, and the access posture.
        Absent fields take their floor defaults, so a request carrying only a
        conversation id still produces a working conversation.

        Creating the conversation writes its record as step one. No path may create a
        conversation without its record existing.

        Nothing fancy is returned. When this returns, the conversation exists and is
        addressable by its id.

        Raises ``ConversationAlreadyStarted`` if that conversation id already exists.
        """
        ...

    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
    ) -> PromptDeliveryFate:
        """Send text into a conversation. This is the only way text gets to an agent.

        ``mode`` decides how the text meets the agent — run when free, send now, or
        steer into the running turn — and defaults to run-when-free. See
        ``PromptDeliveryMode`` for what each one does against an idle and a busy agent.

        The return value is the fate of this delivery, and fate means it happened, never
        that it was attempted. See ``PromptDeliveryFate``: started, queued, injected, or
        refused. A refusal is only ever a genuine delivery impossibility — no such
        conversation, the backend would not spawn, the session would not load, the write
        failed. Busyness is never a refusal, and nothing the system can hold is refused.

        A turn's ending is never a return value. Completion, failure and interruption
        are all recorded as events; a failing turn also gets an error-log line of the
        conversation system's own.

        ``sender_label`` says who sent the text — the automatic loop or the owner, for
        example. It is recorded on the prompt event and it is display-only: nothing else
        consumes it and nothing branches on it.
        """
        ...

    async def interrupt(self, conversation_id: str) -> None:
        """Stop the running turn. It carries no text of its own.

        It is its own operation because stopping and sending are different acts: the UI
        stop button uses it directly, and send-now uses it internally to kill the
        incumbent turn before its own message runs.

        Stopping the turn frees the agent, so a message that was being held for it runs
        from that point — interrupting sends nothing, but it is not the end of the
        conversation's traffic.

        The turn's interruption is recorded as an event. There is nothing to return. If
        the conversation is idle, or the id names no conversation, nothing happens.
        """
        ...

    async def is_running(self, conversation_id: str) -> bool:
        """Whether a turn is running in this conversation right now.

        This is the one read the contract carries. A conversation that has not been
        started is not running. A turn that is waiting on a permission ask is still
        running.
        """
        ...
