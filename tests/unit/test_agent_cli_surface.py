"""The ordinary agent CLI stays small while legacy routes remain callable."""

from __future__ import annotations

import json
import sys
from io import StringIO
from typing import Any

import click
import httpx
import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import _read_skill_patch, main


def _visible_leaves(command: click.Command, path: tuple[str, ...]) -> list[tuple[str, ...]]:
    if not isinstance(command, click.Group):
        return [path]
    leaves: list[tuple[str, ...]] = []
    for name, child in command.commands.items():
        if not child.hidden:
            leaves.extend(_visible_leaves(child, (*path, name)))
    return leaves


def test_ordinary_help_exposes_orchestrators_and_retires_calendar_writes() -> None:
    visible_roots = [name for name, command in main.commands.items() if not command.hidden]
    leaves = _visible_leaves(main, ("panels",))

    assert visible_roots == [
        "send-message",
        "project",
        "day",
        "ticket",
        "sprint",
        "orchestrator",
    ]
    assert ("panels", "orchestrator", "settle") in leaves
    assert ("panels", "ticket", "assign") in leaves
    assert ("panels", "day", "set") not in leaves
    assert ("panels", "sprint", "create") not in leaves
    assert ("panels", "ticket", "edit") in leaves
    assert ("panels", "ticket", "proposal") in leaves
    assert ("panels", "worker", "propose") not in leaves
    assert ("panels", "sprint", "outcome", "add") not in leaves


def test_administrative_and_legacy_paths_are_hidden_but_registered() -> None:
    for root in (
        "environment",
        "maintenance",
        "feedback",
        "schedule",
        "worker-type",
        "skill",
        "worker",
        "model-corrections",
    ):
        assert main.commands[root].hidden is True

    ticket = main.commands["ticket"]
    assert isinstance(ticket, click.Group)
    for name in ("set", "set-value", "place", "approve", "reject", "copy"):
        assert ticket.commands[name].hidden is True

    sprint = main.commands["sprint"]
    assert isinstance(sprint, click.Group)
    assert sprint.commands["outcome"].hidden is True


def _ticket_record(ticket_id: str) -> dict[str, Any]:
    return {
        "id": ticket_id,
        "title": "Selected parts",
        "worker_type": "coding",
        "worker": "panels-worker-coding",
        "stage": "needs_success_condition",
        "ticket_status": "agent",
        "ceiling": "needs_success_condition",
        "field_values": {"brief": "The brief."},
        "pending_proposal": None,
        "recap": "",
    }


def test_ticket_show_reads_selected_parts_from_current_worker_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/api/worker-types":
            return {"worker_types": [{"worker_type": "coding", "fields": [{"id": "brief"}]}]}
        return _ticket_record("t_current")

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        main,
        ["ticket", "show", "brief", "--json"],
        env={"PLAN_TICKET_ID": "t_current"},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["parts"] == {
        "brief": {"value": "The brief.", "proposal": None},
    }
    assert calls[0][1] == "/api/tickets/t_current/worker-self"


def test_ticket_show_reads_selected_parts_from_named_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/api/worker-types":
            return {"worker_types": [{"worker_type": "coding", "fields": [{"id": "brief"}]}]}
        return _ticket_record("t_named")

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        main,
        ["ticket", "show", "t_named", "brief", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert list(json.loads(result.stdout)["parts"]) == ["brief"]
    assert calls[0] == (
        "GET",
        "/api/tickets",
        {"as_json": True, "params": {"detail": "full", "id": "t_named"}},
    )


def test_structured_create_forwards_the_existing_create_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs["json_body"]))
        return {"id": "t_new", "stage": "needs_success_condition", "supervision_receipt": ""}

    monkeypatch.setattr(http, "send", fake_send)
    body = {
        "title": "One Ticket",
        "worker_type": "coding",
        "project_id": "project_panels",
        "orchestrator": {"kind": "chief", "id": "chief"},
        "blocked_by_ticket_ids": ["t_blocker"],
    }
    result = CliRunner().invoke(
        main,
        ["ticket", "create", "--input-json", "-", "--json"],
        input=json.dumps(body),
    )

    assert result.exit_code == 0, result.output
    assert calls == [("POST", "/api/tickets", body)]


