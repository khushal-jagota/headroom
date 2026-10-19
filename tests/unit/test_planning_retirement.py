"""The approved retirement is truthful, exact, and preserves historical work."""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from planner.core.migrations import planning_retirement as retirement


@pytest.fixture(autouse=True)
def _separate_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retirement, "COHORT", json.loads(json.dumps(retirement.COHORT)))


def _database() -> sa.Connection:
    engine = sa.create_engine("sqlite://")
    conn = engine.connect()
    conn.exec_driver_sql(
        "CREATE TABLE tickets(id TEXT PRIMARY KEY,worker_type TEXT,stage TEXT,"
        "conversation_id TEXT,worker_step_claim TEXT,worker_step_claim_revision INTEGER,"
        "pending_proposal TEXT,pending_proposal_revision INTEGER,field_values TEXT)"
    )
    conn.exec_driver_sql("CREATE TABLE worker_types(worker_type TEXT,definition_json TEXT)")
    for row in retirement.COHORT:
        conn.exec_driver_sql(
            "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?)",
            (
                row["ticket_id"],
                row["original_worker_type"],
                row["original_stage"],
                row["conversation_id"],
                row["worker_step_claim"],
                row["worker_step_claim_revision"],
                '{"body":"original proposal"}' if row["has_pending_proposal"] else None,
                5,
                json.dumps({"review": "original writing"}),
            ),
        )
        original = (
            conn.exec_driver_sql("SELECT * FROM tickets WHERE id=?", (row["ticket_id"],))
            .mappings()
            .one()
        )
        row["original_record_sha256"] = retirement._row_hash(original)
        if row["worker_step_claim"] != "none":
            conn.exec_driver_sql(
                "UPDATE tickets SET worker_step_claim='none',"
                "worker_step_claim_revision=worker_step_claim_revision+1 WHERE id=?",
                (row["ticket_id"],),
            )
            released = (
                conn.exec_driver_sql("SELECT * FROM tickets WHERE id=?", (row["ticket_id"],))
                .mappings()
                .one()
            )
            evidence = {
                "conversation_id": row["conversation_id"],
                "is_running": False,
                "held_prompt_count": 0,
                "original_record_sha256": row["original_record_sha256"],
                "post_release_record_sha256": retirement._row_hash(released),
                "post_release_field_values": released["field_values"],
            }
            values = json.loads(released["field_values"])
            values["retirement"] = json.dumps({"claim_reconciliation": evidence})
            conn.exec_driver_sql(
                "UPDATE tickets SET field_values=? WHERE id=?",
                (json.dumps(values), row["ticket_id"]),
            )
    for ticket_id, worker_type, stage in [
        ("completed", "planning-day", "done"),
        ("ordinary", "coding", "needs_plan"),
    ]:
        conn.exec_driver_sql(
            "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?)",
            (ticket_id, worker_type, stage, "conversation", "none", 0, None, 0, "{}"),
        )
    return conn


def test_exact_retirement_preserves_proposals_and_other_records() -> None:
    with _database() as conn:
        untouched = conn.exec_driver_sql(
            "SELECT * FROM tickets WHERE id IN ('ordinary','completed')"
        ).all()
        before = {
            row["id"]: dict(row) for row in conn.exec_driver_sql("SELECT * FROM tickets").mappings()
        }
        retirement.retire_planning_workers(conn)
        rows = (
            conn.exec_driver_sql("SELECT * FROM tickets WHERE stage='cancelled'").mappings().all()
        )
        assert len(rows) == 44
        for row in rows:
            provenance = json.loads(json.loads(row["field_values"])["retirement"])
            assert (
                provenance["original_pending_proposal_json"]
                == before[row["id"]]["pending_proposal"]
            )
            assert provenance["original_stage"] == before[row["id"]]["stage"]
            assert row["pending_proposal"] is None
            assert row["conversation_id"] == before[row["id"]]["conversation_id"]
            assert json.loads(row["field_values"])["review"] == "original writing"
        assert (
            untouched
            == conn.exec_driver_sql(
                "SELECT * FROM tickets WHERE id IN ('ordinary','completed')"
            ).all()
        )


