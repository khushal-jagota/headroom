"""Packaging contract for the local CLI name, plus worker my-ticket surfacing."""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli import main as cli_main


def test_worker_my_ticket_requests_worker_self_for_explicit_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_paths: list[str] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requested_paths.append(path)
        return {
            "id": "t_correct",
            "worker_type": "exploration",
            "stage": "needs_understanding",
            "ticket_status": "agent",
            "priority": "P1",
            "title": "Correct ticket",
            "worker": "panels-worker-exploration",
            "fields": {"understanding": {"value": None, "user_note": None, "proposal": None}},
        }

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["worker", "my-ticket"],
        env={"PLAN_TICKET_ID": "t_correct"},
    )

    assert result.exit_code == 0, result.output
    assert requested_paths == ["/api/tickets/t_correct/worker-self"]
    assert "id: t_correct" in result.output
    assert "stage: needs_understanding" in result.output


def test_worker_request_user_help_is_a_no_payload_worker_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"id": "t_help"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["worker", "request-user-help"],
        env={"PLAN_TICKET_ID": "t_help"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [("POST", "/api/tickets/t_help/request-user-help", {"as_json": False})]
    assert "user help requested on t_help" in result.output


def test_ticket_list_passes_repeatable_filters_and_page_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {
            "tickets": [],
            "page": {
                "match_count": 0,
                "return_count": 0,
                "limit": 7,
                "offset": 14,
                "omitted_before": 0,
                "omitted_after": 0,
                "complete": True,
                "next_offset": None,
            },
        }

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        [
            "ticket",
            "list",
            "--stage",
            "needs_user",
            "--stage",
            "user",
            "--exclude-stage",
            "done",
            "--ticket-status",
            "needs_user",
            "--exclude-ticket-status",
            "errored",
            "--include-terminal",
            "--search",
            "Needle",
            "--limit",
            "7",
            "--offset",
            "14",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0][0:2] == ("GET", "/api/ticket-summaries")
    assert calls[0][2]["params"] == {
        "stage": ["needs_user", "user"],
        "exclude_stage": ["done"],
        "ticket_status": ["needs_user"],
        "exclude_ticket_status": ["errored"],
        "include_terminal": True,
        "search": "Needle",
        "limit": 7,
        "offset": 14,
    }
    assert "No matches." in result.output
    assert "Complete: yes." in result.output


@pytest.mark.parametrize(
    ("arguments", "path", "key", "row"),
    (
        (
            ("ticket", "list"),
            "/api/ticket-summaries",
            "tickets",
            {
                "id": "t_one",
                "title": "One",
                "stage": "needs_success",
                "ticket_status": "empty",
                "priority": "P1",
                "project": None,
                "recap_preview": "Ready.",
            },
        ),
        (
            ("sprint", "list"),
            "/api/sprint-summaries",
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
            "/api/sprint-item-summaries",
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
            "/api/project-summaries",
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
