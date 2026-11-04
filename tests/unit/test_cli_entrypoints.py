"""Packaging contract for the local CLI name, plus worker my-ticket surfacing."""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli import main as cli_main


def test_explicit_ticket_parts_read_only_the_named_retained_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    ticket_id = "t_retained"
    body_path = f"/api/tickets/{ticket_id}/workspace/fields/retired%20field"
    record: dict[str, Any] = {
        "id": ticket_id,
        "worker_type": "coding",
        "field_values": {"brief": "Current brief"},
    }

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert method == "GET"
        calls.append(path)
        if path == "/api/worker-types":
            return {"worker_types": [{"worker_type": "coding", "fields": [{"id": "brief"}]}]}
        if path in ("/api/tickets", f"/api/tickets/{ticket_id}/worker-self"):
            return record
        if path == body_path:
            return {"field": "retired field", "value": "Retained body", "revision": "7"}
        assert path.endswith("/workspace/fields/never_stored")
        http.fail_validation("Ticket field not found", kwargs["as_json"])

    monkeypatch.setattr(http, "send", send)
    monkeypatch.setenv("PLAN_TICKET_ID", ticket_id)
    runner = CliRunner()
    for command in (("ticket", "show", ticket_id), ("worker", "my-ticket")):
        calls.clear()
        manifest = runner.invoke(cli_main.main, [*command, "--json"])
        assert manifest.exit_code == 0, manifest.output
        assert "retired field" not in json.loads(manifest.output)["manifest"]
        assert body_path not in calls

        selected = runner.invoke(cli_main.main, [*command, "retired field,brief", "--json"])
        assert selected.exit_code == 0, selected.output
        parts = json.loads(selected.output)["parts"]
        assert parts["retired field"]["value"] == "Retained body"
        assert parts["brief"]["value"] == "Current brief"
        assert calls.count(body_path) == 1

        calls.clear()
        rejected = runner.invoke(cli_main.main, [*command, "rejection"])
        assert rejected.exit_code != 0
        assert "Revision feedback arrives in the Worker prompt" in rejected.output
        assert not any("/workspace/fields/" in path for path in calls)

        unknown = runner.invoke(cli_main.main, [*command, "never_stored", "--json"])
        assert unknown.exit_code != 0
        assert "Ticket field not found" in unknown.output


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
                "stage": "needs_what_changes",
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


@pytest.mark.parametrize(
    "arguments",
    (
        ("project", "create", "--name", "Legacy", "--requires-macos"),
        ("project", "set", "project_panels", "requires-macos", "--value", "true"),
    ),
)
def test_project_cli_rejects_removed_macos_requirement(arguments: tuple[str, ...]) -> None:
    result = CliRunner().invoke(cli_main.main, arguments)
    assert result.exit_code != 0
    assert "requires-macos" in result.output


def test_removed_ticket_syntax_never_sends_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removed parser paths cannot silently retain their old mutation behavior."""
    def unexpected_send(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("removed syntax sent a request")

    monkeypatch.setattr(http, "send", unexpected_send)
    runner = CliRunner()
    for command in ("set", "set-value", "place", "approve", "reject", "copy"):
        result = runner.invoke(cli_main.main, ["ticket", command, "t_old"])
        assert result.exit_code != 0
        assert "is not a command" in result.output
    for option in (
        "--title", "--worker-type", "--employee-backend", "--employee-launch-model",
        "--deadline", "--project", "--project-id", "--manager", "--blocked-by",
        "--kickoff-note", "--kickoff-note-file", "--ceiling", "--holder",
    ):
        result = runner.invoke(cli_main.main, ["ticket", "create", option, "old"])
        assert result.exit_code != 0
        assert "is not an option" in result.output
    for command, option in (("delete", "--yes"), ("backlog", "--expected-record-sha256"),
                            ("resume", "--expected-record-sha256")):
        result = runner.invoke(cli_main.main, ["ticket", command, "t_old", option])
        assert result.exit_code != 0
        assert "is not an option" in result.output


def test_ticket_delete_force_and_deferral_need_no_confirmations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI forwards force only when requested and sends no deferral fingerprint."""
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {}

    monkeypatch.setattr(http, "send", send)
    runner = CliRunner()
    for arguments in (("delete", "t_old"), ("delete", "t_old", "--force"),
                      ("backlog", "t_old"), ("resume", "t_old")):
        result = runner.invoke(cli_main.main, ["ticket", *arguments])
        assert result.exit_code == 0, result.output
    assert calls[0][0:2] == ("DELETE", "/api/tickets/t_old")
    assert calls[0][2]["params"] is None
    assert calls[1][2]["params"] == {"force": True}
    assert [(method, path, kwargs["json_body"]) for method, path, kwargs in calls[2:]] == [
        ("POST", "/api/tickets/t_old/backlog", {}),
        ("POST", "/api/tickets/t_old/resume", {}),
    ]
