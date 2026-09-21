"""The human CLI error contract and its registered recovery calls."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import click
import httpx
import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.errors import COMMAND_RECOVERY_SHAPES, LEGACY_SUPERVISOR_RECOVERIES
from planner.cli.main import main as cli_main
from planner.environments import cli as environment_cli


def _lines(result_output: str) -> list[str]:
    return [line for line in result_output.splitlines() if line]


def _materialized_tokens(call: str) -> list[str]:
    tokens = shlex.split(call)
    if "<" in tokens:
        tokens = tokens[: tokens.index("<")]
    if call.startswith("panels day set"):
        field = "focus"
    elif call.startswith(("panels project set", "panels sprint set")):
        field = "name"
    else:
        field = "title"
    values = {
        "ARTIFACT_PATH": "note.md",
        "DIRECTORY": ".",
        "FIELD": field,
        "FILE": "pyproject.toml",
        "ITEM_ID": "si_example",
        "PART_NAMES": "brief",
        "PROJECT_ID": "project_example",
        "SCHEDULE_ID": "schedule_example",
        "SPRINT_ID": "sp_example",
        "STAGE": "needs_plan",
        "TEXT": "text",
        "TICKET_ID": "t_example",
        "VALUE": "value",
        "WORKER_TYPE": "coding",
    }
    materialized: list[str] = []
    for token in tokens:
        if token == "[OPTIONS]":
            continue
        shaped = (token.startswith("[") and token.endswith("]")) or (
            token.startswith("{") and token.endswith("}")
        )
        bare = token.removeprefix("[").removesuffix("]")
        bare = bare.removeprefix("{").removesuffix("}")
        if "|" in bare:
            bare = bare.split("|", 1)[0]
        materialized.append(values.get(bare, bare if shaped else token))
    return materialized


def _assert_registered_call(call: str) -> None:
    tokens = _materialized_tokens(call)
    assert tokens.pop(0) == "panels"
    command: click.Command = cli_main
    ctx = click.Context(cli_main, info_name="panels")
    while isinstance(command, click.Group):
        assert tokens, f"recovery stops at command group: {call}"
        name = tokens.pop(0)
        child = command.get_command(ctx, name)
        assert child is not None, f"recovery names no registered command: {call}"
        ctx = click.Context(child, info_name=name, parent=ctx)
        command = child
    command_name = ctx.info_name or command.name or "command"
    with command.make_context(command_name, tokens, parent=ctx.parent):
        pass


def _registered_command_paths() -> tuple[tuple[str, ...], ...]:
    paths: list[tuple[str, ...]] = []

    def walk(command: click.Command, ctx: click.Context, path: tuple[str, ...]) -> None:
        paths.append(path)
        if not isinstance(command, click.Group):
            return
        for name in command.list_commands(ctx):
            child = command.get_command(ctx, name)
            assert child is not None
            child_ctx = click.Context(child, info_name=name, parent=ctx)
            walk(child, child_ctx, (*path, name))

    walk(cli_main, click.Context(cli_main, info_name="panels"), ())
    return tuple(paths)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (
            ("ticket",),
            (
                "error: panels ticket needs a command.",
                "No panels call can be named until an action is selected.",
            ),
        ),
        (
            ("ticket", "shwo"),
            (
                'error: "shwo" is not a command under panels ticket.',
                "Use: panels ticket show [OPTIONS] [TICKET_ID] [PART_NAMES]",
            ),
        ),
        (
            ("ticket", "unknown"),
            (
                'error: "unknown" is not a command under panels ticket.',
                'No replacement panels command can be inferred for "unknown".',
            ),
        ),
        (
            ("ticket", "show", "t_example", "--field", "brief"),
            (
                'error: "--field" is not an option for panels ticket show.',
                "Use: panels ticket show [OPTIONS] [TICKET_ID] [PART_NAMES]",
            ),
        ),
        (
            ("ticket", "create"),
            (
                "error: Missing option '--title'.",
                "Use: panels ticket create --title TEXT --worker-type TEXT [OPTIONS]",
            ),
        ),
        (
            (
                "ticket",
                "create",
                "--title",
                "Example",
                "--worker-type",
                "coding",
                "--priority",
                "PX",
            ),
            (
                "error: Invalid value for '--priority': "
                "'PX' is not one of 'P0', 'P1', 'P2', 'P3'.",
                "Use: panels ticket create --title TEXT --worker-type TEXT [OPTIONS]",
            ),
        ),
        (
            ("feedback", "list", "extra"),
            (
                "error: Got unexpected extra argument (extra)",
                "Use: panels feedback list [OPTIONS]",
            ),
        ),
        (
            ("sprint", "item", "supervisor", "approve"),
            (
                'error: "supervisor" is not a command under panels sprint item.',
                "Use: panels ticket approve TICKET_ID --ceiling STAGE",
            ),
        ),
    ),
)
def test_click_refusals_are_short_and_name_registered_recovery_calls(
    arguments: tuple[str, ...], expected: tuple[str, str]
) -> None:
    result = CliRunner().invoke(cli_main, list(arguments))

    assert result.exit_code == 2
    assert _lines(result.stderr) == list(expected)
    assert "Usage:" not in result.stderr
    if expected[1].startswith("Use: "):
        _assert_registered_call(expected[1].removeprefix("Use: "))


@pytest.mark.parametrize("command_path", _registered_command_paths())
def test_every_registered_command_has_concise_unknown_option_output(
    command_path: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(cli_main, [*command_path, "--not-a-panels-option"])
    lines = _lines(result.stderr)

    assert result.exit_code == 2
    assert 1 <= len(lines) <= 2
    assert lines[0].startswith('error: "--not-a-panels-option" is not an option for panels')
    assert "Usage:" not in result.stderr
    if lines[-1].startswith("Use: "):
        _assert_registered_call(lines[-1].removeprefix("Use: "))


@pytest.mark.parametrize("legacy_command", tuple(LEGACY_SUPERVISOR_RECOVERIES))
def test_each_deleted_supervisor_command_names_a_registered_replacement(
    legacy_command: str,
) -> None:
    result = CliRunner().invoke(
        cli_main,
        ["sprint", "item", "supervisor", legacy_command],
    )
    recovery = LEGACY_SUPERVISOR_RECOVERIES[legacy_command]

    assert result.exit_code == 2
    assert _lines(result.stderr)[1] == f"Use: {recovery}"
    _assert_registered_call(recovery)


@pytest.mark.parametrize("recovery", tuple(COMMAND_RECOVERY_SHAPES.values()))
def test_each_manual_validation_recovery_names_a_registered_call(recovery: str) -> None:
    _assert_registered_call(recovery)


def test_local_validation_uses_a_complete_call_and_keeps_existing_good_guidance() -> None:
    missing_target = CliRunner().invoke(cli_main, ["send-message"])
    retired_recap = CliRunner().invoke(
        cli_main,
        ["worker", "propose", "t_example", "--recap", "old"],
        input="Proposal",
    )

    assert missing_target.exit_code == 1
    assert _lines(missing_target.stderr) == [
        "error: send-message requires exactly one of --owner, --chief, --ticket, or "
        "--sprint-item",
        "Use: panels send-message --owner --message TEXT",
    ]
    _assert_registered_call("panels send-message --owner --message TEXT")
    assert retired_recap.exit_code == 1
    assert _lines(retired_recap.stderr) == [
        "error: a proposal no longer carries the recap: use `panels worker recap` instead"
    ]


def _fake_worker_record_send(
    method: str,
    path: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    assert method == "GET"
    if path == "/api/worker-types":
        return {
            "worker_types": [
                {
                    "worker_type": "coding",
                    "fields": [{"id": "brief"}],
                    "stages": [],
                }
            ]
        }
    assert path == "/api/tickets/t_example/worker-self"
    return {
        "id": "t_example",
        "worker_type": "coding",
        "field_values": {"brief": "Context"},
    }


def test_revision_feedback_error_names_its_real_non_cli_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "send", _fake_worker_record_send)
    result = CliRunner().invoke(
        cli_main,
        ["worker", "my-ticket", "rejection"],
        env={"PLAN_TICKET_ID": "t_example", "PLAN_ACTOR": "worker"},
    )

    assert result.exit_code == 1
    assert _lines(result.stderr) == [
        "error: unknown part names: rejection; valid part names: brief, proposal, recap, "
        "guidance",
        "Revision feedback arrives in the Worker prompt. No panels command reads it.",
    ]


def test_revision_feedback_json_envelope_stays_machine_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "send", _fake_worker_record_send)
    result = CliRunner().invoke(
        cli_main,
        ["worker", "my-ticket", "rejection", "--json"],
        env={"PLAN_TICKET_ID": "t_example", "PLAN_ACTOR": "worker"},
    )

    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error": {
            "code": "validation",
            "message": (
                "unknown part names: rejection; valid part names: brief, proposal, recap, "
                "guidance"
            ),
            "detail": {},
        }
    }


def test_server_authority_refusal_states_that_this_actor_has_no_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "error": {
            "code": "agent_forbidden",
            "message": "this principal does not stand above what it asked to act on",
            "detail": {"actor": "worker"},
        }
    }

    def refuse(*_args: Any, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            400,
            json=payload,
            request=httpx.Request("GET", "http://test/api/projects"),
        )

    monkeypatch.setattr(httpx, "request", refuse)
    human = CliRunner().invoke(cli_main, ["project", "list"])
    machine = CliRunner().invoke(cli_main, ["project", "list", "--json"])

    assert human.exit_code == 1
    assert _lines(human.stderr) == [
        "error: This actor cannot perform this operation.",
        "No panels call can perform it as this actor.",
    ]
    assert machine.exit_code == 1
    assert json.loads(machine.stderr) == payload


def test_transport_and_unstructured_http_failures_name_their_true_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", "http://test/api/projects")
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(httpx, "request", unavailable)
    connection = CliRunner().invoke(cli_main, ["project", "list"])

    def broken(*_args: Any, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            500,
            text="broken",
            request=httpx.Request("GET", "http://test/api/projects"),
        )

    monkeypatch.setattr(httpx, "request", broken)
    response = CliRunner().invoke(cli_main, ["project", "list"])

    assert connection.exit_code == 2
    assert _lines(connection.stderr) == [
        "error: The Panels server is unavailable.",
        "No panels call can work until the server is available.",
    ]
    assert response.exit_code == 1
    assert _lines(response.stderr) == [
        "error: HTTP 500",
        "No panels call can recover from this server response.",
    ]


def test_environment_exception_keeps_the_cause_and_names_the_command_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_db = tmp_path / "source.db"
    source_db.touch()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    def fail_backup(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("snapshot validation failed")

    monkeypatch.setattr(environment_cli, "create_database_backup", fail_backup)
    result = CliRunner().invoke(
        cli_main,
        [
            "environment",
            "backup",
            "--source-db",
            str(source_db),
            "--backup-dir",
            str(backup_dir),
            "--deployed-revision",
            "abc123",
        ],
    )

    assert result.exit_code == 1
    assert _lines(result.stderr) == [
        "error: snapshot validation failed",
        "Use: panels environment backup --source-db FILE --backup-dir DIRECTORY "
        "--deployed-revision TEXT",
    ]
    _assert_registered_call(
        "panels environment backup --source-db FILE --backup-dir DIRECTORY "
        "--deployed-revision TEXT"
    )
