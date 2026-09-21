"""Sprint prose uses one write and read path per document."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.cli import http
from planner.cli.main import main
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


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
