"""One-time, exact-cohort planning retirement. Never run this against production in rehearsal."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from planner.worker_types.contracts import RETIRED_PLANNING_WORKER_TYPES

MIGRATION_ID = "retire_planning_workers"
COHORT = json.loads(Path(__file__).with_name("planning_retirement_cohort.json").read_text())


def _row_hash(row: Any) -> str:
    return hashlib.sha256(
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def retire_planning_workers(conn: Any) -> None:
    """Cancel the reviewed cohort in place, refusing unexpected or unreconciled work."""
    rows = (
        conn.exec_driver_sql(
            "SELECT * FROM tickets WHERE worker_type IN (?,?,?) AND stage != 'done' ORDER BY id",
            tuple(sorted(RETIRED_PLANNING_WORKER_TYPES)),
        )
        .mappings()
        .all()
    )
    expected = {row["ticket_id"]: row for row in COHORT}
    present = conn.exec_driver_sql(
        "SELECT id FROM tickets WHERE id IN (" + ",".join("?" for _ in expected) + ")",
        tuple(expected),
    ).all()
    if (rows or present) and {row["id"] for row in rows} != set(expected):
        raise RuntimeError("obsolete planning cohort changed; review exact cohort before migration")
    tables = {
        row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if rows and "manager_wakes" in tables:
        active_batch = conn.exec_driver_sql(
            "SELECT DISTINCT b.id FROM manager_wake_batches b "
            "JOIN manager_wake_batch_members m ON m.batch_id=b.id "
            "JOIN manager_wakes w ON w.id=m.wake_id "
            "WHERE w.ticket_id IN (" + ",".join("?" for _ in expected) + ") "
            "AND b.status IN ('pending','offering','dispatching','accepted','uncertain')",
            tuple(expected),
        ).all()
        if active_batch:
            raise RuntimeError(
                "retired planning notice still has delivery ownership; reconcile normally"
            )
    retired_at = conn.exec_driver_sql("SELECT CAST(strftime('%s','now') AS INTEGER)").scalar_one()
    prepared: list[tuple[str, str]] = []
    for row in rows:
        original = expected[row["id"]]
        for key, source in (
            ("worker_type", "original_worker_type"),
            ("stage", "original_stage"),
            ("conversation_id", "conversation_id"),
        ):
            if row[key] != original[source]:
                raise RuntimeError(f"obsolete planning record changed: {row['id']} {key}")
        if (row["pending_proposal"] is not None) != original["has_pending_proposal"]:
            raise RuntimeError(f"obsolete planning proposal changed: {row['id']}")
        if row["worker_step_claim"] != "none":
            raise RuntimeError(f"planning claim needs verified runtime reconciliation: {row['id']}")
        values = json.loads(row["field_values"])
        reconciliation = None
        if original["worker_step_claim"] != "none":
            reconciliation = json.loads(values.get("retirement", "{}")).get("claim_reconciliation")
            if (
                not reconciliation
                or row["worker_step_claim_revision"] != original["worker_step_claim_revision"] + 1
            ):
                raise RuntimeError(f"planning claim reconciliation evidence missing: {row['id']}")
        elif row["worker_step_claim_revision"] != original["worker_step_claim_revision"]:
            raise RuntimeError(f"obsolete planning claim revision changed: {row['id']}")
        if reconciliation is None:
            if _row_hash(row) != original["original_record_sha256"]:
                raise RuntimeError(f"obsolete planning original record hash changed: {row['id']}")
        else:
            without_provenance = dict(row)
            values_without_provenance = dict(values)
            del values_without_provenance["retirement"]
            without_provenance["field_values"] = reconciliation["post_release_field_values"]
            if (
                reconciliation["original_record_sha256"] != original["original_record_sha256"]
                or _row_hash(without_provenance) != reconciliation["post_release_record_sha256"]
                or json.loads(reconciliation["post_release_field_values"])
                != values_without_provenance
                or reconciliation["conversation_id"] != row["conversation_id"]
                or reconciliation["is_running"] is not False
                or reconciliation["held_prompt_count"] != 0
            ):
                raise RuntimeError(f"planning reconciliation record hash changed: {row['id']}")
        if "retirement" in values and reconciliation is None:
            raise RuntimeError(f"unexpected retirement provenance: {row['id']}")
        values["retirement"] = json.dumps(
            {
                "migration": MIGRATION_ID,
                "retired_at": retired_at,
                "reason": "Obsolete Sprint/Today planning work retired without success.",
                "original_record_sha256": original["original_record_sha256"],
                "original_stage": row["stage"],
                "original_updated_at": (
                    reconciliation.get("original_updated_at")
                    if reconciliation
                    else row.get("updated_at")
                ),
                "original_pending_proposal_json": row["pending_proposal"],
                "original_pending_proposal_revision": row["pending_proposal_revision"],
                "original_worker_step_claim": original["worker_step_claim"],
                "original_worker_step_claim_revision": original["worker_step_claim_revision"],
                "claim_reconciliation": reconciliation,
                "closed_manager_wake_ids": [
                    wake[0]
                    for wake in conn.exec_driver_sql(
                        "SELECT id FROM manager_wakes WHERE ticket_id=? AND closed_at IS NULL",
                        (row["id"],),
                    )
                ]
                if "manager_wakes" in tables
                else [],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        prepared.append((json.dumps(values, ensure_ascii=False), row["id"]))
    for values_json, ticket_id in prepared:
        conn.exec_driver_sql(
            "UPDATE tickets SET stage='cancelled',pending_proposal=NULL,field_values=? WHERE id=?",
            (values_json, ticket_id),
        )
        if "manager_wakes" in tables:
            conn.exec_driver_sql(
                "UPDATE manager_wakes SET closed_at=? WHERE ticket_id=? AND closed_at IS NULL",
                (retired_at, ticket_id),
            )
        if "notification_attention_state" in tables:
            conn.exec_driver_sql(
                "UPDATE notification_attention_state SET active=0 WHERE subject_kind='ticket' "
                "AND subject_id=? AND notification_type IN "
                "('awaiting_approval','assigned','errored')",
                (ticket_id,),
            )
            conn.exec_driver_sql(
                "UPDATE notification_attention_edges SET decided=1 WHERE subject_kind='ticket' "
                "AND subject_id=? AND notification_type IN "
                "('awaiting_approval','assigned','errored')",
                (ticket_id,),
            )
    for row in conn.exec_driver_sql(
        "SELECT worker_type,definition_json FROM worker_types"
    ).mappings():
        if row["worker_type"] not in RETIRED_PLANNING_WORKER_TYPES:
            continue
        definition = json.loads(row["definition_json"])
        if not any(field["id"] == "retirement" for field in definition["fields"]):
            definition["fields"].append({"id": "retirement", "label": "Retirement"})
            conn.exec_driver_sql(
                "UPDATE worker_types SET definition_json=? WHERE worker_type=?",
                (json.dumps(definition, ensure_ascii=False), row["worker_type"]),
            )


async def reconcile_isolated_planning_claim(
    conn: sqlite3.Connection,
    conversations: Any,
    ticket_id: str,
    *,
    now: int,
    isolated_database: Path,
    production_database: Path,
) -> dict[str, Any]:
    """Use the normal claim CAS under the runtime lifecycle lock on an isolated copy only.

    The caller must use the runtime which owns the isolated conversation state. No server
    startup calls this helper. Live cutover remains a separately authorized operation.
    """
    from planner.runtime import conversation_start
    from planner.tickets import data
    from planner.tickets.contracts import WorkerStepClaim

    actual = conn.execute("PRAGMA database_list").fetchone()[2]
    if (
        not actual
        or Path(actual).resolve() != isolated_database.resolve()
        or isolated_database.resolve() == production_database.resolve()
        or isolated_database.resolve()
        == Path("/home/vps/Deployments/Panels/current/data/planner.db").resolve()
    ):
        raise RuntimeError("claim rehearsal requires the explicit isolated database connection")
    expected = next((row for row in COHORT if row["ticket_id"] == ticket_id), None)
    if expected is None or expected["worker_step_claim"] == "none":
        raise RuntimeError(
            "claim reconciliation is restricted to reviewed outstanding planning claim"
        )
    async with conversation_start.conversation_link_lock(f"ticket:{ticket_id}"):
        before = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if before is None or before["conversation_id"] != expected["conversation_id"]:
            raise RuntimeError("planning conversation identity changed")
        if _row_hash(before) != expected["original_record_sha256"]:
            raise RuntimeError("reviewed planning original record hash changed")
        conversation_id = before["conversation_id"]
        running = await conversations.is_running(conversation_id)
        held = await conversations.held_prompts(conversation_id)
        if running or held:
            raise RuntimeError("planning runtime still owns traffic")
        evidence = {
            "conversation_id": conversation_id,
            "is_running": running,
            "held_prompt_count": len(held),
            "checked_at": now,
            "source": "owning isolated ConversationSystem under conversation_link_lock",
        }
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if dict(current) != dict(before):
                raise RuntimeError("planning record changed during runtime inspection")
            if (
                current["worker_step_claim_revision"] != expected["worker_step_claim_revision"]
                or current["worker_step_claim"] != expected["worker_step_claim"]
            ):
                raise RuntimeError("reviewed planning claim changed")
            if not data.release_worker_step_claim(
                conn,
                ticket_id,
                expected_claim=WorkerStepClaim(current["worker_step_claim"]),
                expected_claim_revision=current["worker_step_claim_revision"],
                now=now,
            ):
                raise RuntimeError("planning claim CAS refused")
            released = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            evidence["original_updated_at"] = current["updated_at"]
            evidence["original_record_sha256"] = expected["original_record_sha256"]
            evidence["post_release_record_sha256"] = _row_hash(released)
            evidence["post_release_field_values"] = released["field_values"]
            values = json.loads(current["field_values"])
            if "retirement" in values:
                raise RuntimeError("retirement provenance already exists")
            values["retirement"] = json.dumps({"claim_reconciliation": evidence})
            conn.execute(
                "UPDATE tickets SET field_values=? WHERE id=?", (json.dumps(values), ticket_id)
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return evidence
