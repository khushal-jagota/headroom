"""An in-memory conversation system: the reference implementation of the contract.

It exists so that work which consumes the conversation contract can be built and tested
before the real conversation system lands. It is deterministic — no clocks, no
randomness, no threads, no I/O, and it never sleeps — so a test that drives it gets the
same answer every time.

Its recording surface (``InMemoryConversationObservation``) is this fake's own test
observation surface. It is not the deferred event record shape, and it must not be
treated as one.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from planner.conversation.contracts import (
    AddressedPromptDeliveryReceipt,
    ConversationAlreadyStarted,
    ConversationStartRequest,
    ConversationTurnReference,
    HeldPrompt,
    HeldPromptPromotionFate,
    HeldPromptPromotionMode,
    PromptDeliveryFate,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
    PromptQueueReason,
    ResolvedConversationStart,
    backend_supports_steer,
)
from planner.conversation.events import UserInputAnswer, UserInputQuestion
from planner.conversation.logic.addressed_reply import with_authenticated_reply_directive
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.logic.held_line import (
    leading_run_that_can_share_a_turn,
    one_prompt_from,
)
from planner.conversation.message_content import (
    MessageContent,
    MessageText,
    message_content_starts_with_slash_token,
    message_content_text,
    require_message_content,
    sender_labeled_composed_message_content,
)
from planner.core.contracts import Principal


class InMemoryConversationObservationKind(StrEnum):
    """What kind of thing this fake observed happening."""

    prompt_delivered = "prompt_delivered"
    prompt_delivery_refused = "prompt_delivery_refused"
    prompt_delivery_uncertain = "prompt_delivery_uncertain"
    proposal_delivery_failed = "proposal_delivery_failed"
    prompt_discarded = "prompt_discarded"
    turn_ended = "turn_ended"
    permission_asked = "permission_asked"
    permission_answered = "permission_answered"
    user_input_requested = "user_input_requested"
    user_input_answered = "user_input_answered"
    model_changed = "model_changed"
    message_to_owner = "message_to_owner"
    explicit_reply_missing = "explicit_reply_missing"


class InMemoryConversationTurnEnding(StrEnum):
    """How a turn stopped running."""

    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


@dataclass(frozen=True, slots=True)
class InMemoryConversationObservation:
    """One thing this fake observed. The fake's own surface, not an event record.

    ``sender_message_id`` and ``sent_at_unix_milliseconds`` are the sender's own two facts
    about the message, kept exactly as they arrived. The real system writes them onto the
    message's row; there are no rows here, so this is where a caller that wants to know
    whether they survived the trip finds out. Both are absent for a sender that minted
    neither, which is every sender that is not a browser.
    """

    kind: InMemoryConversationObservationKind
    content: MessageContent | None = None
    sender_label: str | None = None
    mode: PromptDeliveryMode | None = None
    turn_ending: InMemoryConversationTurnEnding | None = None
    refusal_reason: PromptDeliveryRefusalReason | None = None
    attempt_count: int | None = None
    last_error: str | None = None
    permission_ask_id: str | None = None
    user_input_request_id: str | None = None
    user_input_questions: tuple[UserInputQuestion, ...] | None = None
    user_input_answers: tuple[UserInputAnswer, ...] | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    sender_message_id: str | None = None
    sent_at_unix_milliseconds: int | None = None
    sender: Principal | None = None
    recipient: Principal | None = None

    @property
    def text(self) -> str | None:
        """The words of the message this observation is about, when it is about one.

        The message itself is ``content``. This is here because most questions asked of
        this fake are about what was said, and a message that is only words should be as
        easy to ask about as it was before a message could be more than words.
        """
        return None if self.content is None else message_content_text(self.content)


@dataclass(frozen=True, slots=True)
class InMemoryBackendPromptWrite:
    """One prompt write that actually reached the backend stand-in's wire."""

    content: MessageContent
    sender_label: str
    mode: PromptDeliveryMode

    @property
    def text(self) -> str:
        """The words that were written. The whole message is ``content``."""
        return message_content_text(self.content)


class TurnCannotEndWhilePermissionAskIsPending(Exception):
    """The backend stand-in was told a turn finished while an ask was still pending.

    This is the fake modelling an agent that is blocked waiting for an answer, not a
    policy the contract imposes. An agent with an unanswered permission ask has not
    finished its turn.
    """


