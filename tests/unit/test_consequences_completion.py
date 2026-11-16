"""Consequences reports wait for review, including after upgrade."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.support.launch_identity import agent_headers
from tests.unit.test_ticket_create_api import create_api as create_api
from tests.unit.test_ticket_workflow_contract import _custom_workflow

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.runtime.logic.worker_step_prompt import compose_worker_step_prompt
from planner.tickets import data
from planner.tickets.contracts import TITLE_MAX_CHARS, StageOwnershipMode
from planner.worker_types.shipped import shipped_worker_types
from planner.worker_types.store import (
    definition_from_json,
    workflow_from_preset,
    workflow_to_json,
)


@pytest.mark.parametrize("protected", [False, True])
def test_final_decision_replay_and_consequences_review(
    create_api: tuple[TestClient, Path],
    protected: bool,
) -> None:
    client, _ = create_api
    profile = client.get("/api/worker-types/coding").json()["profile"]
    workflow = _custom_workflow()
    # The shared field, not a coding-specific Stage id, owns the completion rule.
    workflow["stages"][-2]["id"] = "land_the_result"
    created = client.post(
        "/api/tickets",
        json={
            "title": "Finish the evidence review",
            "worker_type": None,
            "workflow": workflow,
            "employee_backend": profile["default_backend"],
            "employee_launch_model": profile["default_model"],
            "brief": "Compare the evidence.",
            "ceiling": "needs_evidence",
            "locale": "vps",
        },
    )
    assert created.status_code == 200, created.text
    ticket_id = created.json()["id"]
    assert "land_the_result" in created.json()["workflow"]["ceiling_range"]
    worker = agent_headers(client, "worker", ticket_id)
    proposal = client.post(
        f"/api/tickets/{ticket_id}/propose",
        json={"body": "The records agree."},
        headers=worker,
    )
    revision = proposal.json()["pending_proposal"]["revision"]
    if protected:
        approval_path = f"/api/tickets/{ticket_id}/decision-operations/final"
        approval_body = {
            "decision": "approve",
            "field": "evidence",
            "expected_proposal_revision": revision,
        }
    else:
        approval_path = f"/api/tickets/{ticket_id}/accept/evidence"
        approval_body = {
            "expected_proposal_revision": revision,
            "decision_id": "legacy-final",
        }
    approved = client.post(approval_path, json=approval_body)
    assert approved.status_code == 200, approved.text
    if protected:
        assert approved.json()["receipt"]["outcome"] == "applied", approved.text
    else:
        assert approved.json()["stage"] == "land_the_result"
    completed = client.post(
        f"/api/tickets/{ticket_id}/propose",
        json={"body": "Verified the saved result."},
        headers=worker,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["stage"] == "land_the_result"
    pending = completed.json()["pending_proposal"]
    assert pending["field"] == "consequences"
    final = client.post(
        f"/api/tickets/{ticket_id}/accept/consequences",
        json={"expected_proposal_revision": pending["revision"]},
    )
    assert final.status_code == 200, final.text
    assert final.json()["stage"] == "done"
    assert final.json()["field_values"]["consequences"] == "Verified the saved result."
    # Completion changes the current Stage, but never the original command identity.
    repeated = client.post(approval_path, json=approval_body)
    assert repeated.status_code == 200, repeated.text
    if protected:
        assert repeated.json() == approved.json()
    else:
        assert repeated.json()["stage"] == "done"
        assert repeated.json()["field_values"] == final.json()["field_values"]


def test_consequences_report_waits_for_holder_and_acceptance_finishes(
    tmp_db: sqlite3.Connection,
) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Review the result",
        worker_type="coding",
        brief="The job",
        principal=OWNER_PRINCIPAL,
        stated_ceiling="needs_consequences",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    tmp_db.execute("UPDATE tickets SET stage='needs_consequences' WHERE id=?", (ticket.id,))
    parked = data.file_current_proposal(
        tmp_db,
        ticket.id,
        body="The merge is verified.",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=2,
    )
    assert parked.stage == "needs_consequences"
    assert parked.pending_proposal is not None
    completed = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="consequences",
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    assert completed.stage == "done"
    assert completed.pending_proposal is None
    assert completed.field_values["consequences"] == "The merge is verified."


@pytest.mark.parametrize("legacy_ceiling", [None, "none", "done", "needs_consequences"])
def test_legacy_approval_before_consequences_keeps_the_holder_and_review(
    tmp_db: sqlite3.Connection, legacy_ceiling: str | None
) -> None:
    """Old clients omit scope or send their former terminal boundary at this one gate."""
    ticket = data.create_ticket(
        tmp_db,
        title="Legacy client approval",
        worker_type="coding",
        brief="The job",
        principal=OWNER_PRINCIPAL,
        stated_ceiling="needs_implementation",
        stated_holder=CHIEF_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    tmp_db.execute("UPDATE tickets SET stage='needs_implementation' WHERE id=?", (ticket.id,))
    data.file_current_proposal(
        tmp_db,
        ticket.id,
        body="Work done",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=2,
    )
    accepted = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="implementation",
        principal=OWNER_PRINCIPAL,
        now=3,
        next_ceiling=legacy_ceiling,
    )
    assert accepted.stage == "needs_consequences"
    assert accepted.ceiling == "needs_consequences"
    assert accepted.ceiling_holder == CHIEF_PRINCIPAL
    parked = data.file_current_proposal(
        tmp_db,
        ticket.id,
        body="Report",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=4,
    )
    assert parked.stage == "needs_consequences"
    assert parked.pending_proposal is not None


def test_upgrade_preserves_work_and_sends_parked_report_in_actual_prompt(tmp_path: Path) -> None:
    path = str(tmp_path / "old-completion.db")
    preset = next(item for item in shipped_worker_types() if item["worker_type"] == "coding")
    workflow = workflow_from_preset(definition_from_json(json.dumps(preset)))
    workflow = replace(
        workflow,
        guidance=(
            "Keep this. Panels settles that report and finishes the Ticket "
            "without another approval."
        ),
    )
    human_workflow = replace(
        workflow,
        stages=tuple(
            replace(stage, ownership_mode=StageOwnershipMode.user)
            if stage.gating_field == "consequences"
            else stage
            for stage in workflow.stages
        ),
    )
    report = {
        "field": "consequences",
        "body": "A merge was attempted. Check its result.",
        "proposed_by": "worker",
        "created_at": 5,
    }
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, "actual_compaction_boundaries")
        for ticket_id, claim, backlog, stage in (
            ("parked", "none", 0, "needs_consequences"),
            ("paused", "none", 1, "needs_consequences"),
            ("active", "out", 0, "needs_consequences"),
            ("failed", "errored", 0, "needs_consequences"),
            ("human", "none", 0, "needs_consequences"),
            ("finished", "none", 0, "done"),
            ("unreviewed", "none", 0, "needs_consequences"),
        ):
            migration.exec_driver_sql(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,workflow_json,created_at,updated_at,worker_step_claim,"
                "is_backlogged,pending_proposal) VALUES(?,?,NULL,'codex',?,"
                "'needs_consequences','{}',?,1,2,?,?,?)",
                (
                    ticket_id,
                    ticket_id,
                    stage,
                    workflow_to_json(human_workflow if ticket_id == "human" else workflow),
                    claim,
                    backlog,
                    json.dumps(report) if ticket_id == "parked" else None,
                ),
            )
        migration.exec_driver_sql("UPDATE tickets SET ceiling='done' WHERE id='unreviewed'")
    with closing(connect(path)) as conn:
        create_schema(conn)
        unreviewed = data.read_ticket(conn, "unreviewed")
        assert unreviewed.ceiling == "needs_consequences"
        projected = conn.execute(
            "SELECT json_extract(value, '$.ceiling') FROM navigation_records "
            "WHERE kind='ticket' AND id='unreviewed'"
        ).fetchone()
        assert projected is not None and projected[0] == "needs_consequences"
        assert "Keep this." in unreviewed.workflow.guidance
        assert "ceiling holder reviews" in unreviewed.workflow.guidance
        parked = data.read_ticket(conn, "parked")
        assert parked.stage == "needs_consequences"
        assert parked.pending_proposal is None
        assert parked.field_values.get("consequences") is None
        text = compose_worker_step_prompt(parked, worker_type_definition=parked.workflow).model_text
        assert str(report["body"]) in text
        assert "Check the actual result before repeating any side effect" in text
        assert "ceiling holder reviews the Consequences report" in text
        assert data.read_ticket(conn, "paused").is_backlogged
        assert data.read_ticket(conn, "active").worker_step_claim.value == "out"
        assert data.read_ticket(conn, "failed").worker_step_claim.value == "errored"
        human = data.read_ticket(conn, "human")
        assert (
            human.workflow.stage_definition(human.stage).ownership_mode is StageOwnershipMode.user
        )
        human_prompt = compose_worker_step_prompt(
            human, worker_type_definition=human.workflow
        ).model_text
        assert "open the collaborative discussion" in human_prompt
        assert "do not file a proposal until" in human_prompt
        assert "for the ceiling holder to review it" in human_prompt
        finished = conn.execute("SELECT workflow_json FROM tickets WHERE id='finished'").fetchone()[
            0
        ]
        assert finished == workflow_to_json(workflow)
