"""Deliver immutable Ticket decisions through the ordinary conversation door."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryFate,
    PromptDeliveryInjected,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliverySuppressed,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.core.task_settlement import run_on_worker_thread
from planner.executor.clock import now_ms
from planner.runtime import conversation_start
from planner.tickets import data as tickets_data
from planner.tickets import decision_messages, locale_configuration
from planner.tickets.contracts import TicketLocale
from planner.worker_types.registry import WorkerTypeRegistry

_log = logging.getLogger(__name__)


def _set_status(
    conn: sqlite3.Connection, identity: str, status: str, conversation_id: str | None
) -> None:
    conn.execute(
        "UPDATE ticket_decision_messages SET delivery_status=?,conversation_id=? "
        "WHERE decision_id=?",
        (status, conversation_id, identity),
    )
    conn.commit()


async def deliver_ticket_decision_messages(
    ticket_id: str,
    *,
    connect_database: Callable[[], sqlite3.Connection],
    conversation_system: ConversationSystem,
    worker_type_registry: WorkerTypeRegistry,
    now: Callable[[], int],
) -> PromptDeliveryFate | None:
    """A queue admission stays pending. A lost in-flight receipt never causes replay."""
    last_fate: PromptDeliveryFate | None = None
    async with conversation_start.conversation_link_lock(f"ticket:{ticket_id}"):
        conn = await run_on_worker_thread(connect_database)
        try:
            ticket = tickets_data.read_ticket(conn, ticket_id)
            if ticket.is_backlogged or not locale_configuration.can_execute_locale(
                conn, ticket.locale
            ):
                return None
            if ticket.locale is TicketLocale.macos and not locale_configuration.mac_is_present(
                conn, now_ms()
            ):
                return None
            from planner.conversation.backend_state import model_is_enabled
            from planner.conversation.contracts import require_conversation_backend_key

            if ticket.employee_launch_model is not None and not model_is_enabled(
                conn,
                require_conversation_backend_key(ticket.employee_backend),
                ticket.employee_launch_model,
            ):
                return None
            rows = conn.execute(
                "SELECT * FROM ticket_decision_messages WHERE ticket_id=? "
                "AND delivery_status != 'delivered' ORDER BY created_at,rowid",
                (ticket_id,),
            ).fetchall()
            for row in rows:
                identity = str(row["decision_id"])
                sender_message_id = str(row["sender_message_id"])
                target = row["conversation_id"]
                status = decision_messages.recorded_outcome(conn, target, sender_message_id)
                if status is not None:
                    if status == row["delivery_status"] and status != "delivered":
                        break
                    await run_on_worker_thread(_set_status, conn, identity, status, target)
                    if status != "delivered":
                        await run_on_worker_thread(
                            tickets_data.mark_worker_step_claim_errored,
                            conn,
                            ticket_id,
                            expected_claim=ticket.worker_step_claim,
                            expected_claim_revision=ticket.worker_step_claim_revision,
                            now=now(),
                            error_reason=f"decision delivery {status}",
                        )
                        break
                    continue
                if row["delivery_status"] in ("uncertain", "refused", "discarded"):
                    break
                if row["delivery_status"] != "pending":
                    if target is not None:
                        held = await conversation_system.held_prompts(str(target))
                        if any(item.sender_message_id == sender_message_id for item in held):
                            continue
                    # Sending was durably marked before the wire. Without an outcome or
                    # an owned queue item, restart cannot know whether it crossed.
                    await run_on_worker_thread(_set_status, conn, identity, "uncertain", target)
                    await run_on_worker_thread(
                        tickets_data.mark_worker_step_claim_errored,
                        conn,
                        ticket_id,
                        expected_claim=ticket.worker_step_claim,
                        expected_claim_revision=ticket.worker_step_claim_revision,
                        now=now(),
                        error_reason="decision delivery uncertain",
                    )
                    _log.error("decision delivery receipt is uncertain (ticket=%s)", ticket_id)
                    break

                async def content_for(
                    sending_into: str,
                    *,
                    decision_identity: str = identity,
                    model_text: str = str(row["model_text"]),
                ) -> MessageContent:
                    await run_on_worker_thread(
                        _set_status, conn, decision_identity, "sending", sending_into
                    )
                    return text_message_content(model_text)

                try:
                    ticket = tickets_data.read_ticket(conn, ticket_id)
                    delivered = await conversation_start.send_to_ticket_conversation(
                        conversation_system,
                        conn,
                        ticket_id,
                        content_for,
                        conversation_id=ticket.conversation_id,
                        created_conversation_id=conversation_start.new_conversation_id(),
                        sender_label="system",
                        sender_message_id=sender_message_id,
                        worker_type_registry=worker_type_registry,
                        retain_backend_start_failure=True,
                        now=now(),
                    )
                except Exception:
                    # A setup failure before content_for is safe to retry. Anything
                    # after the marker stays ambiguous until its conversation outcome.
                    _log.exception("decision message could not be sent (ticket=%s)", ticket_id)
                    attempt = conn.execute(
                        "SELECT delivery_status,conversation_id FROM ticket_decision_messages "
                        "WHERE decision_id=?",
                        (identity,),
                    ).fetchone()
                    if attempt is not None and attempt["delivery_status"] == "sending":
                        await run_on_worker_thread(
                            _set_status, conn, identity, "uncertain", attempt["conversation_id"]
                        )
                        await run_on_worker_thread(
                            tickets_data.mark_worker_step_claim_errored,
                            conn,
                            ticket_id,
                            expected_claim=ticket.worker_step_claim,
                            expected_claim_revision=ticket.worker_step_claim_revision,
                            now=now(),
                            error_reason="decision delivery uncertain",
                        )
                    break
                last_fate = delivered.fate
                if isinstance(delivered.fate, (PromptDeliveryStarted, PromptDeliveryInjected)):
                    status = "delivered"
                elif isinstance(delivered.fate, PromptDeliveryQueued):
                    status = "queued"
                elif isinstance(delivered.fate, PromptDeliveryRefused):
                    status = "refused"
                elif isinstance(delivered.fate, PromptDeliverySuppressed):
                    status = "discarded"
                else:
                    status = "uncertain"
                await run_on_worker_thread(
                    _set_status, conn, identity, status, delivered.conversation_id
                )
                if status not in ("delivered", "queued"):
                    await run_on_worker_thread(
                        tickets_data.mark_worker_step_claim_errored,
                        conn,
                        ticket_id,
                        expected_claim=ticket.worker_step_claim,
                        expected_claim_revision=ticket.worker_step_claim_revision,
                        now=now(),
                        error_reason=f"decision delivery {status}",
                    )
                    break
        finally:
            await run_on_worker_thread(conn.close)
    return last_fate
