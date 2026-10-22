"""Admitted deployment reconciliation and factual delivery receipts.

These writes never send backend traffic or release a worker to readiness.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import cast
from uuid import uuid4

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
            matched = matches_occurrence(
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
            state = "awaiting_action" if matched else "historical"
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
                    can_restart=False,
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


def bind_delivery(conn: sqlite3.Connection, conversation_id: str, turn_number: int) -> str | None:
    """Bind pending context to an actual delivery attempt, including a fresh Worker link."""
    rows = conn.execute(
        "SELECT d.interruption_id,d.detail FROM deploy_interruptions d WHERE "
        "d.recovery_state IN ('awaiting_action','unable_to_resume') AND "
        "d.exact_match=1 AND (d.source_conversation_id=? OR (d.owner_kind='ticket' "
        "AND EXISTS (SELECT 1 FROM tickets t WHERE t.id=d.owner_id AND "
        "t.conversation_id=? AND t.stage!='done'))) ORDER BY d.interruption_id",
        (conversation_id, conversation_id),
    ).fetchall()
    if not rows:
        return None
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO deploy_interruption_deliveries VALUES (?,?,?,'intent')",
            (row[0], conversation_id, turn_number),
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
) -> None:
    for fact in facts:
        if (
            fact.kind is not LifecycleKind.attempt
            or fact.operation is not LifecycleOperation.prompt
        ):
            continue
        if fact.status not in (
            LifecycleStatus.started,
            LifecycleStatus.refused,
            LifecycleStatus.unknown,
        ):
            continue
        status = str(fact.status) if composed_content_delivered else "context_not_delivered"
        state = "resumed" if status == "started" else "unable_to_resume"
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