class TurnCannotEndWhileUserInputIsPending(Exception):
    """The backend stand-in is still waiting for the owner to answer questions."""


@dataclass
class _InMemoryBackendSession:
    """Stands in for a live backend child process under a bound session.

    It is a separate object from the fake's observations on purpose: a test that asks
    "did this text actually reach the backend?" — or "was the child actually cancelled?"
    — must be answered by something other than the conversation system's own report of
    what it did.
    """

    prompt_writes: list[InMemoryBackendPromptWrite] = field(default_factory=list)
    pending_permission_answers: dict[str, str | None] = field(default_factory=dict)
    pending_user_input_answers: dict[str, tuple[UserInputAnswer, ...] | None] = field(
        default_factory=dict
    )
    cancellations: int = 0
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass
class _RunningTurn:
    turn_number: int
    prompt_senders: dict[Principal, None] = field(default_factory=dict)
    explicit_reply_recipients: set[Principal] = field(default_factory=set)
    pending_permission_ask_ids: set[str] = field(default_factory=set)
    pending_user_input_questions: dict[str, tuple[UserInputQuestion, ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class _HeldPrompt:
    held_prompt_id: str
    content: MessageContent
    sender_label: str
    model_change: str | None = None
    reasoning_effort_change: str | None = None
    sender_message_id: str | None = None
    sent_at_unix_milliseconds: int | None = None
    sender: Principal | None = None
    recipient: Principal | None = None
    reply_requested: bool = True
    snapshot_sent_at_unix_milliseconds: int = 0
    queue_reason: PromptQueueReason = PromptQueueReason.requested


@dataclass
class _ConversationState:
    resolved_start: ResolvedConversationStart
    current_model: str | None = None
    current_reasoning_effort: str | None = None
    backend_session: _InMemoryBackendSession | None = None
    running_turn: _RunningTurn | None = None
    held_prompts: deque[_HeldPrompt] = field(default_factory=deque)
    held_prompts_created: int = 0
    observations: list[InMemoryConversationObservation] = field(default_factory=list)
    permission_asks_raised: int = 0
    user_input_requests_raised: int = 0
    next_turn_number: int = 1
    armed_backend_start_failure: bool = False
    armed_session_load_failure: bool = False
    armed_backend_write_failure: bool = False


class InMemoryConversationSystem:
    """The contract, implemented against dictionaries instead of child processes."""

    def __init__(self) -> None:
        self._conversations: dict[str, _ConversationState] = {}

    # --- the contract ---

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        resolved = resolve_conversation_start_request(request)
        if resolved.conversation_id in self._conversations:
            raise ConversationAlreadyStarted(resolved.conversation_id)
        self._conversations[resolved.conversation_id] = _ConversationState(
            resolved_start=resolved,
            current_model=resolved.model,
            current_reasoning_effort=resolved.reasoning_effort,
        )

    async def send(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
    ) -> PromptDeliveryFate:
        receipt = await self.send_with_receipt(
            conversation_id,
            content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
        )
        return receipt.fate

    async def send_with_receipt(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
    ) -> AddressedPromptDeliveryReceipt:
        require_message_content(content)
        if message_content_starts_with_slash_token(content):
            reply_requested = False

        state = self._conversations.get(conversation_id)
        if state is None:
            return AddressedPromptDeliveryReceipt(
                fate=PromptDeliveryRefused(
                    refusal_reason=PromptDeliveryRefusalReason.no_such_conversation
                ),
                newly_accepted=False,
            )

        if sender_message_id is not None:
            for position, held in enumerate(state.held_prompts, start=1):
                if held.sender_message_id != sender_message_id:
                    continue
                if (
                    held.content,
                    held.sender_label,
                    held.sender,
                    held.recipient,
                ) != (content, sender_label, sender, recipient):
                    raise ValueError("sender_message_id already names a different message")
                return AddressedPromptDeliveryReceipt(
                    PromptDeliveryQueued(queue_position=position), newly_accepted=False
                )
            for observed in reversed(state.observations):
                if observed.sender_message_id != sender_message_id:
                    continue
                if (
                    observed.content,
                    observed.sender_label,
                    observed.sender,
                    observed.recipient,
                ) != (content, sender_label, sender, recipient):
                    raise ValueError("sender_message_id already names a different message")
                if observed.kind is InMemoryConversationObservationKind.prompt_delivered:
                    if observed.mode is PromptDeliveryMode.steer:
                        fate: PromptDeliveryFate = PromptDeliveryInjected()
                    else:
                        fate = PromptDeliveryStarted()
                    return AddressedPromptDeliveryReceipt(fate, newly_accepted=False)
                if observed.kind is InMemoryConversationObservationKind.prompt_delivery_refused:
                    assert observed.refusal_reason is not None
                    return AddressedPromptDeliveryReceipt(
                        PromptDeliveryRefused(refusal_reason=observed.refusal_reason),
                        newly_accepted=False,
                    )
                if observed.kind is InMemoryConversationObservationKind.prompt_delivery_uncertain:
                    return AddressedPromptDeliveryReceipt(
                        PromptDeliveryUncertain(), newly_accepted=False
                    )
                raise ValueError("sender_message_id already names a different message")

        if state.running_turn is None:
            fate = self._start_turn(
                state,
                content,
                sender_label,
                PromptDeliveryMode.queue if mode is PromptDeliveryMode.steer else mode,
                model_change,
                reasoning_effort_change,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
            )
        elif mode is PromptDeliveryMode.queue:
            fate = self._hold_prompt(
                state,
                content,
                sender_label,
                model_change,
                reasoning_effort_change,
                sender_message_id,
                sent_at_unix_milliseconds,
                PromptQueueReason.requested,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
            )
        elif mode is PromptDeliveryMode.steer:
            if any(not isinstance(piece, MessageText) for piece in content):
                queue_reason: PromptQueueReason | None = PromptQueueReason.attachment
            elif model_change is not None or reasoning_effort_change is not None:
                queue_reason = PromptQueueReason.run_change
            else:
                steer_fate = self._steer(
                    state,
                    content,
                    sender_label,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=reply_requested,
                )
                if isinstance(steer_fate, PromptDeliveryInjected):
                    fate = steer_fate
                    queue_reason = None
                else:
                    queue_reason = PromptQueueReason.steer_refused
            if queue_reason is not None:
                fate = self._hold_prompt(
                    state,
                    content,
                    sender_label,
                    model_change,
                    reasoning_effort_change,
                    sender_message_id,
                    sent_at_unix_milliseconds,
                    queue_reason,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=reply_requested,
                )
        else:
            # send-now against a busy agent: the incumbent dies first, and this message runs
            # next — ahead of everything already held, which keeps its order behind it.
            self._end_running_turn(state, InMemoryConversationTurnEnding.interrupted)
            fate = self._start_turn(
                state,
                content,
                sender_label,
                mode,
                model_change,
                reasoning_effort_change,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
            )
            if isinstance(fate, PromptDeliveryRefused):
                # The incumbent is already dead and the agent is free, so the held prompts
                # are owed their run even though this delivery could not happen.
                self._drain(state)
        return AddressedPromptDeliveryReceipt(
            fate=fate,
            newly_accepted=not isinstance(fate, PromptDeliveryRefused),
        )

    def _hold_prompt(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        queue_reason: PromptQueueReason,
        *,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
    ) -> PromptDeliveryQueued:
        state.held_prompts_created += 1
        state.held_prompts.append(
            _HeldPrompt(
                held_prompt_id=f"held-{state.held_prompts_created}",
                content=content,
                sender_label=sender_label,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                snapshot_sent_at_unix_milliseconds=(
                    sent_at_unix_milliseconds
                    if sent_at_unix_milliseconds is not None
                    else state.held_prompts_created
                ),
                queue_reason=queue_reason,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
            )
        )
        return PromptDeliveryQueued(queue_position=len(state.held_prompts))

    async def record_message_to_owner(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        sender: Principal,
        recipient: Principal,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
    ) -> None:
        require_message_content(content)
        state = self._conversations.get(conversation_id)
        if state is None:
            raise ValueError("no such conversation")
        if sender_message_id is not None:
            for observed in state.observations:
                if observed.sender_message_id != sender_message_id:
                    continue
                if (
                    observed.kind is not InMemoryConversationObservationKind.message_to_owner
                    or observed.content != content
                    or observed.sender != sender
                    or observed.recipient != recipient
                ):
                    raise ValueError("sender_message_id already names a different message")
                return
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.message_to_owner,
                content=content,
                sender_label=sender_label,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
            )
        )
        if state.running_turn is not None:
            state.running_turn.explicit_reply_recipients.add(recipient)

    async def record_prompt_delivery_uncertain(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode,
        sender_message_id: str,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
    ) -> None:
        require_message_content(content)
        state = self._conversations.get(conversation_id)
        if state is None:
            raise ValueError("no such conversation")
        for observed in state.observations:
            if observed.sender_message_id != sender_message_id:
                continue
            if (
                observed.kind is not InMemoryConversationObservationKind.prompt_delivery_uncertain
                or observed.content != content
                or observed.sender_label != sender_label
                or observed.mode is not mode
                or observed.sender != sender
                or observed.recipient != recipient
            ):
                raise ValueError("sender_message_id already names a different message")
            return
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.prompt_delivery_uncertain,
                content=content,
                sender_label=sender_label,
                mode=mode,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
            )
        )

    async def record_proposal_delivery_failed(
        self,
        conversation_id: str,
        *,
        attempt_count: int,
        last_error: str,
        sender_message_id: str,
    ) -> None:
        if attempt_count < 1 or not last_error.strip() or not sender_message_id.strip():
            raise ValueError("proposal delivery failure fields must be non-empty")
        state = self._conversations.get(conversation_id)
        if state is None:
            raise ValueError("no such conversation")
        for observed in state.observations:
            if observed.sender_message_id != sender_message_id:
                continue
            if (
                observed.kind is not InMemoryConversationObservationKind.proposal_delivery_failed
                or observed.attempt_count != attempt_count
                or observed.last_error != last_error
            ):
                raise ValueError("sender_message_id already names a different message")
            return
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.proposal_delivery_failed,
                attempt_count=attempt_count,
                last_error=last_error,
                sender_message_id=sender_message_id,
            )
        )

    async def active_turn_reference(self, conversation_id: str) -> ConversationTurnReference | None:
        state = self._conversations.get(conversation_id)
        if state is None or state.running_turn is None:
            return None
        return ConversationTurnReference(conversation_id, state.running_turn.turn_number)

    async def turn_expects_reply(
        self, turn: ConversationTurnReference, recipient: Principal
    ) -> bool:
        state = self._conversations.get(turn.conversation_id)
        return bool(
            state is not None
            and state.running_turn is not None
            and state.running_turn.turn_number == turn.turn_number
            and recipient in state.running_turn.prompt_senders
            and recipient not in state.running_turn.explicit_reply_recipients
        )

    async def record_explicit_reply(
        self, turn: ConversationTurnReference, recipient: Principal
    ) -> None:
        state = self._conversations.get(turn.conversation_id)
        if (
            state is not None
            and state.running_turn is not None
            and state.running_turn.turn_number == turn.turn_number
        ):
            state.running_turn.explicit_reply_recipients.add(recipient)

    async def interrupt(self, conversation_id: str) -> None:
        state = self._conversations.get(conversation_id)
        if state is None or state.running_turn is None:
            return
        self._end_running_turn(state, InMemoryConversationTurnEnding.interrupted)
        self._drain(state)

    async def held_prompts(self, conversation_id: str) -> tuple[HeldPrompt, ...]:
        state = self._conversations.get(conversation_id)
        if state is None:
            return ()
        return tuple(
            HeldPrompt(
                held_prompt_id=held.held_prompt_id,
                content=held.content,
                sender_label=held.sender_label,
                sender_message_id=held.sender_message_id,
                sent_at_unix_milliseconds=held.snapshot_sent_at_unix_milliseconds,
                sender=held.sender,
                recipient=held.recipient,
                queue_reason=held.queue_reason,
            )
            for held in state.held_prompts
        )

    async def promote_held_prompt(
        self,
        conversation_id: str,
        held_prompt_id: str,
        mode: HeldPromptPromotionMode,
    ) -> HeldPromptPromotionFate | None:
        state = self._conversations.get(conversation_id)
        if state is None:
            return None
        position = next(
            (
                index
                for index, candidate in enumerate(state.held_prompts)
                if candidate.held_prompt_id == held_prompt_id
            ),
            None,
        )
        if position is None:
            return None
        held = state.held_prompts[position]
        del state.held_prompts[position]

        fate: HeldPromptPromotionFate
        if mode is HeldPromptPromotionMode.send_now:
            if state.running_turn is not None:
                self._end_running_turn(state, InMemoryConversationTurnEnding.interrupted)
            fate = self._start_turn(
                state,
                held.content,
                held.sender_label,
                PromptDeliveryMode.send_now,
                held.model_change,
                held.reasoning_effort_change,
                sender_message_id=held.sender_message_id,
                sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                sender=held.sender,
                recipient=held.recipient,
            )
        else:
            fate = self._steer(
                state,
                held.content,
                held.sender_label,
                sender_message_id=held.sender_message_id,
                sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                sender=held.sender,
                recipient=held.recipient,
            )

        if isinstance(fate, PromptDeliveryRefused):
            state.observations.append(
                InMemoryConversationObservation(
                    kind=InMemoryConversationObservationKind.prompt_delivery_refused,
                    content=held.content,
                    sender_label=held.sender_label,
                    mode=(
                        PromptDeliveryMode.send_now
                        if mode is HeldPromptPromotionMode.send_now
                        else PromptDeliveryMode.steer
                    ),
                    refusal_reason=fate.refusal_reason,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                    sender=held.sender,
                    recipient=held.recipient,
                )
            )
            self._drain(state)
        return fate

    async def discard_held_prompt(self, conversation_id: str, held_prompt_id: str) -> bool:
        state = self._conversations.get(conversation_id)
        if state is None:
            return False
        for position, held in enumerate(state.held_prompts):
            if held.held_prompt_id != held_prompt_id:
                continue
            del state.held_prompts[position]
            state.observations.append(
                InMemoryConversationObservation(
                    kind=InMemoryConversationObservationKind.prompt_discarded,
                    content=held.content,
                    sender_label=held.sender_label,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                    sender=held.sender,
                    recipient=held.recipient,
                )
            )
            return True
        return False

    async def kill(self, conversation_id: str) -> None:
        state = self._conversations.get(conversation_id)
        if state is None:
            return
        # The held messages are discarded before the turn ends, so nothing can dequeue
        # them in between — kill silences the queue and the turn as one act.
        while state.held_prompts:
            held = state.held_prompts.popleft()
            state.observations.append(
                InMemoryConversationObservation(
                    kind=InMemoryConversationObservationKind.prompt_discarded,
                    content=held.content,
                    sender_label=held.sender_label,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                    sender=held.sender,
                    recipient=held.recipient,
                )
            )
        if state.running_turn is not None:
            self._end_running_turn(state, InMemoryConversationTurnEnding.interrupted)

    async def is_running(self, conversation_id: str) -> bool:
        state = self._conversations.get(conversation_id)
        return state is not None and state.running_turn is not None

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        state = self._conversations.get(conversation_id)
        if state is None or state.running_turn is None:
            return False
        return bool(state.running_turn.pending_permission_ask_ids)

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        state = self._conversations.get(conversation_id)
        if state is None or state.running_turn is None:
            return False
        return bool(state.running_turn.pending_user_input_questions)

    # --- driving the backend stand-in ---

    def complete_running_turn(self, conversation_id: str) -> None:
        """The backend finished the turn. Nothing running means nothing to end."""
        state = self._conversations[conversation_id]
        running_turn = state.running_turn
        if running_turn is None:
            return
        if running_turn.pending_permission_ask_ids:
            raise TurnCannotEndWhilePermissionAskIsPending(conversation_id)
        if running_turn.pending_user_input_questions:
            raise TurnCannotEndWhileUserInputIsPending(conversation_id)
        self._end_running_turn(state, InMemoryConversationTurnEnding.completed)
        self._drain(state)

    def fail_running_turn(self, conversation_id: str) -> None:
        """The backend errored out of the turn. Nothing running means nothing to end."""
        state = self._conversations[conversation_id]
        if state.running_turn is None:
            return
        self._end_running_turn(state, InMemoryConversationTurnEnding.failed)
        self._drain(state)

    def raise_permission_ask(self, conversation_id: str) -> str:
        """The agent asked for permission. The ask waits; nothing ever answers it here."""
        state = self._conversations[conversation_id]
        running_turn = state.running_turn
        session = state.backend_session
        if running_turn is None or session is None:
            raise RuntimeError(f"conversation {conversation_id} has no running turn")
        state.permission_asks_raised += 1
        ask_id = f"ask-{state.permission_asks_raised}"
        running_turn.pending_permission_ask_ids.add(ask_id)
        session.pending_permission_answers[ask_id] = None
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.permission_asked,
                permission_ask_id=ask_id,
            )
        )
        return ask_id

    def answer_permission_ask(self, conversation_id: str, ask_id: str, option_id: str) -> bool:
        """Answer an ask, as the browser would. Only a still-pending ask of the live turn
        takes the answer; anything else changes nothing and reports that it did not land."""
        state = self._conversations.get(conversation_id)
        if state is None:
            return False
        running_turn = state.running_turn
        session = state.backend_session
        if running_turn is None or session is None:
            return False
        if ask_id not in running_turn.pending_permission_ask_ids:
            return False
        running_turn.pending_permission_ask_ids.remove(ask_id)
        session.pending_permission_answers[ask_id] = option_id
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.permission_answered,
                permission_ask_id=ask_id,
            )
        )
        return True

    def raise_user_input(
        self, conversation_id: str, questions: tuple[UserInputQuestion, ...]
    ) -> str:
        """The agent asked an ordered group of questions and waits for all answers."""
        state = self._conversations[conversation_id]
        running_turn = state.running_turn
        session = state.backend_session
        if running_turn is None or session is None:
            raise RuntimeError(f"conversation {conversation_id} has no running turn")
        state.user_input_requests_raised += 1
        request_id = f"user-input-{state.user_input_requests_raised}"
        running_turn.pending_user_input_questions[request_id] = questions
        session.pending_user_input_answers[request_id] = None
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.user_input_requested,
                user_input_request_id=request_id,
                user_input_questions=questions,
            )
        )
        return request_id

    def answer_user_input(
        self,
        conversation_id: str,
        request_id: str,
        answers: tuple[UserInputAnswer, ...],
    ) -> bool:
        state = self._conversations.get(conversation_id)
        if state is None or state.running_turn is None or state.backend_session is None:
            return False
        questions = state.running_turn.pending_user_input_questions.get(request_id)
        answers_by_question_id = {answer.question_id: answer for answer in answers}
        if (
            questions is None
            or len(answers_by_question_id) != len(answers)
            or set(answers_by_question_id) != {question.question_id for question in questions}
        ):
            return False
        ordered_answers = tuple(
            answers_by_question_id[question.question_id] for question in questions
        )
        if any(
            not answer.answers
            or any(not value for value in answer.answers)
            or (not question.multi_select and len(answer.answers) != 1)
            or (
                not question.allow_other
                and any(
                    value not in {option.label for option in question.options}
                    for value in answer.answers
                )
            )
            for question, answer in zip(questions, ordered_answers, strict=True)
        ):
            return False
        del state.running_turn.pending_user_input_questions[request_id]
        state.backend_session.pending_user_input_answers[request_id] = ordered_answers
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.user_input_answered,
                user_input_request_id=request_id,
                user_input_answers=ordered_answers,
            )
        )
        return True

    def arm_backend_start_failure(self, conversation_id: str) -> None:
        """The backend process will not spawn when this conversation next needs it."""
        self._conversations[conversation_id].armed_backend_start_failure = True

    def arm_session_load_failure(self, conversation_id: str) -> None:
        """The session will not load when this conversation next needs it."""
        self._conversations[conversation_id].armed_session_load_failure = True

    def arm_backend_write_failure(self, conversation_id: str) -> None:
        """Writes to this conversation's backend wire will not succeed."""
        self._conversations[conversation_id].armed_backend_write_failure = True

    # --- reads ---

    def backend_prompt_writes(self, conversation_id: str) -> tuple[InMemoryBackendPromptWrite, ...]:
        state = self._conversations.get(conversation_id)
        if state is None or state.backend_session is None:
            return ()
        return tuple(state.backend_session.prompt_writes)

    def backend_permission_answer(self, conversation_id: str, ask_id: str) -> str | None:
        state = self._conversations.get(conversation_id)
        if state is None or state.backend_session is None:
            return None
        return state.backend_session.pending_permission_answers.get(ask_id)

    def backend_user_input_answers(
        self, conversation_id: str, request_id: str
    ) -> tuple[UserInputAnswer, ...] | None:
        state = self._conversations.get(conversation_id)
        if state is None or state.backend_session is None:
            return None
        return state.backend_session.pending_user_input_answers.get(request_id)

    def backend_cancellations(self, conversation_id: str) -> int:
        """How many times the backend stand-in was told to cancel its running turn."""
        state = self._conversations.get(conversation_id)
        if state is None or state.backend_session is None:
            return 0
        return state.backend_session.cancellations

    def backend_model(self, conversation_id: str) -> str | None:
        """The model the backend stand-in's session currently runs on — its own account."""
        state = self._conversations.get(conversation_id)
        if state is None or state.backend_session is None:
            return None
        return state.backend_session.model

    def backend_reasoning_effort(self, conversation_id: str) -> str | None:
        """The reasoning effort the backend stand-in's session currently runs on."""
        state = self._conversations.get(conversation_id)
        if state is None or state.backend_session is None:
            return None
        return state.backend_session.reasoning_effort

    def observations(self, conversation_id: str) -> tuple[InMemoryConversationObservation, ...]:
        state = self._conversations.get(conversation_id)
        if state is None:
            return ()
        return tuple(state.observations)

    # --- internals ---

    def _establish_backend_session(
        self, state: _ConversationState
    ) -> _InMemoryBackendSession | PromptDeliveryRefusalReason:
        session = state.backend_session
        if session is not None:
            return session
        if state.armed_backend_start_failure:
            return PromptDeliveryRefusalReason.backend_did_not_start
        if state.armed_session_load_failure:
            return PromptDeliveryRefusalReason.session_did_not_load
        session = _InMemoryBackendSession(
            model=state.current_model, reasoning_effort=state.current_reasoning_effort
        )
        state.backend_session = session
        return session

    def _write_to_backend(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        *,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
        recorded_messages: Sequence[_HeldPrompt] = (),
    ) -> _InMemoryBackendSession | PromptDeliveryRefused:
        established = self._establish_backend_session(state)
        if isinstance(established, PromptDeliveryRefusalReason):
            return PromptDeliveryRefused(refusal_reason=established)
        if state.armed_backend_write_failure:
            return PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
            )
        if model_change is not None or reasoning_effort_change is not None:
            # The change lands with the delivery, so it is applied only once the write
            # is known to go through — a refused delivery must change nothing.
            if model_change is not None:
                state.current_model = model_change
                established.model = model_change
            if reasoning_effort_change is not None:
                state.current_reasoning_effort = reasoning_effort_change
                established.reasoning_effort = reasoning_effort_change
            state.observations.append(
                InMemoryConversationObservation(
                    kind=InMemoryConversationObservationKind.model_changed,
                    model=state.current_model,
                    reasoning_effort=state.current_reasoning_effort,
                )
            )
        reply_senders = (
            tuple(
                dict.fromkeys(
                    message.sender
                    for message in recorded_messages
                    if message.reply_requested
                    and message.sender is not None
                    and message.recipient is not None
                )
            )
            if recorded_messages
            else (
                (sender,)
                if reply_requested and sender is not None and recipient is not None
                else ()
            )
        )
        wire_content = with_authenticated_reply_directive(content, reply_senders)
        established.prompt_writes.append(
            InMemoryBackendPromptWrite(
                content=sender_labeled_composed_message_content(
                    wire_content, content, sender_label
                ),
                sender_label=sender_label,
                mode=mode,
            )
        )
        if recorded_messages:
            # One prompt to the agent is still one record per message, each with its own
            # words, so every sender recognises its own message when the record hands it
            # back.
            for message in recorded_messages:
                state.observations.append(
                    InMemoryConversationObservation(
                        kind=InMemoryConversationObservationKind.prompt_delivered,
                        content=message.content,
                        sender_label=message.sender_label,
                        mode=mode,
                        sender_message_id=message.sender_message_id,
                        sent_at_unix_milliseconds=message.sent_at_unix_milliseconds,
                        sender=message.sender,
                        recipient=message.recipient,
                    )
                )
            return established
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.prompt_delivered,
                content=content,
                sender_label=sender_label,
                mode=mode,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
            )
        )
        return established

    def _start_turn(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        *,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
        recorded_messages: Sequence[_HeldPrompt] = (),
    ) -> PromptDeliveryStarted | PromptDeliveryRefused:
        written = self._write_to_backend(
            state,
            content,
            sender_label,
            mode,
            model_change,
            reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            recorded_messages=recorded_messages,
        )
        if isinstance(written, PromptDeliveryRefused):
            return written
        prompt_senders: dict[Principal, None] = {}
        messages = recorded_messages or (
            _HeldPrompt(
                held_prompt_id="",
                content=content,
                sender_label=sender_label,
                sender=sender,
                recipient=recipient,
                reply_requested=reply_requested,
            ),
        )
        for message in messages:
            if message.reply_requested and message.sender is not None:
                prompt_senders.setdefault(message.sender, None)
        state.running_turn = _RunningTurn(
            turn_number=state.next_turn_number,
            prompt_senders=prompt_senders,
        )
        state.next_turn_number += 1
        return PromptDeliveryStarted()

    def _steer(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        *,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
    ) -> PromptDeliveryInjected | PromptDeliveryRefused:
        if not backend_supports_steer(state.resolved_start.backend_key):
            return PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.backend_cannot_steer
            )
        if state.running_turn is None:
            return PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.no_running_turn_to_steer_into
            )
        written = self._write_to_backend(
            state,
            content,
            sender_label,
            PromptDeliveryMode.steer,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
        )
        if isinstance(written, PromptDeliveryRefused):
            return written
        if reply_requested and sender is not None and state.running_turn is not None:
            state.running_turn.prompt_senders.setdefault(sender, None)
        return PromptDeliveryInjected()

    def _end_running_turn(
        self, state: _ConversationState, ending: InMemoryConversationTurnEnding
    ) -> None:
        running = state.running_turn
        assert running is not None
        state.running_turn = None
        session = state.backend_session
        if ending is InMemoryConversationTurnEnding.interrupted and session is not None:
            # Only an interruption cancels the child. A turn that completed or failed
            # ended on the backend's own account, with nothing left to cancel.
            session.cancellations += 1
        for prompt_sender in running.prompt_senders:
            if prompt_sender in running.explicit_reply_recipients:
                continue
            state.observations.append(
                InMemoryConversationObservation(
                    kind=InMemoryConversationObservationKind.explicit_reply_missing,
                    sender=prompt_sender,
                )
            )
        state.observations.append(
            InMemoryConversationObservation(
                kind=InMemoryConversationObservationKind.turn_ended,
                turn_ending=ending,
            )
        )

    def _drain(self, state: _ConversationState) -> None:
        """Run held prompts until one of them starts a turn or there are none left.

        A held prompt that cannot be delivered has its own fate recorded and is then
        dropped: the caller that sent it is gone, so there is nobody to hold it for and
        nobody to tell.
        """
        while state.running_turn is None and state.held_prompts:
            run = leading_run_that_can_share_a_turn(state.held_prompts)
            for _ in run:
                state.held_prompts.popleft()
            held = run[0]
            fate = self._start_turn(
                state,
                one_prompt_from(run),
                held.sender_label,
                PromptDeliveryMode.queue,
                held.model_change,
                held.reasoning_effort_change,
                sender_message_id=held.sender_message_id,
                sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                sender=held.sender,
                recipient=held.recipient,
                recorded_messages=run,
            )
            if isinstance(fate, PromptDeliveryRefused):
                for message in run:
                    state.observations.append(
                        InMemoryConversationObservation(
                            kind=InMemoryConversationObservationKind.prompt_delivery_refused,
                            content=message.content,
                            sender_label=message.sender_label,
                            mode=PromptDeliveryMode.queue,
                            refusal_reason=fate.refusal_reason,
                            sender_message_id=message.sender_message_id,
                            sent_at_unix_milliseconds=message.sent_at_unix_milliseconds,
                            sender=message.sender,
                            recipient=message.recipient,
                        )
                    )
