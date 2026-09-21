"""Packaging contract for the local CLI name, plus worker my-ticket surfacing."""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli import main as cli_main


@pytest.mark.parametrize(
    ("arguments", "path", "key", "row"),
    (
        (
            ("ticket", "list"),
            "/api/tickets",
            "tickets",
            {
                "id": "t_one",
                "title": "One",
                "stage": "needs_success_condition",
                "ticket_status": "empty",
                "priority": "P1",
                "project": None,
                "recap_preview": "Ready.",
            },
        ),
        (
            ("sprint", "list"),
            "/api/sprints",
            "sprints",
            {
                "id": "sp_one",
                "name": "One",
                "date_start": "2026-07-01",
                "date_end": "2026-07-14",
            },
        ),
        (
            ("sprint", "item", "list"),
            "/api/items",
            "items",
            {"id": "si_one", "title": "One", "status": "todo", "priority": "P2"},
        ),
        (
            ("day", "list-tickets"),
            "/api/day/today/tickets",
            "tickets",
            {
                "id": "t_day",
                "title": "Day",
                "stage": "needs_plan",
                "ticket_status": "user",
                "priority": "P0",
                "project": "Panels",
                "recap_preview": "Plan it.",
            },
        ),
        (
            ("project", "list"),
            "/api/projects",
            "projects",
            {"id": "project_one", "name": "One"},
        ),
    ),
)
def test_bounded_list_commands_report_page_facts_in_text_and_json(
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
    path: str,
    key: str,
    row: dict[str, Any],
) -> None:
    requested_paths: list[str] = []
    response = {
        key: [row],
        "page": {
            "match_count": 3,
            "return_count": 1,
            "limit": 1,
            "offset": 1,
            "omitted_before": 1,
            "omitted_after": 1,
            "complete": False,
            "next_offset": 2,
        },
    }
    if arguments[0] == "day":
        response["id"] = "day_2026-07-04"

    def fake_send(method: str, requested_path: str, **kwargs: Any) -> dict[str, Any]:
        assert method == "GET"
        requested_paths.append(requested_path)
        return response

    monkeypatch.setattr(http, "send", fake_send)
    runner = CliRunner()
    text_result = runner.invoke(cli_main.main, [*arguments, "--limit", "1", "--offset", "1"])
    json_result = runner.invoke(
        cli_main.main,
        [*arguments, "--limit", "1", "--offset", "1", "--json"],
    )

    assert text_result.exit_code == 0, text_result.output
    assert "Returned 1 of 3 matches." in text_result.output
    assert "Omitted 1 before and 1 after." in text_result.output
    assert "Use --limit 1 --offset 2 for the next page." in text_result.output
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output)["page"] == response["page"]
    assert requested_paths == [path, path]


