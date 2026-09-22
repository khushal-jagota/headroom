"""The ordinary agent CLI stays small while legacy routes remain callable."""

from __future__ import annotations

import json
from typing import Any

import click
import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


def _visible_leaves(
    command: click.Command, path: tuple[str, ...]
) -> list[tuple[str, ...]]:
    if not isinstance(command, click.Group):
        return [path]
    leaves: list[tuple[str, ...]] = []
    for name, child in command.commands.items():
        if not child.hidden:
            leaves.extend(_visible_leaves(child, (*path, name)))
    return leaves


def test_ordinary_help_has_five_roots_and_forty_leaves() -> None:
    visible_roots = [
        name for name, command in main.commands.items() if not command.hidden
    ]
    leaves = _visible_leaves(main, ("panels",))

    assert visible_roots == ["send-message", "project", "day", "ticket", "sprint"]
    assert len(leaves) == 40
    assert ("panels", "ticket", "edit") in leaves
    assert ("panels", "ticket", "proposal") in leaves
    assert ("panels", "worker", "propose") not in leaves
    assert ("panels", "sprint", "outcome", "add") not in leaves


def test_administrative_and_legacy_paths_are_hidden_but_registered() -> None:
    for root in (
        "environment",
        "feedback",
        "schedule",
        "worker-type",
        "skill",
        "worker",
    ):
        assert main.commands[root].hidden is True

    ticket = main.commands["ticket"]
    assert isinstance(ticket, click.Group)
    for name in ("set", "set-value", "place", "approve", "reject", "copy"):
        assert ticket.commands[name].hidden is True

    sprint = main.commands["sprint"]
    assert isinstance(sprint, click.Group)
    assert sprint.commands["outcome"].hidden is True


def test_structured_create_forwards_the_existing_create_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs["json_body"]))
        return {"id": "t_new", "stage": "needs_success_condition"}

    monkeypatch.setattr(http, "send", fake_send)
    body = {
        "title": "One Ticket",
        "worker_type": "coding",
        "project_id": "project_panels",
        "sprint_id": None,
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
        "priority": "P1",
        "recap": "Ready.",
        "guidance_append": "Keep the route exact.",
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
