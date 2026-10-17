"""Historical Sprint prose remains readable while calendar writes are retired."""

from __future__ import annotations

import json
from contextlib import closing
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
from planner.sprints import data


def test_historical_sprint_documents_preserve_content_and_refuse_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sprints.db"
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(path)})
    clock = build_clock(config)
    body = {
        "name": "Sprint",
        "date_start": "2026-08-03",
        "date_end": "2026-08-09",
        "primary_bet": "One useful outcome",
        "kickoff": "## Intent\n\nWork well.",
        "checkpoint": "A revised plan.",
        "review": "A learned lesson.",
    }
    with closing(connect(str(path))) as conn:
        create_schema(conn)
        sprint = data.create_sprint(
            conn,
            clock=clock,
            name=body["name"],
            date_start=body["date_start"],
            date_end=body["date_end"],
            primary_bet=body["primary_bet"],
            kickoff=body["kickoff"],
            checkpoint=body["checkpoint"],
            review=body["review"],
        )
    with TestClient(create_app(config, clock, lambda: connect(str(path)))) as client:
        route = f"/api/sprints/{sprint.id}"
        read_route = f"/api/sprints?detail=full&id={sprint.id}"
        original = client.get(read_route).json()
        assert {field: original[field] for field in body} == body
        for headers in (
            {},
            {"X-Plan-Actor": "chief"},
            {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "historical"},
        ):
            assert client.post("/api/sprints", json=body, headers=headers).status_code == 405
            assert (
                client.patch(route, json={"review": "Replacement"}, headers=headers).status_code
                == 404
            )
            assert client.delete(route, headers=headers).status_code == 404
        assert client.get(read_route).json() == original
        assert len(client.get("/api/sprints?detail=full").json()["sprints"]) == 1
        page = client.get("/api/sprints?detail=summary&limit=1&offset=0").json()
        assert [row["id"] for row in page["sprints"]] == [sprint.id]
        assert client.get("/api/sprints?detail=summary&limit=1&offset=1").json()["sprints"] == []

        def send(method: str, url: str, **kwargs: Any) -> Any:
            response = client.request(
                method, url, json=kwargs.get("json_body"), params=kwargs.get("params")
            )
            assert response.status_code == 200, response.text
            return response.json()

        monkeypatch.setattr(http, "send", send)
        runner = CliRunner()
        shown = runner.invoke(main, ["sprint", "show", sprint.id, "primary_bet,review", "--json"])
        assert shown.exit_code == 0, shown.output
        parts = json.loads(shown.output)["parts"]
        assert parts["primary_bet"]["value"] == body["primary_bet"]
        assert parts["review"]["value"] == body["review"]
        retired = runner.invoke(
            main, ["sprint", "set", sprint.id, "review", "--value", "Replacement"]
        )
        assert retired.exit_code != 0
        assert client.get(read_route).json() == original
