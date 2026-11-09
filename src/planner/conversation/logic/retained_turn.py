"""What a VPS restart makes of an open Mac run, whose agent the restart did not stop.

The run's turn is taken over rather than interrupted. The durable facts are the only
account of that turn: the running turn is rebuilt from them, and whatever only the lost
process could have settled is marked as not known, never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from planner.conversation.backend_event_contracts import RunningTurn
from planner.conversation.events import UserInputQuestion
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.execution_identity import TurnToken

LIVE_FACT_STATUSES = frozenset(
    (
        LifecycleStatus.intent,
        LifecycleStatus.running,
        LifecycleStatus.pending,
        LifecycleStatus.answering,
    )
)


@dataclass(frozen=True, slots=True)
class OpenRunTurn:
    """An open Mac run's last turn in the mailbox (``mac_runs.turn_number``).

    ``executor_process_current``: the run belongs to the current executor process, so its
    agent and its open asks are alive. A replaced process's agent is gone; its turn is
    kept only so its drain can end it, and its asks can no longer be answered.
    """

    turn_number: int
    executor_process_current: bool


def facts_after_restart(
    facts: tuple[LifecycleFact, ...], open_run: OpenRunTurn | None
) -> tuple[LifecycleFact, ...]:
    """The facts a VPS restart writes in place of a conversation's live ones.

    Without an open run, every live fact is what only the lost process knew: an attempt
    becomes unknown, and anything else unavailable. With one, its turn is kept when the
    Mac is still on it (see ``kept_turn_number``):

    - The turn stays as it is. Its prompt is in the mailbox, which is what a delivered
      Mac prompt means, so a prompt the lost process had not yet recorded is started,
      while the process that holds the mailbox row is current. A replaced process's row
      was withdrawn or left uncertain, so its prompt becomes unknown.
    - Its open asks stay open while the agent is alive and the turn is not being
      cancelled.
    - A turn reserved after it never reached the mailbox, because a prompt's mailbox row
      and that number commit together. It never started, so it ends unavailable like any
      reserved turn that never started, and its prompt provably never crossed the wire.
    """
    kept = kept_turn_number(facts, open_run)
    asks_stay_open = (
        open_run is not None
        and open_run.executor_process_current
        and kept is not None
        and not _being_cancelled(facts, kept)
    )
    settled: list[LifecycleFact] = []
    for fact in facts:
        if fact.status not in LIVE_FACT_STATUSES:
            continue
        if open_run is not None and fact.turn_number == kept:
            if fact.kind is LifecycleKind.turn:
                continue
            if fact.kind is LifecycleKind.request and asks_stay_open:
                continue
            if (
                fact.kind is LifecycleKind.attempt
                and fact.operation is LifecycleOperation.prompt
                and open_run.executor_process_current
            ):
                settled.append(replace(fact, status=LifecycleStatus.started))
                continue
        if (
            open_run is not None
            and fact.turn_number > open_run.turn_number
            and fact.kind is LifecycleKind.attempt
            and fact.operation is LifecycleOperation.prompt
        ):
            settled.append(replace(fact, status=LifecycleStatus.suppressed))
            continue
        settled.append(
            replace(
                fact,
                status=(
                    LifecycleStatus.unknown
                    if fact.kind is LifecycleKind.attempt
                    else LifecycleStatus.unavailable
                ),
            )
        )
    return tuple(settled)


def kept_turn_number(facts: tuple[LifecycleFact, ...], open_run: OpenRunTurn | None) -> int | None:
    """The open run's turn, if the Mac is still on it.

    It is running, or it is still at intent with its prompt not settled otherwise. A
    prompt the lost process already recorded as uncertain or refused leaves the turn to
    end as any other.
    """
    if open_run is None:
        return None
    number = open_run.turn_number
    turn = _fact(facts, LifecycleKind.turn, number, LifecycleOperation.turn)
    prompt = _fact(facts, LifecycleKind.attempt, number, LifecycleOperation.prompt)
    if turn is None:
        return None
    if turn.status is LifecycleStatus.running:
        return number
    if (
        turn.status is LifecycleStatus.intent
        and prompt is not None
        and prompt.status in (LifecycleStatus.intent, LifecycleStatus.started)
    ):
        return number
    return None


def adopted_running_turn(
    token: TurnToken,
    facts: tuple[LifecycleFact, ...],
    user_input_questions: Mapping[str, tuple[UserInputQuestion, ...]],
    *,
    executor_process_current: bool,
) -> RunningTurn | None:
    """The running turn the facts still show for ``token``, or None if it has ended.

    An ask that is still pending can be answered again; one already being answered waits
    for its answer to apply. ``user_input_questions`` are the stored questions of each
    user-input request, by its public id. The turn takes no new asks when nobody can
    answer them: its agent belonged to a replaced process, or the core had begun to
    cancel the turn.
    """
    turn = _fact(facts, LifecycleKind.turn, token.turn_number, LifecycleOperation.turn)
    if turn is None or turn.status not in (LifecycleStatus.intent, LifecycleStatus.running):
        return None
    running = RunningTurn(
        token=token,
        callbacks_revoked=not executor_process_current
        or _being_cancelled(facts, token.turn_number),
    )
    if running.callbacks_revoked:
        return running
    for fact in facts:
        if (
            fact.kind is not LifecycleKind.request
            or fact.turn_number != token.turn_number
            or fact.provider_identity is None
            or fact.status not in (LifecycleStatus.pending, LifecycleStatus.answering)
        ):
            continue
        pending = fact.status is LifecycleStatus.pending
        if fact.operation is LifecycleOperation.permission:
            running.permission_provider_ids[fact.identity] = fact.provider_identity
            if pending:
                running.pending_permission_ask_ids.add(fact.identity)
        elif fact.operation is LifecycleOperation.user_input:
            running.user_input_provider_ids[fact.identity] = fact.provider_identity
            questions = user_input_questions.get(fact.identity)
            if pending and questions is not None:
                running.pending_user_input_questions[fact.identity] = questions
    return running


def _fact(
    facts: tuple[LifecycleFact, ...],
    kind: LifecycleKind,
    turn_number: int,
    operation: LifecycleOperation,
) -> LifecycleFact | None:
    """A turn's own turn or prompt fact (one of each per turn)."""
    return next(
        (
            fact
            for fact in facts
            if fact.kind is kind
            and fact.turn_number == turn_number
            and fact.operation is operation
            and (kind is not LifecycleKind.attempt or fact.identity == f"prompt:{turn_number}")
        ),
        None,
    )


def _being_cancelled(facts: tuple[LifecycleFact, ...], turn_number: int) -> bool:
    """The core had begun to end this turn itself."""
    return any(
        fact.kind is LifecycleKind.attempt
        and fact.operation is LifecycleOperation.cancel
        and fact.turn_number == turn_number
        for fact in facts
    )
