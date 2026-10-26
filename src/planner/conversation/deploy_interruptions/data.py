"""Admitted deployment reconciliation and factual delivery receipts.

These writes never send backend traffic or release a worker to readiness.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import cast
from uuid import uuid4

from planner.conversation.contracts import DeploymentRecoveryReference
from planner.conversation.deploy_interruptions.contracts import (
    CapturedTurn,
    CaptureManifest,
    InterruptionAccount,
    InterruptionEntry,
    InterruptionSummary,
    OwnerKind,
    RecoveryState,
)
from planner.conversation.deploy_interruptions.logic import (
    claim_matches,
    combine_capture_states,
    matches_occurrence,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import WorkerStepClaim


def _identity(manifest: CaptureManifest, turn: CapturedTurn) -> str:
    occurrence = (
        turn.occurrence_id or f"{turn.runtime_epoch}:{turn.conversation_id}:{turn.turn_number}"
    )
    return hashlib.sha256(f"{manifest.deployment_id}:{occurrence}".encode()).hexdigest()


def reconcile(conn: sqlite3.Connection, manifests: tuple[CaptureManifest, ...], now: int) -> None:
    """Import immutable evidence before boot changes the runtime epoch."""
    epoch_row = conn.execute(
        "SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1"
    ).fetchone()
    if (
        epoch_row is None
        and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='signal_publication_meta'"
        ).fetchone()
        is not None
    ):
        epoch_row = conn.execute(
            "SELECT runtime_epoch FROM signal_publication_meta WHERE singleton=1"
        ).fetchone()
    old_epoch = None if epoch_row is None else str(epoch_row[0])
    for manifest in manifests:
        conn.execute(
            "INSERT OR IGNORE INTO deploy_interruption_accounts VALUES (?,?,?,?,?)",
            (
                manifest.capture_id,
                manifest.deployment_id,
                manifest.captured_at,
                manifest.state,
                manifest.detail,
            ),
        )
        for turn in manifest.turns:
            interruption_id = _identity(manifest, turn)
            if conn.execute(
                "SELECT 1 FROM deploy_interruptions WHERE interruption_id=?", (interruption_id,)
            ).fetchone():
                continue
            occurrence = conn.execute(
                "SELECT occurrence_id FROM conversation_turn_occurrences WHERE "
                "conversation_id=? AND turn_number=?",
                (turn.conversation_id, turn.turn_number),
            ).fetchone()
            fact = conn.execute(
                "SELECT status FROM conversation_lifecycle_facts WHERE conversation_id=? "
                "AND kind='turn' AND turn_number=?",
                (turn.conversation_id, turn.turn_number),
            ).fetchone()
            exists = (
                conn.execute(
                    "SELECT 1 FROM conversations WHERE conversation_id=?", (turn.conversation_id,)
                ).fetchone()
                is not None
            )
            from planner.conversation.lifecycle_storage import native_execution_is_unresolved

            native_unresolved = native_execution_is_unresolved(conn, turn.conversation_id)
            matched = not native_unresolved and matches_occurrence(
                turn.occurrence_id,
                None if occurrence is None else str(occurrence[0]),
                turn.runtime_epoch,
                old_epoch,
                None if fact is None else str(fact[0]),
            )
            uncertain = turn.original_status == "intent"
            detail = (
                "A deployment stopped this turn before its launch outcome was certain. "
                "External actions can remain uncertain."
                if uncertain
                else "A deployment ended this turn. External actions can remain uncertain."
            )
            uncertain_recovery = (
                conn.execute(
                    "SELECT 1 FROM deploy_interruption_deliveries WHERE conversation_id=? "
                    "AND turn_number=? AND status IN ('intent','unknown') LIMIT 1",
                    (turn.conversation_id, turn.turn_number),
                ).fetchone()
                is not None
            )
            state = "awaiting_action" if matched else "historical"
            if native_unresolved:
                detail = (
                    "The native executor owns this turn. A VPS stop does not prove termination."
                )
            if uncertain_recovery:
                state = "unable_to_resume"
                detail += " Recovery delivery outcome is unknown. No automatic retry occurred."
            if not exists:
                detail += " Its conversation is unavailable after restore."
            elif not matched:
                detail += (
                    " This historical account does not match an unresolved turn "
                    "in the current database."
                )
            ticket = None
            if turn.owner_kind == "ticket" and turn.owner_id is not None:
                ticket = conn.execute(
                    "SELECT stage,conversation_id,worker_step_claim,worker_step_claim_revision "
                    "FROM tickets WHERE id=?",
                    (turn.owner_id,),
                ).fetchone()
                if ticket is not None and str(ticket[0]) == "done":
                    state = "closed"
                    detail += " The Ticket is complete."
                elif ticket is not None and claim_matches(
                    exact=matched,
                    captured_conversation=turn.conversation_id,
                    current_conversation=ticket[1],
                    captured_revision=turn.claim_revision,
                    current_revision=int(ticket[3]),
                    captured_claim=turn.claim,
                    current_claim=str(ticket[2]),
                    stage=str(ticket[0]),
                ):
                    assert turn.claim_revision is not None
                    tickets_data.mark_worker_step_claim_errored(
                        conn,
                        turn.owner_id,
                        expected_claim=WorkerStepClaim.out,
                        expected_claim_revision=turn.claim_revision,
                        now=now,
                        notify_manager=False,
                    )
            conn.execute(
                "INSERT INTO deploy_interruptions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    interruption_id,
                    manifest.capture_id,
                    manifest.deployment_id,
                    turn.conversation_id,
                    turn.turn_number,
                    turn.occurrence_id,
                    turn.runtime_epoch,
                    turn.display_name,
                    turn.owner_kind,
                    turn.owner_id,
                    turn.claim_revision,
                    turn.original_status,
                    state,
                    detail,
                    int(matched),
                ),
            )
    # A new epoch makes legacy attribution across later snapshot restores explicit.
    conn.execute(
        "INSERT INTO conversation_runtime_epoch VALUES (1,?) ON CONFLICT(singleton) "
        "DO UPDATE SET epoch=excluded.epoch",
        (uuid4().hex,),
    )
    # A process lost after an actual wire attempt leaves an unknown receipt, never resumed.
    for delivery in conn.execute(
        "SELECT DISTINCT conversation_id,turn_number FROM deploy_interruption_deliveries "
        "WHERE status='intent'"
    ).fetchall():
        fail_recovery_claims(
            conn, str(delivery[0]), int(delivery[1]), now=now, notify_manager=False
        )
    conn.execute(
        "UPDATE deploy_interruptions SET "
        "recovery_state='unable_to_resume',detail=detail || ' Recovery delivery "
        "outcome is unknown. Inspect current work before another action.' WHERE "
        "interruption_id IN (SELECT interruption_id FROM "
        "deploy_interruption_deliveries WHERE status='intent') AND "
        "recovery_state='awaiting_action'"
    )
    conn.execute("UPDATE deploy_interruption_deliveries SET status='unknown' WHERE status='intent'")


def read_summary(conn: sqlite3.Connection) -> InterruptionSummary:
    accounts_by_deployment: dict[str, InterruptionAccount] = {}
    for account in conn.execute(
        "SELECT capture_id,deployment_id,capture_state,detail FROM "
        "deploy_interruption_accounts ORDER BY captured_at DESC,capture_id"
    ):
        entries: list[InterruptionEntry] = []
        for row in conn.execute(
            "SELECT d.*, c.conversation_id AS live_conversation_id,t.stage AS ticket_stage FROM "
            "deploy_interruptions d LEFT JOIN conversations c ON "
            "c.conversation_id=d.source_conversation_id "
            "LEFT JOIN tickets t ON d.owner_kind='ticket' AND t.id=d.owner_id "
            "WHERE d.capture_id=? ORDER BY "
            "d.display_name,d.interruption_id",
            (account[0],),
        ):
            closed = row["ticket_stage"] == "done" and row["recovery_state"] in (
                "awaiting_action",
                "unable_to_resume",
            )
            entries.append(
                InterruptionEntry(
                    interruption_id=str(row["interruption_id"]),
                    deployment_id=str(row["deployment_id"]),
                    conversation_id=row["live_conversation_id"],
                    display_name=str(row["display_name"]),
                    owner_kind=cast(OwnerKind, row["owner_kind"]),
                    owner_id=row["owner_id"],
                    original_status=str(row["original_status"]),
                    recovery_state="closed"
                    if closed
                    else cast(RecoveryState, row["recovery_state"]),
                    detail=str(row["detail"]) + (" The Ticket is complete." if closed else ""),
                )
            )
        deployment_id = str(account[1])
        incoming = InterruptionAccount(
            deployment_id=deployment_id,
            capture_state=account[2],
            detail=account[3],
            entries=entries,
        )
        existing = accounts_by_deployment.get(deployment_id)
        if existing is None:
            accounts_by_deployment[deployment_id] = incoming
        else:
            existing["capture_state"] = combine_capture_states(
                existing["capture_state"], incoming["capture_state"]
            )
            existing["entries"].extend(entries)
            details = tuple(
                dict.fromkeys(
                    detail for detail in (existing["detail"], incoming["detail"]) if detail
                )
            )
            existing["detail"] = " ".join(details) or None
    return InterruptionSummary(accounts=list(accounts_by_deployment.values()))


def bind_delivery(
    conn: sqlite3.Connection,
    conversation_id: str,
    turn_number: int,
    references: tuple[DeploymentRecoveryReference, ...] | None = None,
    recovery_sender_message_ids: dict[str, str] | None = None,
) -> str | None:
    """Bind pending context to an actual delivery attempt, including a fresh Worker link."""
    rows = conn.execute(
        "SELECT d.interruption_id,d.detail FROM deploy_interruptions d WHERE "
        "d.recovery_state IN ('awaiting_action','unable_to_resume') AND "
        "d.exact_match=1 AND (d.source_conversation_id=? OR (d.owner_kind='ticket' "
        "AND EXISTS (SELECT 1 FROM tickets t WHERE t.id=d.owner_id AND "
        "t.conversation_id=? AND t.stage!='done'))) ORDER BY d.interruption_id",
        (conversation_id, conversation_id),
    ).fetchall()
    if references is not None:
        selected = {ref.interruption_id for ref in references}
        rows = [row for row in rows if str(row[0]) in selected]
    if not rows:
        return None
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO deploy_interruption_deliveries "
            "(interruption_id,conversation_id,turn_number,status) VALUES (?,?,?,'intent')",
            (row[0], conversation_id, turn_number),
        )
        if recovery_sender_message_ids is not None:
            conn.execute(
                "UPDATE deploy_interruption_deliveries SET recovery_sender_message_id=? "
                "WHERE interruption_id=? AND conversation_id=? AND turn_number=?",
                (recovery_sender_message_ids[str(row[0])], row[0], conversation_id, turn_number),
            )
    return (
        "[Panels deployment interruption context]\n"
        + "\n".join(str(row[1]) for row in rows)
        + (
            "\nThe previous work did not resume automatically. "
            "Inspect current work before repeating any external action. "
            "Do not replay unknown prompts or permission answers."
        )
    )


def settle_delivery(
    conn: sqlite3.Connection,
    conversation_id: str,
    facts: tuple[LifecycleFact, ...],
    *,
    composed_content_delivered: bool = True,
    now: int,
) -> None:
    for fact in facts:
        if (
            fact.kind is not LifecycleKind.attempt
            or fact.operation is not LifecycleOperation.prompt
        ):
            continue
        if fact.status in (
            LifecycleStatus.suppressed,
            LifecycleStatus.refused,
            LifecycleStatus.unknown,
        ):
            fail_recovery_claims(
                conn,
                conversation_id,
                fact.turn_number,
                now=now,
                notify_manager=fact.status is not LifecycleStatus.suppressed,
            )
        if fact.status is LifecycleStatus.suppressed:
            # Admission proves that no prompt reached the wire. Release only this
            # attempt's provisional binding so the next actual send retains context.
            conn.execute(
                "DELETE FROM deploy_interruption_deliveries WHERE conversation_id=? "
                "AND turn_number=? AND status='intent'",
                (conversation_id, fact.turn_number),
            )
            continue
        if fact.status not in (
            LifecycleStatus.started,
            LifecycleStatus.refused,
            LifecycleStatus.unknown,
        ):
            continue
        status = str(fact.status) if composed_content_delivered else "context_not_delivered"
        state = "resumed" if status == "started" else "unable_to_resume"
        if state == "resumed":
            for claim in conn.execute(
                "SELECT d.owner_id,x.restored_claim_revision,t.stage FROM "
                "deploy_interruption_deliveries x JOIN deploy_interruptions d "
                "USING(interruption_id) JOIN tickets t ON t.id=d.owner_id "
                "JOIN ticket_paired_stage_openers p ON p.ticket_id=t.id AND p.stage=t.stage "
                "WHERE x.conversation_id=? AND x.turn_number=? AND x.status='intent' "
                "AND d.owner_kind='ticket' AND x.restored_claim_revision IS NOT NULL",
                (conversation_id, fact.turn_number),
            ).fetchall():
                tickets_data.release_worker_step_claim(
                    conn,
                    str(claim[0]),
                    expected_claim=WorkerStepClaim.out,
                    expected_claim_revision=int(claim[1]),
                    now=now,
                )
        if state != "resumed":
            fail_recovery_claims(conn, conversation_id, fact.turn_number, now=now)
        conn.execute(
            "UPDATE deploy_interruptions SET recovery_state=?,detail=detail || ? WHERE "
            "interruption_id IN (SELECT interruption_id FROM "
            "deploy_interruption_deliveries WHERE conversation_id=? AND turn_number=? "
            "AND status='intent')",
            (
                state,
                (
                    " Recovery started with this notice in its backend prompt."
                    if state == "resumed"
                    else f" Recovery delivery: {status}. No automatic retry occurred."
                ),
                conversation_id,
                fact.turn_number,
            ),
        )
        conn.execute(
            "UPDATE deploy_interruption_deliveries SET status=? WHERE conversation_id=? "
            "AND turn_number=? AND status='intent'",
            (status, conversation_id, fact.turn_number),
        )


def _owner_blocker(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    conversation_id = str(row["source_conversation_id"])
    kind, owner_id = row["owner_kind"], row["owner_id"]
    if (
        conn.execute(
            "SELECT 1 FROM conversations WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        is None
    ):
        return "the captured conversation is unavailable"
    if kind == "ticket":
        ticket = conn.execute(
            "SELECT conversation_id FROM tickets WHERE id=?", (owner_id,)
        ).fetchone()
        if ticket is None or ticket[0] != conversation_id:
            return "the Ticket conversation link changed"
    elif kind in ("chief", "manager"):
        from planner.core.errors import PlannerError
        from planner.managers.data import manager_agent_key

        try:
            key = "chief_of_staff" if kind == "chief" else manager_agent_key(str(owner_id), conn)
        except PlannerError:
            return "the Manager conversation identity conflicts"
        agent = conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key=?", (key,)
        ).fetchone()
        if agent is None or agent[0] != conversation_id:
            return "the owner conversation link changed"
    elif conn.execute(
        "SELECT 1 FROM tickets WHERE conversation_id=? UNION ALL SELECT 1 FROM agents "
        "WHERE conversation_id=?",
        (conversation_id, conversation_id),
    ).fetchone():
        return "the conversation now has a different owner"
    return None


def recovery_blocker(
    conn: sqlite3.Connection,
    conversation_id: str,
    reference: DeploymentRecoveryReference,
    *,
    allow_restored: bool = False,
) -> str | None:
    from planner.conversation.lifecycle_storage import native_execution_is_unresolved

    if native_execution_is_unresolved(conn, conversation_id):
        return "native executor reconciliation owns this turn"
    row = conn.execute(
        "SELECT * FROM deploy_interruptions WHERE interruption_id=?", (reference.interruption_id,)
    ).fetchone()
    if row is None or row["source_conversation_id"] != conversation_id or not row["exact_match"]:
        return "the interruption identity does not match"
    if row["recovery_state"] != "awaiting_action":
        return "the interruption is no longer eligible"
    blocker = _owner_blocker(conn, row)
    if blocker is not None:
        return blocker
    if row["owner_kind"] != "ticket":
        return None
    from planner.core import ticket_blocks
    from planner.tickets import locale_configuration
    from planner.tickets.contracts import StageOwnershipMode
    from planner.tickets.logic import machine
    from planner.worker_types.configuration import configured_worker_type_registry

    ticket = tickets_data.read_ticket(conn, str(row["owner_id"]))
    definition = configured_worker_type_registry().require(ticket.worker_type)
    if ticket.stage != reference.expected_stage:
        return "the Ticket Stage changed"
    if ticket.is_backlogged:
        return "the Ticket is backlogged"
    if ticket.locale != locale_configuration.CURRENT_EXECUTION_LOCALE:
        return "the Ticket execution locale changed"
    if definition.is_terminal(ticket.stage) or definition.gating_field(ticket.stage) is None:
        return "the Ticket has no eligible worker step"
    if ticket.pending_proposal is not None:
        return "the Ticket has a pending proposal"
    if ticket.id in ticket_blocks.blocked_ticket_ids(conn):
        return "the Ticket has a live blocker"
    if definition.stage_index(ticket.stage) > definition.stage_index(ticket.ceiling):
        return "the Ticket Stage is beyond its ceiling"
    if (
        machine.stage_ownership_mode(ticket.stage, worker_type_definition=definition)
        is StageOwnershipMode.user
    ):
        if (
            conn.execute(
                "SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=? AND stage=?",
                (ticket.id, ticket.stage),
            ).fetchone()
            is None
        ):
            return "the captured user Stage opener is unavailable"
    captured = row["claim_revision"]
    if captured is None:
        return "the captured Ticket claim is unavailable"
    restored = conn.execute(
        "SELECT restored_claim_revision FROM deploy_interruption_deliveries WHERE "
        "interruption_id=? AND conversation_id=? AND status='intent' AND "
        "restored_claim_revision IS NOT NULL",
        (reference.interruption_id, conversation_id),
    ).fetchone()
    if (
        allow_restored
        and restored is not None
        and ticket.worker_step_claim is WorkerStepClaim.out
        and ticket.worker_step_claim_revision == int(restored[0])
    ):
        return None
    if (
        ticket.worker_step_claim is not WorkerStepClaim.errored
        or ticket.worker_step_claim_revision != int(captured) + 1
    ):
        return "the captured Ticket claim changed"
    return None


def select_recoveries(
    conn: sqlite3.Connection,
) -> dict[str, tuple[DeploymentRecoveryReference, ...]]:
    selected: dict[str, list[DeploymentRecoveryReference]] = {}
    for row in conn.execute(
        "SELECT d.*,t.stage AS current_stage FROM deploy_interruptions d LEFT JOIN "
        "tickets t ON d.owner_kind='ticket' AND t.id=d.owner_id WHERE d.exact_match=1 "
        "AND d.recovery_state='awaiting_action' ORDER BY d.interruption_id"
    ):
        conversation_id = str(row["source_conversation_id"])
        reference = DeploymentRecoveryReference(str(row["interruption_id"]), row["current_stage"])
        if recovery_blocker(conn, conversation_id, reference) is None:
            selected.setdefault(conversation_id, []).append(reference)
    return {key: tuple(value) for key, value in selected.items()}


def admit_recovery(
    conn: sqlite3.Connection,
    conversation_id: str,
    references: tuple[DeploymentRecoveryReference, ...],
    now: int,
) -> str | None:
    for reference in references:
        reason = recovery_blocker(conn, conversation_id, reference, allow_restored=True)
        if reason is not None:
            conn.execute(
                "UPDATE deploy_interruptions SET detail=detail || ? WHERE interruption_id=?",
                (" Recovery suppressed: " + reason + ".", reference.interruption_id),
            )
            return reason
    for reference in references:
        row = conn.execute(
            "SELECT * FROM deploy_interruptions WHERE interruption_id=?",
            (reference.interruption_id,),
        ).fetchone()
        if row["owner_kind"] != "ticket":
            continue
        restored = conn.execute(
            "SELECT restored_claim_revision FROM deploy_interruption_deliveries WHERE "
            "interruption_id=? AND conversation_id=? AND status='intent'",
            (reference.interruption_id, conversation_id),
        ).fetchone()
        # Early admission is read-only; only restore after binding, immediately before wire.
        if restored is None or restored[0] is not None:
            continue
        revision = tickets_data.restore_interrupted_worker_step_claim(
            conn,
            str(row["owner_id"]),
            conversation_id=conversation_id,
            expected_claim_revision=int(row["claim_revision"]) + 1,
            now=now,
        )
        if revision is None:
            # Multiple deployment manifests may describe the same interrupted claim.
            prior = conn.execute(
                "SELECT x.restored_claim_revision FROM deploy_interruption_deliveries x "
                "JOIN deploy_interruptions d USING(interruption_id) WHERE "
                "x.conversation_id=? AND x.status='intent' AND d.owner_id=? "
                "AND d.claim_revision=? AND x.restored_claim_revision IS NOT NULL",
                (conversation_id, row["owner_id"], row["claim_revision"]),
            ).fetchone()
            current = tickets_data.read_ticket(conn, str(row["owner_id"]))
            if (
                prior is None
                or current.worker_step_claim is not WorkerStepClaim.out
                or current.worker_step_claim_revision != int(prior[0])
            ):
                return "the captured Ticket claim changed"
            revision = int(prior[0])
        conn.execute(
            "UPDATE deploy_interruption_deliveries SET restored_claim_revision=? WHERE "
            "interruption_id=? AND conversation_id=? AND status='intent'",
            (revision, reference.interruption_id, conversation_id),
        )
    return None


def fail_recovery_claims(
    conn: sqlite3.Connection,
    conversation_id: str,
    turn_number: int,
    *,
    now: int,
    notify_manager: bool = True,
) -> None:
    for row in conn.execute(
        "SELECT d.owner_id,d.claim_revision,x.restored_claim_revision FROM "
        "deploy_interruption_deliveries x JOIN deploy_interruptions d "
        "USING(interruption_id) WHERE x.conversation_id=? AND x.turn_number=? AND "
        "x.status='intent' AND d.owner_kind='ticket' AND d.claim_revision IS NOT NULL",
        (conversation_id, turn_number),
    ).fetchall():
        if row[2] is None and not notify_manager:
            # Suppression and reconciliation do not manufacture a new failed claim.
            continue
        tickets_data.mark_worker_step_claim_errored(
            conn,
            str(row[0]),
            expected_claim=(WorkerStepClaim.out if row[2] is not None else WorkerStepClaim.errored),
            expected_claim_revision=(int(row[2]) if row[2] is not None else int(row[1]) + 1),
            now=now,
            notify_manager=notify_manager,
        )
