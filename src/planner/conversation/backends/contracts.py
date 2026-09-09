"""The seam between the conversation system's core and one backend's child process.

This is an internal seam, not the contract Panels talks to. It exists so that the rules
live in exactly one place: **the core owns every contract semantic** — the held queue, the
four fates, steer gating, permission bookkeeping, which rows get written, when a child is
spawned and when it is stopped. An adapter owns one child process and its wire, and knows
none of that.

The division shows up in what each side is allowed to decide. An adapter never decides
that a message should wait, never decides that an ask has expired, and never writes a row.
The core never speaks a vendor's protocol. When an adapter cannot do what it was asked, it
says so by raising one of the named failures below, and the core turns that into the one
refusal reason the contract has for it:

- ``BackendSpawnFailed`` → ``backend_did_not_start``
- ``SessionLoadFailed`` → ``session_did_not_load``
- ``PromptWriteFailed`` → ``write_to_backend_failed``

Anything else an adapter raises is a fault in the adapter, not a delivery impossibility,
and is not translated into a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from planner.conversation.contracts import (
    ComposerCatalogEntry,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    PermissionAskOption,
    PlanEntry,
    ToolCallStatus,
    UserInputAnswer,
    UserInputQuestion,
)
from planner.conversation.message_content import MessageContent
from planner.conversation.message_files import ConversationMessageFiles


class BackendAdapterError(Exception):
    """Something an adapter was asked to do could not be done. Always say which."""


class BackendSpawnFailed(BackendAdapterError):
    """The backend process would not spawn, so there is no live process to write to."""


class SessionLoadFailed(BackendAdapterError):
    """The process is alive but there is no valid bound session to write under.

    This covers a session that would not be created, one that would not load, and — just
    as much — a resume that came back with a session that has lost the conversation's
    memory. A backend that quietly hands back a fresh thread in place of the one that was
    asked for has not loaded the session, and saying so here is what keeps that from being
    accepted in silence.
    """


class PromptWriteFailed(BackendAdapterError):
    """The prompt write did not complete, so an automatic retry is not safe."""


class PermissionAnswerWriteFailed(BackendAdapterError):
    """The answer to a permission ask did not reach the backend's wire.

    The answer lands only when the backend has it, so this is the difference between an
    answer that was recorded and one that was actually given.
    """


class UserInputAnswerWriteFailed(BackendAdapterError):
    """A complete answer map did not reach the backend's waiting question request."""


class NeedsRebind(BackendAdapterError):
    """This child cannot safely accept the prompt; replace it before the write.

    Some backends take a model or reasoning-effort change as a parameter of the next turn,
    and some can only be changed by starting again. An adapter of the second kind raises
    this instead of applying the change, and the core stops the child and starts a new one
    from the stored session cursor, under the same conversation, before writing again.

    An adapter also raises this when it knows before a new write that the child's wire is
    unusable, but the stored session can be resumed. It must not raise this for a failure
    during the current write because delivery is then uncertain and must not be retried.

    ``failed_child_recovery`` is true only for that known-broken-child case. The core uses
    it to keep a later recovery refusal in the conversation record. A value-change rebind
    leaves it false and retains the normal direct-refusal behavior.
    """

    def __init__(self, message: str, *, failed_child_recovery: bool = False) -> None:
        super().__init__(message)
        self.failed_child_recovery = failed_child_recovery


@dataclass(frozen=True, slots=True)
class TurnToken:
    """The core's name for one started turn.

    The core mints it, hands it to the adapter with the prompt that starts the turn, and
    the adapter puts it on everything it later reports about that turn. It is what lets
    the core tell this turn's news from the news of a turn that has already been ended and
    replaced. A cancelled turn's late-arriving live and operational events are dropped.
    One finished agent message may still be kept from the most recently ended turn: it is
    durable conversation content even when the backend reported its ending first.
    """

    conversation_id: str
    turn_number: int


@dataclass(frozen=True, slots=True)
class BackendSteerAccepted:
    """The provider admitted the message to the exact turn that the token names."""


@dataclass(frozen=True, slots=True)
class BackendSteerRefused:
    """The adapter proved that the message was not admitted to the target turn."""

    refusal_reason: PromptDeliveryRefusalReason


@dataclass(frozen=True, slots=True)
class BackendSteerUncertain:
    """The adapter cannot prove admission or non-admission after the attempt."""


type BackendSteerOutcome = BackendSteerAccepted | BackendSteerRefused | BackendSteerUncertain


@dataclass(frozen=True, slots=True)
class BackendPermissionAsk:
    """A permission ask exactly as the backend raised it.

    The options are the backend's own list. The conversation system holds no catalog of
    answers, adds none of its own, and never answers one itself.
    """

    ask_id: str
    title: str
    detail: str | None
    options: tuple[PermissionAskOption, ...]


@dataclass(frozen=True, slots=True)
class BackendUserInputRequest:
    """One ordered, complete request for answers raised by a backend."""

    request_id: str
    questions: tuple[UserInputQuestion, ...]


