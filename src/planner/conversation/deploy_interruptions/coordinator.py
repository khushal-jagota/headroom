"""Recovery through the ordinary conversation delivery boundary.

A deploy recovers every eligible conversation once, at startup. A Mac process restart
recovers the one conversation whose turn it interrupted, while the server keeps running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from typing import Any

from planner.conversation.contracts import ConversationSystem, PromptDeliveryMode
from planner.conversation.deploy_interruptions.contracts import CaptureManifest
from planner.conversation.deploy_interruptions.data import import_captured_turns
from planner.conversation.deploy_interruptions.logic import recovery_message_identity
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.execution_identity import ChildReference

_LOG = logging.getLogger(__name__)

MAC_RESTART_CLAIM_ERROR_REASON = "interrupted by a Mac restart"


async def run_automatic_recovery(system: ConversationSystem, store: ConversationStore) -> None:
    # Startup only: a recovery prompt still at intent was lost with the previous process.
    await store.record_unknown_recovery_deliveries()
    await recover_conversations(system, store)


async def recover_conversations(
    system: ConversationSystem,
    store: ConversationStore,
    *,
    conversation_ids: tuple[str, ...] | None = None,
    mode: PromptDeliveryMode = PromptDeliveryMode.queue,
) -> None:
    """Send each eligible conversation its one recovery prompt, optionally only some.

    ``send_now`` puts the prompt ahead of messages already waiting in the conversation.
    """
    selected = await store.select_deployment_recoveries()
    if conversation_ids is not None:
        selected = {
            conversation_id: references
            for conversation_id, references in selected.items()
            if conversation_id in conversation_ids
        }

    async def resume(conversation_id: str) -> None:
        references = selected[conversation_id]
        # Do not manufacture activity over work already running on this boot.
        if await system.is_running(conversation_id):
            return
        identity = recovery_message_identity(tuple(ref.interruption_id for ref in references))
        try:
            await system.send(
                conversation_id,
                text_message_content(
                    "Inspect the interrupted work and continue the current authorized step."
                ),
                sender_label="Panels deployment recovery",
                mode=mode,
                sender_message_id=identity,
                record_refusal=True,
                recovery_references=references,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("Automatic recovery failed for conversation %s", conversation_id)

    async with asyncio.TaskGroup() as group:
        for conversation_id in selected:
            group.create_task(resume(conversation_id))


def close_run_of_replaced_mac_process(db_path: str, reference: ChildReference, now_ms: int) -> None:
    """Close the run of a replaced Mac process whose turn records no interruption."""
    from planner.executor.store import ExecutorStore

    ExecutorStore(db_path).close_child(reference, now_ms)


def _answers_the_agent_may_not_have(
    conn: sqlite3.Connection, reference: ChildReference, turn_number: int
) -> str | None:
    """What the owner answered on this turn that the old agent did not take, as text for
    the recovery prompt, or None.

    An answer is the old agent's once its application is recorded. Otherwise its mailbox
    row says what is known: withdrawn or refused means it never reached the agent;
    anything else means it may have, and the text says so rather than guessing. An
    expired answer's question had already closed, so it is not repeated.
    """
    from planner.executor.protocol import (
        AnswerPermission,
        AnswerUserInput,
        CommandDisposition,
        CommandResult,
        MailboxCommand,
        type_adapter,
    )

    def stored(kind: str, key: str, value: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT payload FROM conversation_events WHERE conversation_id=? AND kind=? "
            "AND json_extract(payload,?)=? ORDER BY sequence DESC LIMIT 1",
            (reference.conversation_id, kind, f"$.{key}", value),
        ).fetchone()
        return None if row is None else json.loads(str(row[0]))

    lines: list[str] = []
    for command_id, command_json, state, outcome_json in conn.execute(
        "SELECT command_id,command_json,state,outcome_json FROM executor_commands "
        "WHERE executor_incarnation=? AND child_id=? AND turn_number=? "
        "AND operation IN ('answer_permission_ask','answer_user_input') "
        "ORDER BY command_sequence",
        (reference.executor_incarnation, reference.child_id, turn_number),
    ).fetchall():
        if stored("answer_applied", "answer_id", str(command_id)) is not None:
            continue
        command = type_adapter(MailboxCommand).validate_json(command_json).child_command
        if command is None:
            continue
        outcome = (
            None
            if outcome_json is None
            else type_adapter(CommandResult).validate_json(outcome_json).child_outcome
        )
        if state == "expired":
            continue  # Its question had already closed some other way.
        never_reached = state == "withdrawn" or (
            outcome is not None and outcome.disposition is CommandDisposition.refused
        )
        arguments = command.arguments
        if isinstance(arguments, AnswerPermission):
            ask = stored(
                "permission_asked", "ask_id", f"{turn_number}:permission:{arguments.ask_id}"
            )
            title = arguments.ask_id if ask is None else str(ask["title"])
            answer = next(
                (
                    str(option["label"])
                    for option in (() if ask is None else ask["options"])
                    if option["option_id"] == arguments.option_id
                ),
                arguments.option_id,
            )
            asked = [(title, answer)]
        elif isinstance(arguments, AnswerUserInput):
            request = stored(
                "user_input_requested",
                "request_id",
                f"{turn_number}:user_input:{arguments.request_id}",
            )
            questions = {
                str(question["question_id"]): str(question["question"])
                for question in (() if request is None else request["questions"])
            }
            asked = [
                (questions.get(given.question_id, given.question_id), ", ".join(given.answers))
                for given in arguments.answers
            ]
        else:
            continue
        for question, answer in asked:
            lines.append(
                f'Before the restart you asked: "{question}". The answer was: {answer}. '
                + (
                    "That answer never reached you."
                    if never_reached
                    else "That answer may already have reached you before the restart."
                )
            )
    return " ".join(lines) or None


def close_run_and_record_mac_process_interruption(
    db_path: str,
    reference: ChildReference,
    replaced_by: str,
    now_ms: int,
    *,
    undelivered_message: str | None = None,
) -> None:
    """Close the run of a replaced Mac process and record the turn it left unfinished, as
    a deploy records one, in one transaction.

    Committed together, a VPS crash either leaves the run open, so the drain is rebuilt at
    startup and comes back here, or leaves the recorded interruption, which startup
    recovery resumes. The turn is read after the close in the same transaction, so it is
    no longer taken for one the executor still owns. The owner's answers the old agent
    did not take go into the interruption's notice, so the recovery prompt carries them.

    ``undelivered_message`` is the text of a turn's opening prompt that never reached the
    old agent. It goes into the notice too, so the message survives a crash before it is
    written again to a fresh agent (close_mac_interruption_of_turn then closes this).
    """
    from planner.core.db import connect
    from planner.environments.interruption_capture import read_unresolved_turns
    from planner.executor.store import ExecutorStore

    conversation_id = reference.conversation_id
    now = now_ms // 1000
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ExecutorStore.close_child_in_transaction(conn, reference, now_ms)
        turns = read_unresolved_turns(conn, conversation_ids=(conversation_id,))
        answers = {
            (turn.conversation_id, turn.turn_number): text
            for turn in turns
            if (text := _answers_the_agent_may_not_have(conn, reference, turn.turn_number))
        }
        if undelivered_message is not None:
            for turn in turns:
                key = (turn.conversation_id, turn.turn_number)
                answers[key] = (
                    "The message that opened this turn never reached you: the Mac "
                    f"restarted before fetching it. The message was: {undelivered_message}"
                    + (f" {answers[key]}" if key in answers else "")
                )
        if turns:
            import_captured_turns(
                conn,
                CaptureManifest(
                    f"mac-process:{replaced_by}",
                    f"mac-process:{replaced_by}:{conversation_id}",
                    now,
                    "captured",
                    None,
                    turns,
                ),
                now,
                claim_error_reason=MAC_RESTART_CLAIM_ERROR_REASON,
                ended_by="A Mac restart",
                turn_notes=answers,
            )
        conn.commit()
    finally:
        conn.close()


def close_mac_interruption_of_turn(
    db_path: str, conversation_id: str, turn_number: int, now: int, outcome: str
) -> None:
    """Close the Mac-restart interruption of a turn whose never-ran prompt has been
    settled (written again to a fresh agent, or dropped with its turn), so no recovery
    prompt is sent and its notice rides on no later prompt. The Ticket claim the
    interruption errored is restored, as a recovery would restore it."""
    from planner.core.db import connect
    from planner.tickets import data as tickets_data

    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for interruption_id, owner_kind, owner_id, claim_revision in conn.execute(
            "SELECT interruption_id,owner_kind,owner_id,claim_revision FROM deploy_interruptions "
            "WHERE source_conversation_id=? AND turn_number=? AND deployment_id LIKE "
            "'mac-process:%' AND recovery_state IN ('awaiting_action','unable_to_resume')",
            (conversation_id, turn_number),
        ).fetchall():
            conn.execute(
                "UPDATE deploy_interruptions SET recovery_state='closed',detail=detail||? "
                "WHERE interruption_id=?",
                (" " + outcome, interruption_id),
            )
            if owner_kind == "ticket" and owner_id is not None and claim_revision is not None:
                tickets_data.restore_interrupted_worker_step_claim(
                    conn,
                    str(owner_id),
                    conversation_id=conversation_id,
                    expected_claim_revision=int(claim_revision) + 1,
                    now=now,
                )
        conn.commit()
    finally:
        conn.close()