def test_structured_create_rejects_legacy_option_mix() -> None:
    result = CliRunner().invoke(
        main,
        ["ticket", "create", "--input-json", "-", "--title", "Mixed", "--json"],
        input='{"title":"Structured","worker_type":"coding"}',
    )

    assert result.exit_code != 0
    error = json.loads(result.stderr)
    assert "cannot be combined" in error["error"]["message"]


def test_batch_edit_forwards_one_existing_patch_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs["json_body"]))
        return {"id": "t_one"}

    monkeypatch.setattr(http, "send", fake_send)
    body = {
        "recap": "Ready.",
        "field_values": {"success_condition": "Done means done."},
    }
    result = CliRunner().invoke(
        main,
        ["ticket", "edit", "t_one", "--input-json", "-", "--json"],
        input=json.dumps(body),
    )

    assert result.exit_code == 0, result.output
    assert calls == [("PATCH", "/api/tickets/t_one", body)]


@pytest.mark.parametrize(
    ("arguments", "path", "body"),
    (
        (("t_one", "submit"), "/api/tickets/t_one/propose", {"body": "Proposal."}),
        (("t_one", "revise"), "/api/tickets/t_one/reject", {"message": "Narrow it."}),
    ),
)
def test_proposal_actions_reuse_existing_writers(
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, str],
    path: str,
    body: dict[str, str],
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, requested_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, requested_path, kwargs["json_body"]))
        return {"id": "t_one"}

    monkeypatch.setattr(http, "send", fake_send)
    stdin = "Proposal." if arguments[1] == "submit" else "Narrow it."
    result = CliRunner().invoke(
        main,
        ["ticket", "proposal", *arguments, "--json"],
        input=stdin,
    )

    assert result.exit_code == 0, result.output
    assert calls == [("POST", path, body)]


def test_proposal_action_uses_worker_ticket_when_id_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert method == "POST"
        assert kwargs["json_body"] == {"body": "Proposal."}
        calls.append(path)
        return {"id": "t_current"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        main,
        ["ticket", "proposal", "submit", "--json"],
        input="Proposal.",
        env={"PLAN_TICKET_ID": "t_current"},
    )

    assert result.exit_code == 0, result.output
    assert calls == ["/api/tickets/t_current/propose"]


