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
                "project": None,
                "recap_preview": "Ready.",
            },
        ),
        (
            ("manager", "list"),
            "/api/managers",
            "managers",
            {"id": "si_one", "title": "One", "settled": False},
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


def test_issued_manager_reply_alias_remains_executable_without_teaching_old_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets: list[dict[str, str]] = []

    def sent(method: str, path: str, **kwargs: Any) -> dict[str, str]:
        assert (method, path) == ("POST", "/api/messages/send")
        targets.append(kwargs["json_body"]["target"])
        return {"fate": "accepted"}

    monkeypatch.setattr(http, "send", sent)
    runner = CliRunner()
    for option in ("--manager", "--sprint-item"):
        result = runner.invoke(
            cli_main.main, ["send-message", option, "si_same", "--message", "Reply."]
        )
        assert result.exit_code == 0, result.output
    assert targets == [{"kind": "manager", "id": "si_same"}] * 2
    help_text = runner.invoke(cli_main.main, ["send-message", "--help"]).output
    assert "--manager" in help_text and "--sprint-item" not in help_text
    refused = runner.invoke(
        cli_main.main,
        ["send-message", "--manager", "si_one", "--sprint-item", "si_other", "--message", "Reply."],
    )
    assert refused.exit_code != 0
    assert len(targets) == 2


def test_schedule_issued_assignment_field_normalizes_before_canonical_choice_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[dict[str, Any]] = []

    def sent(method: str, path: str, **kwargs: Any) -> dict[str, str]:
        assert (method, path) == ("PATCH", "/api/schedules/schedule_example")
        writes.append(kwargs["json_body"])
        return {"id": "schedule_example"}

    monkeypatch.setattr(http, "send", sent)
    result = CliRunner().invoke(
        cli_main.main,
        ["schedule", "set", "schedule_example", "sprint-item", "--value", "si_example"],
    )
    assert result.exit_code == 0, result.output
    assert writes == [{"manager": {"kind": "manager", "id": "si_example"}}]


@pytest.mark.parametrize("prefix", [("manager",), ("orchestrator",), ("sprint", "item")])
def test_manager_state_survives_cli_reads_and_filters(
    monkeypatch: pytest.MonkeyPatch, prefix: tuple[str, ...]
) -> None:
    calls: list[dict[str, Any]] = []
    record: dict[str, Any] = {"id": "si_one", "title": "One", "goal": "Goal", "recap": "Recap"}

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["params"])
        if kwargs["params"]["detail"] == "full":
            return record
        return {
            "managers": [record],
            "page": {
                "match_count": 1,
                "return_count": 1,
                "limit": 50,
                "offset": 0,
                "omitted_before": 0,
                "omitted_after": 0,
                "complete": True,
                "next_offset": None,
            },
        }

    monkeypatch.setattr(http, "send", send)
    runner = CliRunner()
    for state in (False, True):
        record["settled"] = state
        for selected in ([], ["goal,recap"]):
            for output in ([], ["--json"]):
                result = runner.invoke(
                    cli_main.main, [*prefix, "show", "si_one", *selected, *output]
                )
                assert result.exit_code == 0, result.output
                if output:
                    assert json.loads(result.output)["header"]["settled"] is state
                else:
                    assert "settled" in result.output
                    assert str(state).lower() in result.output.lower()
        result = runner.invoke(cli_main.main, [*prefix, "list"])
        assert result.exit_code == 0, result.output
        assert f"[{'settled' if state else 'active'}]" in result.output
    for flag, expected in (([], None), (["--active"], False), (["--settled"], True)):
        result = runner.invoke(cli_main.main, [*prefix, "list", *flag, "--json"])
        assert result.exit_code == 0, result.output
        assert calls[-1].get("settled") is expected
        assert ("settled" in calls[-1]) is (expected is not None)
    for flags in (["--active", "--settled"], ["--settled", "--active"]):
        count = len(calls)
        result = runner.invoke(cli_main.main, [*prefix, "list", *flags, "--json"])
        assert result.exit_code != 0
        assert "use only one" in result.output
        assert len(calls) == count


def test_manager_help_explains_assignment_state() -> None:
    for args in (
        ("manager",),
        ("manager", "list"),
        ("manager", "show"),
        ("orchestrator",),
        ("orchestrator", "list"),
        ("orchestrator", "show"),
        ("sprint", "item"),
        ("sprint", "item", "list"),
        ("sprint", "item", "show"),
        ("ticket", "create"),
        ("ticket", "assign"),
    ):
        result = CliRunner().invoke(cli_main.main, [*args, "--help"])
        assert result.exit_code == 0
        text = " ".join(result.output.split()).lower()
        assert "finished destinations" in text
        assert "active manager for new work" in text
        assert "settled" in text and "absent settled field" in text
