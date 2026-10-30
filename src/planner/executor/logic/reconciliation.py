"""Exact callback liveness."""

from planner.executor.contracts import (
    AnswerAdmission,
    AskKind,
    AskReachability,
    AskReference,
    ChildInventory,
)


def answer_target_is_open(admission: AnswerAdmission) -> bool:
    context = admission.context
    if context is None or not admission.assignment_open:
        return False
    if (
        context.child != admission.ask.child
        or context.turn != admission.ask.turn
        or context.ended
        or context.source_retired
        or context.move_retirement_uncertain
        or context.delivery_uncertain
        or context.callbacks_revoked
        or admission.reachability not in (AskReachability.reachable, AskReachability.uncertain)
    ):
        return False
    pending = (
        context.pending_user_input_ids
        if admission.ask.kind is AskKind.user_input
        else context.pending_permission_ask_ids
    )
    return admission.ask.ask_id in pending


def preserved_reachability(previous: AskReachability, observed: AskReachability) -> AskReachability:
    if previous in (AskReachability.child_gone, AskReachability.callback_gone):
        return previous
    return observed


def callback_reachability(
    ask: AskReference, children: tuple[ChildInventory, ...]
) -> AskReachability:
    child = next((entry for entry in children if entry.child == ask.child), None)
    if child is None:
        return AskReachability.child_gone
    callback_exists = (
        ask.ask_id in child.pending_user_input_ids
        if ask.kind is AskKind.user_input
        else ask in child.pending_asks
    )
    if child.active_turn == ask.turn and callback_exists:
        return AskReachability.reachable
    return AskReachability.callback_gone