@pytest.mark.parametrize("command", ("ticket", "worker"))
def test_guidance_is_not_a_ticket_record_part(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == "/api/worker-types":
            return {"worker_types": [{"worker_type": "coding", "fields": [{"id": "brief"}]}]}
        return _ticket_record("t_current")

    monkeypatch.setattr(http, "send", fake_send)
    arguments = ["ticket", "show", "t_current"] if command == "ticket" else ["worker", "my-ticket"]
    result = CliRunner().invoke(
        main, [*arguments, "guidance", "--json"], env={"PLAN_TICKET_ID": "t_current"}
    )

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["message"] == (
        "unknown part names: guidance; valid part names: brief, proposal, recap"
    )


def test_worker_note_command_is_removed() -> None:
    result = CliRunner().invoke(main, ["worker", "note", "t_current"], input="Old note.")

    assert result.exit_code == 2
    assert '"note" is not a command under panels worker.' in result.output


@pytest.fixture
def skill_requests(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    """Exercise CLI parsing and the real HTTP seam without starting a server."""
    calls: list[tuple[str, str, dict[str, Any]]] = []
    skill = {"name": "custom-skill", "description": "Old description", "markdown_body": "# Body"}

    def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        path = httpx.URL(url).raw_path.decode()
        calls.append((method, path, kwargs))
        if path == "/api/skills":
            data: dict[str, Any] = {"skills": [skill]}
        elif path == "/api/worker-types/custom":
            data = {"profile": {"specialist_skill": skill["name"]}}
        elif path == "/api/workers/custom":
            data = {
                "manifest": {},
                "settings": {"worker_type": "custom", "specialist_skill": skill},
            }
        else:
            assert path.startswith("/api/skills/")
            data = {**skill, **(kwargs.get("json") or {})}
        return httpx.Response(200, json=data, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", request)
    return calls


def test_skill_reads_preserve_envelopes_and_exact_body_for_roundtrip(
    skill_requests: list[tuple[str, str, dict[str, Any]]],
) -> None:
    runner = CliRunner()
    listed = runner.invoke(main, ["skill", "list"])
    listed_json = runner.invoke(main, ["skill", "list", "--json"])
    shown = runner.invoke(main, ["skill", "show", "folder/name"])
    shown_json = runner.invoke(main, ["skill", "show", "folder/name", "--json"])

    assert listed.exit_code == listed_json.exit_code == shown.exit_code == shown_json.exit_code == 0
    assert listed.stdout == "custom-skill\n"
    assert json.loads(listed_json.stdout)["skills"][0]["name"] == "custom-skill"
    assert shown.stdout == "# Body"
    assert json.loads(shown_json.stdout)["markdown_body"] == shown.stdout
    assert skill_requests[2][1] == skill_requests[3][1] == "/api/skills/folder%2Fname"
    edited = runner.invoke(main, ["skill", "edit", "custom-skill"], input=shown.stdout)
    assert edited.exit_code == 0, edited.output
    assert skill_requests[-1][2]["json"] == {"markdown_body": "# Body"}


@pytest.mark.parametrize(
    ("description", "stdin", "expected"),
    (
        (None, "# New body\n", {"markdown_body": "# New body\n"}),
        ("New description", "", {"description": "New description"}),
        (
            "New description",
            "# New body\n",
            {"description": "New description", "markdown_body": "# New body\n"},
        ),
    ),
)
def test_skill_edit_sends_only_supplied_fields_in_one_patch(
    skill_requests: list[tuple[str, str, dict[str, Any]]],
    description: str | None,
    stdin: str,
    expected: dict[str, str],
) -> None:
    arguments = ["skill", "edit", "custom-skill", "--json"]
    if description is not None:
        arguments.extend(["--description", description])
    result = CliRunner().invoke(main, arguments, input=stdin)

    assert result.exit_code == 0, result.output
    assert len(skill_requests) == 1
    assert skill_requests[0][:2] == ("PATCH", "/api/skills/custom-skill")
    assert skill_requests[0][2]["json"] == expected
    assert json.loads(result.stdout)["name"] == "custom-skill"


@pytest.mark.parametrize(
    "command", (("skill", "edit", "custom-skill"), ("worker-type", "skill", "custom"))
)
def test_skill_edit_commands_keep_worker_identity_and_resolve_declared_skill(
    skill_requests: list[tuple[str, str, dict[str, Any]]], command: tuple[str, ...]
) -> None:
    result = CliRunner().invoke(
        main,
        [*command, "--json"],
        input="# New body",
        env={"PLAN_ACTOR": "worker", "PLAN_TICKET_ID": "t_current"},
    )

    assert result.exit_code == 0, result.output
    for _method, _path, kwargs in skill_requests:
        assert kwargs["headers"] == {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_current"}
    patches = [
        (method, path, kwargs["json"])
        for method, path, kwargs in skill_requests
        if method == "PATCH"
    ]
    assert patches == [("PATCH", "/api/skills/custom-skill", {"markdown_body": "# New body"})]
    if command[0] == "worker-type":
        assert [path for _method, path, _kwargs in skill_requests] == [
            "/api/worker-types/custom",
            "/api/skills/custom-skill",
            "/api/workers/custom",
        ]
        assert json.loads(result.stdout) == {
            "worker_type": "custom",
            "specialist_skill": {
                "name": "custom-skill",
                "description": "Old description",
                "markdown_body": "# Body",
            },
        }


def test_skill_description_edit_never_reads_terminal_input(monkeypatch: pytest.MonkeyPatch) -> None:
    class Terminal(StringIO):
        def isatty(self) -> bool:
            return True

        def read(self, *args: Any, **kwargs: Any) -> str:
            raise AssertionError("must not wait on a terminal")

    monkeypatch.setattr(sys, "stdin", Terminal())
    assert _read_skill_patch("New description", False) == {"description": "New description"}
