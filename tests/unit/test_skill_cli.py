"""The managed skill CLI preserves exact text and the shared write contract."""

from __future__ import annotations

import json
import shlex
import sys
from io import StringIO
from typing import Any

import click
import httpx
import pytest
from click.testing import CliRunner

from planner.cli.main import _read_skill_patch, main


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


@pytest.mark.parametrize(
    ("arguments", "stdin", "cause"),
    (
        ((), "", "supply --description or a skill body on redirected stdin"),
        (("--description", "Description"), " \n", "markdown_body must not be blank"),
        (("--description", " "), "", "description must not be blank"),
    ),
)
def test_skill_input_refusals_name_a_complete_registered_edit(
    arguments: tuple[str, ...], stdin: str, cause: str
) -> None:
    result = CliRunner().invoke(main, ["skill", "edit", "example-skill", *arguments], input=stdin)

    assert result.exit_code == 1
    recovery = "panels skill edit NAME --description TEXT < SKILL_FILE"
    assert [line for line in result.stderr.splitlines() if line] == [
        f"error: {cause}",
        f"Use: {recovery}",
    ]
    tokens = shlex.split(recovery)
    tokens = tokens[: tokens.index("<")]
    replacements = {"NAME": "example-skill", "TEXT": "text"}
    tokens = [replacements.get(token, token) for token in tokens]
    assert tokens.pop(0) == "panels"
    command: click.Command = main
    ctx = click.Context(main, info_name="panels")
    while isinstance(command, click.Group):
        name = tokens.pop(0)
        child = command.get_command(ctx, name)
        assert child is not None, f"recovery names no registered command: {recovery}"
        ctx = click.Context(child, info_name=name, parent=ctx)
        command = child
    with command.make_context(ctx.info_name or "edit", tokens, parent=ctx.parent):
        pass


def test_skill_apply_sends_exact_reviewed_manifest(
    skill_requests: list[tuple[str, str, dict[str, Any]]],
) -> None:
    manifest = {
        "skills": [
            {"name": "custom-skill", "source_text": "exact\nbytes\n", "expected_sha256": None}
        ]
    }
    result = CliRunner().invoke(main, ["skill", "apply", "--json"], input=json.dumps(manifest))
    assert result.exit_code == 0, result.output
    assert skill_requests[0][:2] == ("POST", "/api/skills/apply")
    assert skill_requests[0][2]["json"] == manifest


def test_worker_type_save_exposes_stage_and_ceiling_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = {
        "ticket_id": "t_example",
        "title": "Review preserved",
        "old_stage": "needs_what_changes",
        "new_stage": "needs_what_changes",
        "old_ceiling": "needs_plan",
        "new_ceiling": "needs_what_changes",
    }

    def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        assert method == "POST" and httpx.URL(url).path == "/api/worker-types"
        return httpx.Response(
            200,
            json={"worker_type": "coding", "migrations": [receipt]},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", request)
    runner = CliRunner()
    result = runner.invoke(main, ["worker-type", "save"], input='{"worker_type":"coding"}')
    assert result.exit_code == 0, result.output
    assert "Review preserved: Stage needs_what_changes -> needs_what_changes" in result.stdout
    assert "ceiling needs_plan -> needs_what_changes" in result.stdout
    result_json = runner.invoke(
        main, ["worker-type", "save", "--json"], input='{"worker_type":"coding"}'
    )
    assert result_json.exit_code == 0, result_json.output
    assert json.loads(result_json.stdout)["migrations"] == [receipt]