@pytest.mark.parametrize(
    "change",
    [
        "worker_step_claim='out'",
        "worker_step_claim_revision=999",
        "stage='unexpected'",
        "field_values='{}'",
    ],
)
def test_drift_aborts_before_any_retirement(change: str) -> None:
    with _database() as conn:
        conn.exec_driver_sql(
            f"UPDATE tickets SET {change} WHERE id=?", (retirement.COHORT[0]["ticket_id"],)
        )
        with pytest.raises(RuntimeError):
            retirement.retire_planning_workers(conn)
        assert (
            conn.exec_driver_sql("SELECT COUNT(*) FROM tickets WHERE stage='cancelled'").scalar()
            == 0
        )


def test_unknown_planning_work_is_not_automatically_retired() -> None:
    with _database() as conn:
        conn.exec_driver_sql("UPDATE tickets SET stage='needs_review' WHERE id='completed'")
        with pytest.raises(RuntimeError, match="cohort changed"):
            retirement.retire_planning_workers(conn)




def test_changed_proposal_body_aborts_even_when_proposal_still_exists() -> None:
    with _database() as conn:
        ticket = next(row for row in retirement.COHORT if row["has_pending_proposal"])
        conn.exec_driver_sql(
            "UPDATE tickets SET pending_proposal=? WHERE id=?",
            ('{"body":"different proposal"}', ticket["ticket_id"]),
        )
        with pytest.raises(RuntimeError, match="original record hash changed"):
            retirement.retire_planning_workers(conn)




@pytest.mark.parametrize("active_batch", [False, True])
def test_notice_history_retained_and_queued_delivery_not_discarded(active_batch: bool) -> None:
    with _database() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE manager_wakes(id INTEGER,ticket_id TEXT,closed_at INTEGER)"
        )
        conn.exec_driver_sql("CREATE TABLE manager_wake_batches(id INTEGER,status TEXT)")
        conn.exec_driver_sql(
            "CREATE TABLE manager_wake_batch_members(batch_id INTEGER,wake_id INTEGER)"
        )
        ticket_id = retirement.COHORT[0]["ticket_id"]
        conn.exec_driver_sql("INSERT INTO manager_wakes VALUES (1,?,NULL)", (ticket_id,))
        if active_batch:
            conn.exec_driver_sql("INSERT INTO manager_wake_batches VALUES (2,'pending')")
            conn.exec_driver_sql("INSERT INTO manager_wake_batch_members VALUES (2,1)")
            with pytest.raises(RuntimeError, match="delivery ownership"):
                retirement.retire_planning_workers(conn)
            assert conn.exec_driver_sql("SELECT closed_at FROM manager_wakes").scalar() is None
        else:
            retirement.retire_planning_workers(conn)
            assert conn.exec_driver_sql("SELECT closed_at FROM manager_wakes").scalar() is not None
            value = conn.exec_driver_sql(
                "SELECT field_values FROM tickets WHERE id=?", (ticket_id,)
            ).scalar()
            assert isinstance(value, str)
            assert json.loads(json.loads(value)["retirement"])["closed_manager_wake_ids"] == [1]


def _reviewed_source(monkeypatch: pytest.MonkeyPatch) -> sa.Connection:
    conn = _database()
    eligible = retirement.RECONCILIATION["eligible_ids"][0]
    conn.exec_driver_sql(
        "DELETE FROM tickets WHERE worker_type IN ('planning-day','planning-sprint') "
        "AND stage!='done' AND id!=?",
        (eligible,),
    )
    completed = conn.exec_driver_sql("SELECT * FROM tickets WHERE id='completed'").mappings().one()
    reviewed = json.loads(json.dumps(retirement.RECONCILIATION))
    reviewed["completed_preserved"] = [
        {"ticket_id": "completed", "record_sha256": retirement._row_hash(completed)}
    ]
    monkeypatch.setattr(retirement, "RECONCILIATION", reviewed)
    return conn


