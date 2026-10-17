"""Exact callback liveness and conservative cross-host action timing."""

from planner.executor.contracts import (
    AnswerActionTiming,
    AskReachability,
    AskReference,
    AuthorizedAction,
    ChildInventory,
    ClockSample,
    DurableAnswer,
)


def callback_reachability(
    ask: AskReference, children: tuple[ChildInventory, ...]
) -> AskReachability:
    child = next((entry for entry in children if entry.child == ask.child), None)
    if child is None:
        return AskReachability.child_gone
    if child.active_turn == ask.turn and ask in child.pending_asks:
        return AskReachability.reachable
    return AskReachability.callback_gone


def answer_action_timing(
    answer: DurableAnswer, action: AuthorizedAction | None, sample: ClockSample | None
) -> AnswerActionTiming:
    if action is None:
        return AnswerActionTiming(answer.answer_id, None, None, "unknown", "action not observed")
    if action.answer_id != answer.answer_id or action.ask != answer.ask:
        raise ValueError("Action does not belong to this answer")
    if sample is None:
        return AnswerActionTiming(
            answer.answer_id, None, None, "unknown", "clock bounds unavailable"
        )
    if (
        sample.vps_received_at_ms < sample.vps_sent_at_ms
        or sample.mac_sent_at_ms < sample.mac_received_at_ms
    ):
        return AnswerActionTiming(answer.answer_id, None, None, "unknown", "clock exchange invalid")
    # Offset is Mac minus VPS. Nonnegative network delays bound it on each side.
    offset_lower = sample.mac_sent_at_ms - sample.vps_received_at_ms
    offset_upper = sample.mac_received_at_ms - sample.vps_sent_at_ms
    if offset_lower > offset_upper:
        return AnswerActionTiming(answer.answer_id, None, None, "unknown", "clock exchange invalid")
    local_elapsed = action.observed_at_mac_ms - answer.accepted_at_vps_ms
    return AnswerActionTiming(
        answer.answer_id,
        local_elapsed - offset_upper,
        local_elapsed - offset_lower,
        "handshake_round_trip_bounds",
        None,
    )