class BackendEventSink(Protocol):
    """How an adapter tells the core what its backend just did.

    Every turn fact carries the turn token it belongs to, because the core alone decides
    whether that turn is still the one running. Finished agent messages are the narrow
    exception to live-only acceptance: the most recently ended turn may still deliver one
    as durable conversation content. Calls return as soon as the core has taken the fact:
    they are handed to one queue per conversation and worked through in order, so an
    adapter's reporting is never blocked by whatever the core does about it.

    The one call that carries no turn token is the session cursor. It is not a fact about a
    turn — it is the conversation's durable session identity, and it has to be kept
    whatever turn happened to be running when it changed.
    """

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        """A piece of an agent message that has not finished. Shown live, never stored."""

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        """The model emitted private reasoning just now — and that is the whole message.

        There is no payload and there will not be one. What the model reasoned is dropped
        by the adapter where it arrives, exactly as it always has been; this says only that
        it happened, so a turn with nothing else to show yet can still show it is alive.

        Report it as it arrives. The core decides how often anyone is told, because a burst
        of reasoning is one fact — the agent is working — however many pieces it came in.
        """

    async def agent_message_completed(
        self, turn_token: TurnToken, content: MessageContent
    ) -> None:
        """The whole of a finished agent message.

        Nearly always one piece of written words, which is what an agent's message nearly
        always is. A backend that hands back a file it produced reports that as a piece of
        the same message rather than as a sentence describing one.
        """

    async def tool_call_started(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None: ...

    async def tool_call_progress(
        self, turn_token: TurnToken, *, tool_call_id: str, detail: str
    ) -> None:
        """Output from a tool call that has started and has not finished.

        Shown live and then forgotten, exactly like a message delta: it is not a row, and
        the tool call's own finish is what the record keeps. ``tool_call_id`` is the id the
        call was reported started under, so a surface can put the output where it belongs.

        An adapter reports this only where its wire genuinely carries in-progress output.
        Saying nothing is a perfectly good answer — a tool call that shows nothing until it
        finishes is not a gap, it is a backend that streams nothing.
        """

    async def tool_call_finished(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None: ...

    async def plan_updated(
        self, turn_token: TurnToken, entries: tuple[PlanEntry, ...]
    ) -> None:
        """The agent's plan, whole, as it now stands.

        Report the entire plan every time it changes rather than what moved in it: the
        core writes one row per update and the newest row is the answer on its own, so a
        reader never has to rebuild a plan out of a conversation's history.

        A backend that has no plan on its wire reports nothing here, and the conversation
        simply has no plan — which is a true thing to show and needs no stand-in.
        """

    async def token_usage_reported(
        self,
        turn_token: TurnToken,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        """What this turn has cost, as the backend counts it.

        Report what the backend actually said and nothing else: a count it did not give is
        ``None``, never zero, because a backend silent about cached tokens has not said
        there were none. Only claude knows about money; the other two leave ``cost_usd``
        absent rather than computing one.

        Report it as it arrives. A backend that reports running totals reports them; the
        core writes what it is told.
        """

    async def context_compacted(self, turn_token: TurnToken) -> None:
        """The backend summarised what came before and dropped it.

        No payload: that it happened, and where in the thread, is the whole of what a
        reader needs. Panels never asks for this — the backends do it on their own — and
        without it a transcript's earlier context goes silently.
        """

    async def permission_ask_raised(
        self, turn_token: TurnToken, ask: BackendPermissionAsk
    ) -> None:
        """The agent asked for permission.

        The core records the ask and shows it. Nothing is answered here and no answer is
        returned: an ask waits for a person, however long that takes, and the answer comes
        back the other way, through ``BackendChild.answer_permission_ask``.
        """

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        """The agent asked the owner questions and is waiting for the whole answer map."""

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        """A malformed question request was rejected rather than shown as permission."""

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        """The turn stopped running, on the backend's own account.

        An ending the core caused itself — an interruption, or a send-now killing the
        incumbent — is already recorded by the time this arrives, and the second ending is
        dropped. First ending wins, once per turn.
        """

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        """The backend minted or changed the session id this conversation resumes from."""

    async def composer_catalog_reported(
        self, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        """The composer catalog this backend reports, whole.

        Like the session cursor, this carries no turn token, because it is not a fact
        about a turn. It arrives when a session is established and it stands until the
        backend says otherwise, so it is kept whatever turn happened to be running — and
        it is kept after the child is gone, because the menu a person opens to write their
        first message is worth more than one that is only right while the agent is up.

        Report the whole list every time it changes rather than what moved in it. Hermes
        and Claude map their command reports into typed command entries. Codex joins its
        app-server catalog sources and reports one complete typed snapshot.
        """


class BackendChild(Protocol):
    """One backend child process, under one conversation, and its wire.

    An adapter holds whatever the vendor needs — a subprocess, a client object, a reader
    task — and nothing about the conversation's rules.

    **A turn's asks die with the turn.** Whenever a turn stops running, for any reason,
    every permission ask still outstanding on that turn has to be settled with the backend
    in the way that backend understands a withdrawn ask (a cancelled outcome, a denial),
    so no vendor call is left hanging. The core stops accepting answers for them at the
    same moment, so an ask settled this way never carries a person's answer.
    """

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        """Spawn the process and create or resume its session.

        ``resolved_start`` carries the values this child is to run on *now*, which for a
        conversation that has moved onto another model is not what it was first started
        with. A cursor of ``None`` means create a fresh session; a cursor means resume that
        one. The child is not usable until this has returned.

        Raises ``BackendSpawnFailed`` if the process would not start, and
        ``SessionLoadFailed`` if it started but the session did not.
        """

    async def write_prompt(
        self,
        turn_token: TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        automatic_compaction: bool = False,
    ) -> None:
        """Start a turn with this message, on these values.

        ``content`` is the complete message that goes to the backend. ``sender_content``
        is the exact message the sender wrote before the core added conversation-owned
        material such as the first-prompt role envelope. An adapter can use sender content
        to resolve backend-specific composer entries, but it must send ``content``.

        **Every piece goes over the wire, or none of it does.** The core has already
        refused a message carrying a piece this backend cannot be handed, so an adapter
        reaching one here is looking at a fault rather than at something to drop. A
        picture that silently did not arrive is a picture the person believes the agent
        has seen.

        A picture or a sound names a file the record kept; the bytes are on disk and the
        adapter is handed the way to reach them when it is made. Some backends want the
        path and some want the bytes, and both are one step from the same value.

        ``sender_label`` and ``mode`` travel with the text as the backend's own metadata
        — who sent it and how it was meant to meet the agent. Nothing branches on them,
        here or anywhere: a backend that has a metadata channel is handed them and a
        backend that has none drops them, and the turn runs the same either way.

        The change and the prompt are one operation because they are one act: the message
        carries the change, so **a change must not stand if the write does not**. How that
        is kept is the adapter's business — both as parameters of the same turn request,
        or an option set and put back if the prompt fails, or a rebind whose new child is
        discarded — but the two outcomes are the only ones allowed: the prompt is on the
        wire and the change is in force, or neither happened.

        A change of ``None`` means leave that value where it is.

        ``automatic_compaction`` names the one maintenance turn that the core requested.
        It is semantic intent, not a guess from prompt text. An adapter can use it to
        select a backend-native command and recognize that command's confirmation. It is
        false for every sender-authored turn, including one whose text is ``/compact``.

        Returns once the text is on the wire, not when the turn ends. Raises
        ``PromptWriteFailed`` if the current write did not complete, or ``NeedsRebind``
        if this child cannot safely accept a write that has not started.
        """

    async def steer(
        self, turn_token: TurnToken, content: MessageContent, *, sender_label: str
    ) -> BackendSteerOutcome:
        """Try to admit a message to the exact turn named by ``turn_token``.

        The adapter validates the target before transmission and never substitutes a newer
        turn. It returns one explicit outcome and never retries or falls back to a prompt.
        """

    async def cancel_running_turn(self) -> None:
        """Stop the turn that is running. The child stays alive for the next one.

        **Returns once the backend's own account of that turn has ended**, not merely once
        the stop was sent. The core writes the next prompt the moment this returns — that
        is what a send-now is — and a backend still finishing the turn it was told to drop
        has not freed itself for another. A message that arrives into that gap is one the
        backend may hold for later, answer with a note about holding it, or lose; none of
        which is the turn the caller was told had started.

        An adapter whose backend gives it no way to know when the turn finished bounds the
        wait rather than waiting forever. Giving up on the wait changes nothing about the
        record: the core wrote the ending when it decided on it, and it stands either way.
        """

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        """Give the backend the option a person chose for one of its asks.

        Raises ``PermissionAnswerWriteFailed`` if it did not reach the backend, in which
        case the answer has not landed and the ask is still waiting.
        """

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> None:
        """Give the backend every answer for one pending question request.

        Raises ``UserInputAnswerWriteFailed`` when the answer map did not land.
        """

    async def stop(self) -> None:
        """Shut the child down for good: the janitor's idle sweep, or the server stopping.

        The session cursor is already stored, so a conversation whose child was stopped
        picks up again by resuming, with nothing said about it.
        """


class BackendChildFactory(Protocol):
    """Makes the child for one conversation, on one backend.

    The core holds one factory per backend key and calls the one the conversation was
    started on. Making the child does not spawn it — ``start`` does — but a factory that
    already knows the backend cannot run says so with ``BackendSpawnFailed`` here.

    A child is handed two of the record's own things and nothing else: where to report
    what its backend did, and where the files its messages carry are kept. Both are
    services rather than rules — the adapter reads bytes and reports facts, and decides
    none of the conversation's semantics with either.
    """

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> BackendChild: ...
