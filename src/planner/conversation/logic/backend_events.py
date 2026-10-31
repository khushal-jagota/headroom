"""Common request identities and turn outcomes, without transport execution."""

from dataclasses import replace

from planner.conversation.backend_event_contracts import (
    InterpretedBackendEvent,
    InterpretedRequest,
    InterpretedTurnEnding,
    RunningTurn,
    TurnEndingContext,
)
from planner.conversation.backends.contracts import (
    BackendEventValue,
    BackendPermissionAsk,
    BackendUserInputRequest,
    ContextCompacted,
    PermissionAskRaised,
    PlanUpdated,
    TokenUsageReported,
    ToolCallFinished,
    ToolCallStarted,
    TurnEnded,
    TurnToken,
    UserInputFailed,
    UserInputRequested,
)
from planner.conversation.backends.failure_summary import normalized_failure_summary
from planner.conversation.events import (
    AutomaticCompactionResult,
    ContextCompactedEventPayload,
    ConversationEventPayload,
    ConversationTurnEnding,
    ExplicitReplyMissingEventPayload,
    PermissionAskedEventPayload,
    PlanUpdatedEventPayload,
    TokenUsageEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallStartedEventPayload,
    TurnEndedEventPayload,
    UserInputFailedEventPayload,
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


def interpret_backend_event(
    token: TurnToken | None,
    args: BackendEventValue,
    running: RunningTurn | None,
    known_lifecycle: tuple[LifecycleFact, ...],
) -> InterpretedBackendEvent:
    """Predict rows and turn changes; the caller publishes them only after commit."""
    payloads: tuple[ConversationEventPayload, ...] = ()
    lifecycle: tuple[LifecycleFact, ...] = ()
    compacted = isinstance(args, ContextCompacted)
    compaction_result = None
    failure_notice = False
    predicted = (
        replace(
            running,
            pending_permission_ask_ids=set(running.pending_permission_ask_ids),
            pending_user_input_questions=dict(running.pending_user_input_questions),
            permission_provider_ids=dict(running.permission_provider_ids),
            user_input_provider_ids=dict(running.user_input_provider_ids),
        )
        if running is not None
        and isinstance(
            args,
            (
                ContextCompacted,
                PermissionAskRaised,
                UserInputRequested,
                TurnEnded,
            ),
        )
        else None
    )
    if isinstance(args, ToolCallStarted):
        payloads = (
            ToolCallStartedEventPayload(
                tool_call_id=args.tool_call_id,
                title=args.title,
                tool_kind=args.tool_kind,
                detail=args.detail,
            ),
        )
    elif isinstance(args, ToolCallFinished):
        payloads = (
            ToolCallFinishedEventPayload(
                tool_call_id=args.tool_call_id,
                tool_call_status=args.tool_call_status,
                detail=args.detail,
            ),
        )
    elif isinstance(args, PlanUpdated):
        payloads = (PlanUpdatedEventPayload(entries=args.entries),)
    elif isinstance(args, TokenUsageReported):
        payloads = (
            TokenUsageEventPayload(
                input_tokens=args.input_tokens,
                output_tokens=args.output_tokens,
                cached_input_tokens=args.cached_input_tokens,
                cost_usd=args.cost_usd,
            ),
        )
    elif compacted:
        payloads = (ContextCompactedEventPayload(),)
        if predicted is not None:
            predicted.compaction_confirmed = True
    elif isinstance(args, PermissionAskRaised) and token is not None:
        ask = args.ask
        interpreted_request = interpret_permission_ask(token, ask)
        identity = interpreted_request.lifecycle.identity
        payloads = (interpreted_request.payload,)
        lifecycle = (interpreted_request.lifecycle,)
        if predicted is not None:
            predicted.pending_permission_ask_ids.add(identity)
            predicted.permission_provider_ids[identity] = ask.ask_id
    elif isinstance(args, UserInputRequested) and token is not None:
        request = args.request
        interpreted_request = interpret_user_input_request(token, request)
        identity = interpreted_request.lifecycle.identity
        payloads = (interpreted_request.payload,)
        lifecycle = (interpreted_request.lifecycle,)
        if predicted is not None:
            predicted.pending_user_input_questions[identity] = request.questions
            predicted.user_input_provider_ids[identity] = request.request_id
    elif isinstance(args, UserInputFailed) and token is not None:
        payloads = (
            UserInputFailedEventPayload(
                request_id=f"{token.turn_number}:user_input:{args.request_id}",
                detail=args.detail,
            ),
        )
    elif isinstance(args, TurnEnded) and running is not None:
        interpreted_ending = interpret_turn_ending(
            TurnEndingContext(
                running.token,
                running.automatic_compaction,
                running.compaction_confirmed,
                running.automatic_notice,
                tuple(running.prompt_senders),
                tuple(running.explicit_reply_recipients),
            ),
            args.ending,
            args.error_summary,
            known_lifecycle,
        )
        payloads = interpreted_ending.payloads
        lifecycle = interpreted_ending.lifecycle
        compaction_result = interpreted_ending.compaction_result
        failure_notice = interpreted_ending.failure_notice
        if predicted is not None:
            predicted.ended = True
            predicted.pending_permission_ask_ids.clear()
            predicted.pending_user_input_questions.clear()
            predicted.permission_provider_ids.clear()
            predicted.user_input_provider_ids.clear()
    return InterpretedBackendEvent(
        payloads, lifecycle, predicted, compacted, compaction_result, failure_notice
    )