def test_reviewed_reconciliation_retires_only_exact_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _reviewed_source(monkeypatch) as conn:
        before = conn.exec_driver_sql("SELECT * FROM tickets WHERE id='completed'").one()
        retirement.retire_planning_workers(conn)
        assert [
            tuple(row)
            for row in conn.exec_driver_sql("SELECT id FROM tickets WHERE stage='cancelled'")
        ] == [("t_tnmp9e7r",)]
        assert conn.exec_driver_sql("SELECT * FROM tickets WHERE id='completed'").one() == before
        values = json.loads(
            conn.exec_driver_sql(
                "SELECT field_values FROM tickets WHERE id='t_tnmp9e7r'"
            ).scalar_one()
        )
        assert json.loads(values["retirement"])["reviewed_reconciliation_sha256"] == (
            "bd1f8b4cd402e3d524e35a4ccac952e03afe4f502316c9a26a5074d6f13a7dc7"
        )


@pytest.mark.parametrize(
    "drift", ["excluded", "eligible", "extra", "completed", "missing", "missing_eligible"]
)
def test_reviewed_reconciliation_refuses_source_drift(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    with _reviewed_source(monkeypatch) as conn:
        if drift == "excluded":
            conn.exec_driver_sql(
                "INSERT INTO tickets SELECT 't_d1prmbs8',worker_type,stage,conversation_id,"
                "worker_step_claim,worker_step_claim_revision,pending_proposal,"
                "pending_proposal_revision,field_values FROM tickets WHERE id='t_tnmp9e7r'"
            )
        elif drift == "extra":
            conn.exec_driver_sql(
                "UPDATE tickets SET worker_type='planning-day',stage='needs_review' "
                "WHERE id='ordinary'"
            )
        elif drift == "completed":
            conn.exec_driver_sql(
                "UPDATE tickets SET conversation_id='changed' WHERE id='completed'"
            )
        elif drift == "missing":
            conn.exec_driver_sql("DELETE FROM tickets WHERE id='completed'")
        elif drift == "missing_eligible":
            conn.exec_driver_sql("DELETE FROM tickets WHERE id='t_tnmp9e7r'")
        else:
            conn.exec_driver_sql("UPDATE tickets SET field_values='{}' WHERE id='t_tnmp9e7r'")
        with pytest.raises(RuntimeError):
            retirement.retire_planning_workers(conn)
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM tickets WHERE stage='cancelled'"
            ).scalar_one()
            == 0
        )


def test_reviewed_survivor_refuses_direct_delivery_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _reviewed_source(monkeypatch) as conn:
        conn.exec_driver_sql(
            "CREATE TABLE manager_wakes(id INTEGER,ticket_id TEXT,closed_at INTEGER)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE manager_wake_batches(id INTEGER,status TEXT,"
            "target_kind TEXT,target_id TEXT)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE manager_wake_batch_members(batch_id INTEGER,wake_id INTEGER)"
        )
        conn.exec_driver_sql(
            "INSERT INTO manager_wake_batches VALUES(1,'accepted','ticket','t_tnmp9e7r')"
        )
        with pytest.raises(RuntimeError, match="delivery ownership"):
            retirement.retire_planning_workers(conn)
        assert (
            conn.exec_driver_sql("SELECT stage FROM tickets WHERE id='t_tnmp9e7r'").scalar_one()
            == "needs_brief"
        )


def test_total_planning_loss_with_ordinary_work_refuses_cutover() -> None:
    with _database() as conn:
        conn.exec_driver_sql("DELETE FROM tickets WHERE worker_type LIKE 'planning-%'")
        before = conn.exec_driver_sql("SELECT * FROM tickets").all()
        assert len(before) == 1
        with pytest.raises(RuntimeError, match="cohort changed"):
            retirement.retire_planning_workers(conn)
        assert conn.exec_driver_sql("SELECT * FROM tickets").all() == before
