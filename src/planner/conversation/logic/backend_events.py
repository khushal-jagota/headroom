"""Common request identities and turn outcomes, without transport execution."""

from dataclasses import replace

from planner.conversation.backend_event_contracts import (
    InterpretedRequest,
    InterpretedTurnEnding,
    TurnEndingContext,
)
from planner.conversation.backends.contracts import (
    BackendPermissionAsk,
    BackendUserInputRequest,
    TurnToken,
)
from planner.conversation.backends.failure_summary import normalized_failure_summary
from planner.conversation.events import (
    AutomaticCompactionResult,
    ConversationTurnEnding,
    ExplicitReplyMissingEventPayload,
    PermissionAskedEventPayload,
    TurnEndedEventPayload,
    UserInputRequestedEventPayload,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)


def interpret_permission_ask(token: TurnToken, ask: BackendPermissionAsk) -> InterpretedRequest:
    identity = f"{token.turn_number}:permission:{ask.ask_id}"
    return InterpretedRequest(
        PermissionAskedEventPayload(
            ask_id=identity, title=ask.title, detail=ask.detail, options=ask.options
        ),
        LifecycleFact(
            LifecycleKind.request,
            identity,
            token.turn_number,
            LifecycleOperation.permission,
            LifecycleStatus.pending,
            ask.ask_id,
        ),
        ask.ask_id,
    )


def interpret_user_input_request(
    token: TurnToken, request: BackendUserInputRequest
) -> InterpretedRequest:
    identity = f"{token.turn_number}:user_input:{request.request_id}"
    return InterpretedRequest(
        UserInputRequestedEventPayload(request_id=identity, questions=request.questions),
        LifecycleFact(
            LifecycleKind.request,
            identity,
            token.turn_number,
            LifecycleOperation.user_input,
            LifecycleStatus.pending,
            request.request_id,
        ),
        request.request_id,
    )


def interpret_turn_ending(
    context: TurnEndingContext,
    ending: ConversationTurnEnding,
    error_summary: str | None,
    lifecycle: tuple[LifecycleFact, ...],
) -> InterpretedTurnEnding:
    compaction = (
        AutomaticCompactionResult.not_compacted
        if context.automatic_compaction
        and ending is ConversationTurnEnding.completed
        and not context.compaction_confirmed
        else None
    )
    payloads = tuple(
        ExplicitReplyMissingEventPayload(prompt_sender=sender)
        for sender in context.prompt_senders
        if sender not in context.explicit_reply_recipients
    ) + (
        TurnEndedEventPayload(
            ending=ending,
            error_summary=normalized_failure_summary(error_summary)
            if ending is ConversationTurnEnding.failed
            else None,
            automatic_compaction_result=compaction,
        ),
    )
    facts = (
        LifecycleFact(
            LifecycleKind.turn,
            str(context.token.turn_number),
            context.token.turn_number,
            LifecycleOperation.turn,
            LifecycleStatus(ending.value),
        ),
        *(
            replace(fact, status=LifecycleStatus.unavailable)
            for fact in lifecycle
            if fact.kind is LifecycleKind.request
            and fact.turn_number == context.token.turn_number
            and fact.status in (LifecycleStatus.pending, LifecycleStatus.answering)
        ),
    )
    return InterpretedTurnEnding(
        payloads,
        facts,
        compaction,
        ending is ConversationTurnEnding.failed and not context.automatic_notice,
    )
