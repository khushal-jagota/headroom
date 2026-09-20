"""Packaging contract for the local CLI name, plus worker my-ticket surfacing."""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner
from tests.support.probe import build_shipped_registry

from planner.cli import http
from planner.cli import main as cli_main
from planner.cli.record_projection import project_record

SHIPPED_REGISTRY = build_shipped_registry()

def test_sprint_item_cli_has_no_direct_block_commands() -> None:
    result = CliRunner().invoke(cli_main.main, ["sprint", "item", "--help"])

    assert result.exit_code == 0, result.output
    commands = {
        line.split()[0]
        for line in result.output.splitlines()
        if line.startswith("  ") and line.strip()
    }
    assert "block" not in commands
    assert "unblock" not in commands


def test_ticket_block_cli_uses_explicit_ticket_block_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"ok": True}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["ticket", "block", "t_blocked", "--by", "t_blocker"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "PUT",
            "/api/collections/blockers/t_blocked/t_blocker",
            {"as_json": False, "request_actor": "ordinary"},
        )
    ]


def test_ticket_complete_uses_the_gate_completion_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"id": "t_personal"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["ticket", "complete", "t_personal", "outcome", "--value", "Done"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "POST",
            "/api/tickets/t_personal/complete/outcome",
            {
                "as_json": False,
                "json_body": {"body": "Done"},
                "request_actor": "ordinary",
            },
        )
    ]


def test_worker_my_ticket_requests_worker_self_for_explicit_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_paths: list[str] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requested_paths.append(path)
        if path == "/api/worker-types":
            return {
                "worker_types": [SHIPPED_REGISTRY.manifest("exploration")]
            }
        return {
            "id": "t_correct",
            "worker_type": "exploration",
            "stage": "needs_understanding",
            "ticket_status": "agent",
            "priority": "P1",
            "title": "Correct ticket",
            "worker": "panels-worker-exploration",
            "field_values": {},
            "pending_proposal": None,
        }

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["worker", "my-ticket"],
        env={"PLAN_TICKET_ID": "t_correct"},
    )

    assert result.exit_code == 0, result.output
    assert requested_paths == [
        "/api/tickets/t_correct/worker-self",
        "/api/worker-types",
    ]
    assert "id: t_correct" in result.output
    assert "stage: needs_understanding" in result.output


def test_worker_request_help_sends_stdin_to_the_default_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"fate": "recorded"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["worker", "request-help"],
        input="Please resolve the product choice.\n",
        env={"PLAN_TICKET_ID": "t_help"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "POST",
            "/api/tickets/t_help/request-help",
            {"as_json": False, "json_body": {"message": "Please resolve the product choice.\n"}},
        )
    ]
    assert "help message recorded" in result.output


def test_ticket_approve_sends_the_explicit_next_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/api/tickets/t_child":
            return {
                "id": "t_child",
                "worker_type": "coding",
                "stage": "needs_success_condition",
                "pending_proposal": {"field": "success_condition", "body": "Ready"},
            }
        if path == "/api/worker-types":
            return {"worker_types": [SHIPPED_REGISTRY.manifest("coding")]}
        return {"id": "t_child"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        [
            "ticket",
            "approve",
            "t_child",
            "--ceiling",
            "needs_what_changes",
            "--holder-kind",
            "sprint_item",
            "--holder-id",
            "si_parent",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[-1][0:2] == ("POST", "/api/tickets/t_child/accept/success_condition")
    assert calls[-1][2]["json_body"] == {
        "next_ceiling": "needs_what_changes",
        "next_holder": {"kind": "sprint_item", "id": "si_parent"},
    }


def test_supervisor_approve_defaults_the_next_holder_to_its_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"id": "t_child"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        [
            "sprint",
            "item",
            "supervisor",
            "approve",
            "si_parent",
            "t_child",
            "--ceiling",
            "needs_what_changes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "POST",
            "/api/items/si_parent/supervisor/tickets/t_child/approve",
            {
                "as_json": False,
                "json_body": {
                    "next_ceiling": "needs_what_changes",
                    "next_holder": {"kind": "sprint_item", "id": "si_parent"},
                },
            },
        )
    ]


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
            "needs_success_condition",
            "--stage",
            "needs_what_changes",
            "--exclude-stage",
            "done",
            "--ticket-status",
            "agent",
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
        "stage": ["needs_success_condition", "needs_what_changes"],
        "exclude_stage": ["done"],
        "ticket_status": ["agent"],
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
                "stage": "needs_success_condition",
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


@pytest.mark.parametrize(
    ("options", "key"), [([], "guidance"), (["--append"], "guidance_append")]
)
def test_worker_note_writes_stdin_once_without_a_field_or_type_read(
    monkeypatch: pytest.MonkeyPatch, options: list[str], key: str
) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_send(verb: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((verb, path, kwargs.get("json_body")))
        return {"id": "t_direct", "guidance": kwargs["json_body"][key]}

    monkeypatch.setattr(http, "send", fake_send)
    body = "  Exact stdin\n\n"
    result = CliRunner().invoke(cli_main.main, ["worker", "note", "t_direct", *options], input=body)
    assert result.exit_code == 0, result.output
    assert calls == [("PATCH", "/api/tickets/t_direct", {key: body})]


def test_ticket_parts_expose_guidance_and_recap_without_expanding_default_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert (method, path) == ("GET", "/api/worker-types")
        return {"worker_types": [SHIPPED_REGISTRY.manifest("coding")]}

    monkeypatch.setattr(http, "send", fake_send)
    data = {
        "id": "t_parts",
        "worker_type": "coding",
        "field_values": {"brief": "request"},
        "pending_proposal": None,
        "recap": "orientation",
        "guidance": "  exact guidance\n",
    }
    header, parts = cli_main._ticket_record(data)
    manifest = project_record(header, parts, None)
    assert list(manifest["manifest"]) == [
        "brief",
        "success_condition",
        "what_changes",
        "plan",
        "implementation",
        "consequences",
        "proposal",
        "recap",
        "guidance",
    ]
    assert "parts" not in manifest
    expanded = project_record(header, parts, ("guidance", "recap"))
    assert expanded["parts"] == {
        "guidance": {"value": data["guidance"], "proposal": None},
        "recap": {"value": "orientation", "proposal": None},
    }
