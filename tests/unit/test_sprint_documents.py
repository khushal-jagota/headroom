"""Sprint prose survives migration and uses one write/read path per document."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from click.testing import CliRunner
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from planner.cli import http
from planner.cli.main import main
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import MIGRATIONS_DIRECTORY, connect, create_schema
from planner.core.server import create_app


def test_sprint_migration_preserves_all_prose_and_references(tmp_path: Path) -> None:
    path = tmp_path / "sprint-documents.db"
    engine = create_engine(
        URL.create(drivername="sqlite", database=str(path)),
        connect_args={"isolation_level": None},
    )
    try:
        with engine.begin() as connection:
            config = Config()
            config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
            config.attributes["connection"] = connection
            command.upgrade(config, "planning_day_direction")
    finally:
        engine.dispose()

    conn = connect(str(path))
    try:
        old_prose = {
            "limiting_factor": "  Constraint\n",
            "supports": "Support 🌱",
            "premortem": "Risk",
            "mid_where_we_stand": "Progress",
            "mid_whats_changed": " ",
            "mid_what_to_adjust": "Adjustment",
            "outcomes": "Outcome",
            "solo_reflection": "Reflection",
            "joint_discussion": "Discussion",
            "updates_to_thinking": "Thought",
            "carry_forward": "\tCarry\n",
        }
        for sprint_id in ("sp_full", "sp_empty"):
            conn.execute(
                "INSERT INTO sprints (id, name, date_start, date_end, primary_bet, "
                "created_at, updated_at) VALUES (?, 'History', '2026-08-03', '2026-08-09', "
                "'  Summary\n', 12, 34)",
                (sprint_id,),
            )
        assignments = ", ".join(f"{field} = ?" for field in old_prose)
        conn.execute(
            f"UPDATE sprints SET {assignments} WHERE id = 'sp_full'", tuple(old_prose.values())
        )
        conn.execute(
            "INSERT INTO sprint_items (id, title, project_id, sprint_id, created_at, updated_at) "
            "VALUES ('si_kept', 'Item', 'project_personal', 'sp_full', 12, 34)"
        )
        old_fields = {
            "kickoff": {
                "value": "Saved intake",
                "proposal": {
                    "body": "Current kickoff",
                    "proposed_by": "human",
                    "created_at": 56,
                },
            },
            "success": {
                "value": "Saved result",
                "proposal": {
                    "body": "Unapproved success",
                    "proposed_by": "agent",
                    "created_at": 78,
                },
            },
        }
        conn.execute(
            "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, fields, "
            "project_id, sprint_id, sprint_item_id, ticket_status, created_at, updated_at) "
            "VALUES ('t_kept', 'Ticket', 'coding', 'hermes', 'needs_kickoff', ?, "
            "'project_personal', 'sp_full', 'si_kept', 'awaiting_approval', 12, 34)",
            (json.dumps(old_fields),),
        )
        item_before = dict(
            conn.execute("SELECT * FROM sprint_items WHERE id = 'si_kept'").fetchone()
        )
        ticket_before = dict(conn.execute("SELECT * FROM tickets WHERE id = 't_kept'").fetchone())
        create_schema(conn)
        full = dict(conn.execute("SELECT * FROM sprints WHERE id = 'sp_full'").fetchone())
        assert full == {
            "id": "sp_full",
            "name": "History",
            "date_start": "2026-08-03",
            "date_end": "2026-08-09",
            "primary_bet": "  Summary\n",
            "created_at": 12,
            "updated_at": 34,
            "kickoff": (
                "## Limiting factor\n\n  Constraint\n\n\n"
                "## Supports\n\nSupport 🌱\n\n## Premortem\n\nRisk"
            ),
            "checkpoint": (
                "## Where we stand\n\nProgress\n\n## What's changed\n\n \n\n"
                "## What to adjust\n\nAdjustment"
            ),
            "review": (
                "## Outcomes\n\nOutcome\n\n## Solo reflection\n\nReflection\n\n"
                "## Joint discussion\n\nDiscussion\n\n## Updates to thinking\n\nThought\n\n"
                "## Carry forward\n\n\tCarry\n"
            ),
        }
        empty = conn.execute(
            "SELECT kickoff, checkpoint, review FROM sprints WHERE id = 'sp_empty'"
        ).fetchone()
        assert tuple(empty) == ("", "", "")
        assert not set(old_prose) & {r["name"] for r in conn.execute("PRAGMA table_info(sprints)")}
        assert dict(conn.execute("SELECT * FROM sprint_items WHERE id = 'si_kept'").fetchone()) == {
            key: value for key, value in item_before.items() if key != "sprint_id"
        }
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT sprint_id, outcome_id FROM sprint_outcomes ORDER BY sprint_id, outcome_id"
            )
        ] == [("sp_full", "si_kept")]
        ticket_after = dict(conn.execute("SELECT * FROM tickets WHERE id = 't_kept'").fetchone())
        assert ticket_after.pop("guidance") == ""
        assert json.loads(ticket_after.pop("field_values")) == {
            "brief": "Saved intake",
            "success_condition": "Saved result",
        }
        assert json.loads(ticket_after.pop("pending_proposal")) == {
            "field": "brief",
            "body": "Current kickoff",
            "proposed_by": "human",
            "created_at": 56,
        }
        # The unapproved draft this ladder once preserved does not reach head: a later
        # revision drops the column, because a proposal is approved or rejected and
        # neither leaves a withdrawn draft to keep.
        assert "archived_field_content" not in ticket_after
        assert ticket_before.pop("fields") == json.dumps(old_fields)
        ticket_before.pop("stage_ownership_overrides")
        ticket_before.pop("default_stage_ownership_mode")
        ticket_before.pop("alias")
        ticket_before.pop("backend_error")
        ticket_before.pop("at_cap")
        # The stored status and its two companions became the claim, which carries their
        # values. A Ticket parked at `awaiting_approval` had no claim out.
        assert ticket_before.pop("ticket_status") == "awaiting_approval"
        assert ticket_after.pop("worker_step_claim") == "none"
        assert ticket_after.pop("worker_step_claim_changed_at") == ticket_before.pop(
            "ticket_status_changed_at"
        )
        assert ticket_after.pop("worker_step_claim_revision") == ticket_before.pop(
            "ticket_status_revision"
        )
        assert ticket_after.pop("ceiling_holder") == '{"id":"owner","kind":"owner"}'
        # A later revision moves the ids to the settled labels, so the Stage this row was
        # written with reaches head under its new name, in every column that holds one.
        assert ticket_after.pop("stage") == "needs_brief"
        assert ticket_after.pop("ceiling") == "needs_brief"
        assert ticket_before.pop("stage") == "needs_kickoff"
        assert ticket_before.pop("ceiling") == "needs_kickoff"
        assert ticket_after == ticket_before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_sprint_documents_round_trip_and_reject_stale_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sprints.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(path)})
    with TestClient(create_app(config, build_clock(config), lambda: connect(str(path)))) as client:
        body = {
            "name": "Sprint",
            "date_start": "2026-08-03",
            "date_end": "2026-08-09",
            "primary_bet": "One useful outcome",
            "kickoff": "## Intent\n\nWork well.",
            "checkpoint": "A revised plan.",
            "review": "A learned lesson.",
        }
        created = client.post("/api/sprints", json=body)
        assert created.status_code == 200, created.text
        sprint_id = created.json()["id"]
        route = f"/api/sprints/{sprint_id}"
        read_route = f"/api/sprints?detail=full&id={sprint_id}"
        assert {field: created.json()[field] for field in body} == body
        edited = client.patch(route, json={"kickoff": "New intent", "checkpoint": ""})
        assert edited.status_code == 200
        before_rejection = client.get(read_route).json()
        assert before_rejection["kickoff"] == "New intent"
        assert before_rejection["checkpoint"] == ""
        assert before_rejection["primary_bet"] == body["primary_bet"]
        for invalid in ({"kickoff": "Lost", "review": []}, {"kickoff": "Lost", "outcomes": "Old"}):
            assert client.patch(route, json=invalid).status_code == 400
            assert client.get(read_route).json() == before_rejection
        for retired in (
            "limiting_factor",
            "supports",
            "premortem",
            "mid_where_we_stand",
            "mid_whats_changed",
            "mid_what_to_adjust",
            "outcomes",
            "solo_reflection",
            "joint_discussion",
            "updates_to_thinking",
            "carry_forward",
            "invented_field",
        ):
            stale = client.post("/api/sprints", json={**body, retired: "Do not drop this"})
            assert stale.status_code == 400
            assert stale.json()["error"]["detail"]["field"] == retired
        assert len(client.get("/api/sprints?detail=full").json()["sprints"]) == 1

        def send(method: str, url: str, **kwargs: Any) -> Any:
            response = client.request(
                method, url, json=kwargs.get("json_body"), params=kwargs.get("params")
            )
            assert response.status_code == 200, response.text
            return response.json()

        monkeypatch.setattr(http, "send", send)
        runner = CliRunner()
        review_file = tmp_path / "review.md"
        review_file.write_text("## Result\n\nA useful outcome.\n")
        result = runner.invoke(
            main, ["sprint", "set", sprint_id, "review", "--body-file", str(review_file), "--json"]
        )
        assert result.exit_code == 0, result.output
        shown = runner.invoke(main, ["sprint", "show", sprint_id, "primary_bet,review", "--json"])
        assert shown.exit_code == 0, shown.output
        parts = json.loads(shown.output)["parts"]
        assert parts["primary_bet"]["value"] == body["primary_bet"]
        assert parts["review"]["value"] == review_file.read_text()
        stale_command = runner.invoke(
            main, ["sprint", "set", sprint_id, "outcomes", "--value", "Old"]
        )
        assert stale_command.exit_code != 0
        assert client.get(read_route).json()["review"] == review_file.read_text()
