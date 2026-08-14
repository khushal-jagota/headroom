from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.cli.main import main as cli_main
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

ServerHandle = object


@pytest.fixture(autouse=True)
def scrub_ambient_plan_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)


def _run(_server: object, *args: str, actor: str | None = "chief") -> Any:
    env = {"PLAN_SERVER_URL": "http://testserver"}
    if actor is not None:
        env["PLAN_ACTOR"] = actor
    return CliRunner().invoke(cli_main, list(args), env=env)


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    db_path = tmp_path / "chief-cli.db"
    with connect(str(db_path)) as conn:
        create_schema(conn)
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:

        def request(
            method: str,
            url: str,
            *,
            json: Any = None,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> httpx.Response:
            del timeout
            path = httpx.URL(url).raw_path.decode()
            return cast(
                httpx.Response,
                client.request(method, path, json=json, params=params, headers=headers),
            )

        monkeypatch.setattr(httpx, "request", request)
        yield object()


def _file(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_chief_external_work_help_lists_stage_and_worker_type_options(
    server: ServerHandle,
) -> None:
    # --stage is now a free-form option (validated server-side per Worker type), so --help no
    # longer enumerates coding.s Stages; both external-work commands still surface the
    # --stage option, and create surfaces the required --worker-type option.
    for command in (
        "reconcile-ticket-from-external-work",
        "create-ticket-from-external-work",
    ):
        result = _run(server, "chief", command, "--help", actor=None)
        assert result.exit_code == 0, result.output
        assert "--stage" in result.stdout
        assert "--field-file" in result.stdout
    create_help = _run(
        server, "chief", "create-ticket-from-external-work", "--help", actor=None
    )
    assert "--worker-type" in create_help.stdout


def test_chief_external_work_cli_create_and_reconcile(
    server: ServerHandle, tmp_path: Path
) -> None:
    note = _file(tmp_path, "note.md", "User report plus Chief reconciliation reasoning")
    recap = _file(tmp_path, "recap.md", "Imported work")
    success = _file(tmp_path, "success.md", "Success")
    approach = _file(tmp_path, "approach.md", "Approach")
    plan = _file(tmp_path, "plan.md", "Plan")
    implementation = _file(tmp_path, "implementation.md", "Implementation")
    closeout = _file(tmp_path, "closeout.md", "Closeout")

    created = _run(
        server,
        "chief",
        "create-ticket-from-external-work",
        "--title",
        "Imported through CLI",
        "--worker-type",
        "coding",
        "--employee-backend",
        "hermes",
        "--employee-launch-model",
        "hermes-model",
        "--stage",
        "needs_plan",
        "--kickoff-note-file",
        note,
        "--recap-file",
        recap,
        "--success-file",
        success,
        "--approach-file",
        approach,
        "--json",
    )
    assert created.exit_code == 0, created.output
    created_json = json.loads(created.stdout)
    assert created_json["stage"] == "needs_plan"
    assert created_json["employee_backend"] == "hermes"

    reconciled = _run(
        server,
        "chief",
        "reconcile-ticket-from-external-work",
        created_json["id"],
        "--stage",
        "done",
        "--kickoff-note-file",
        note,
        "--success-file",
        success,
        "--approach-file",
        approach,
        "--plan-file",
        plan,
        "--implementation-file",
        implementation,
        "--closeout-file",
        closeout,
        "--json",
    )
    assert reconciled.exit_code == 0, reconciled.output
    reconciled_json = json.loads(reconciled.stdout)
    assert reconciled_json["id"] == created_json["id"]
    assert reconciled_json["stage"] == "done"


def test_chief_external_work_cli_carries_new_worker_fields(
    server: ServerHandle, tmp_path: Path
) -> None:
    note = _file(tmp_path, "new-worker-note.md", "Design imported outside Panels")
    understanding = _file(
        tmp_path, "understanding.md", "Bounded worker-design understanding"
    )
    stages = _file(tmp_path, "stages.md", "needs_thinking, needs_drafting")
    thinking = _file(tmp_path, "thinking.md", "Worker reasoning contract")
    runtime_defaults = _file(
        tmp_path, "runtime-defaults.md", "codex / gpt-5.6-sol / medium"
    )

    created = _run(
        server,
        "chief",
        "create-ticket-from-external-work",
        "--title",
        "Imported worker design",
        "--worker-type",
        "new_worker",
        "--stage",
        "needs_runtime_defaults",
        "--kickoff-note-file",
        note,
        "--field-file",
        f"understanding={understanding}",
        "--field-file",
        f"stages={stages}",
        "--field-file",
        f"thinking={thinking}",
        "--json",
    )
    assert created.exit_code == 0, created.output
    created_json = json.loads(created.stdout)
    assert created_json["stage"] == "needs_runtime_defaults"
    assert (
        created_json["fields"]["understanding"]["value"]
        == "Bounded worker-design understanding"
    )
    assert created_json["fields"]["stages"]["value"] == "needs_thinking, needs_drafting"
    assert created_json["fields"]["thinking"]["value"] == "Worker reasoning contract"
    assert created_json["fields"]["runtime_defaults"]["value"] is None

    reconciled = _run(
        server,
        "chief",
        "reconcile-ticket-from-external-work",
        created_json["id"],
        "--stage",
        "needs_drafting",
        "--kickoff-note-file",
        note,
        "--field-file",
        f"thinking={thinking}",
        "--field-file",
        f"runtime_defaults={runtime_defaults}",
        "--json",
    )
    assert reconciled.exit_code == 0, reconciled.output
    reconciled_json = json.loads(reconciled.stdout)
    assert reconciled_json["stage"] == "needs_drafting"
    assert (
        reconciled_json["fields"]["understanding"]["value"]
        == "Bounded worker-design understanding"
    )
    assert (
        reconciled_json["fields"]["stages"]["value"] == "needs_thinking, needs_drafting"
    )
    assert reconciled_json["fields"]["thinking"]["value"] == "Worker reasoning contract"
    assert reconciled_json["fields"]["runtime_defaults"]["value"] == (
        "codex / gpt-5.6-sol / medium"
    )


def test_chief_field_file_rejects_ambiguity_before_read_or_request(
    server: ServerHandle,
) -> None:
    before = _run(server, "ticket", "list", "--json")
    assert before.exit_code == 0, before.output
    before_ids = {ticket["id"] for ticket in json.loads(before.stdout)["tickets"]}

    cases = (
        (
            [
                "--field-file",
                "stages=/missing-one",
                "--field-file",
                "stages=/missing-two",
            ],
            "field file provided more than once: stages",
        ),
        (
            ["--success-file", "/missing-one", "--field-file", "success=/missing-two"],
            "field file provided more than once: success",
        ),
        (["--field-file", "malformed"], "field file must be FIELD=PATH: malformed"),
    )
    for options, message in cases:
        rejected = _run(
            server,
            "chief",
            "create-ticket-from-external-work",
            "--title",
            "Must not be created",
            "--worker-type",
            "new_worker",
            "--stage",
            "needs_thinking",
            "--kickoff-note-file",
            "/also-missing",
            *options,
            "--json",
        )
        assert rejected.exit_code == 1
        assert json.loads(rejected.stderr)["error"]["message"] == message

    after = _run(server, "ticket", "list", "--json")
    assert after.exit_code == 0, after.output
    assert {
        ticket["id"] for ticket in json.loads(after.stdout)["tickets"]
    } == before_ids


def test_chief_field_file_rejects_command_fixed_keys_before_read_or_request(
    server: ServerHandle, tmp_path: Path
) -> None:
    existing = _run(
        server,
        "ticket",
        "create",
        "--title",
        "Reserved-key reconciliation target",
        "--worker-type",
        "coding",
        "--json",
        actor=None,
    )
    assert existing.exit_code == 0, existing.output
    ticket_id = json.loads(existing.stdout)["id"]
    before = _run(server, "ticket", "list", "--json")
    assert before.exit_code == 0, before.output
    before_ids = {ticket["id"] for ticket in json.loads(before.stdout)["tickets"]}

    common_fixed_keys = ("stage", "kickoff_note", "recap")
    create_only_fixed_keys = (
        "title",
        "worker_type",
        "employee_backend",
        "employee_launch_model",
        "priority",
        "deadline",
        "project",
        "project_id",
        "sprint_item_id",
    )
    for key in common_fixed_keys:
        rejected = _run(
            server,
            "chief",
            "reconcile-ticket-from-external-work",
            ticket_id,
            "--stage",
            "needs_kickoff",
            "--kickoff-note-file",
            "/missing-kickoff-note",
            "--field-file",
            f"{key}=/missing-field-value",
            "--json",
        )
        assert rejected.exit_code == 1
        assert json.loads(rejected.stderr)["error"]["message"] == (
            f"field file conflicts with fixed request key: {key}"
        )

    for key in (*common_fixed_keys, *create_only_fixed_keys):
        rejected = _run(
            server,
            "chief",
            "create-ticket-from-external-work",
            "--title",
            "Must not be created",
            "--worker-type",
            "coding",
            "--stage",
            "needs_success",
            "--kickoff-note-file",
            "/missing-kickoff-note",
            "--field-file",
            f"{key}=/missing-field-value",
            "--json",
        )
        assert rejected.exit_code == 1
        assert json.loads(rejected.stderr)["error"]["message"] == (
            f"field file conflicts with fixed request key: {key}"
        )

    # A create-only key remains generic for reconcile and reaches the API, which owns
    # Worker-type field validity. Coding does not declare "title", so the API rejects it.
    note = _file(tmp_path, "reserved-note.md", "Complete external-work note")
    title_field = _file(tmp_path, "title-field.md", "Definition-owned title value")
    api_rejected = _run(
        server,
        "chief",
        "reconcile-ticket-from-external-work",
        ticket_id,
        "--stage",
        "needs_kickoff",
        "--kickoff-note-file",
        note,
        "--field-file",
        f"title={title_field}",
        "--json",
    )
    assert api_rejected.exit_code == 1
    assert json.loads(api_rejected.stderr)["error"] == {
        "code": "validation",
        "message": "unknown external-work field",
        "detail": {"field": "title"},
    }

    after = _run(server, "ticket", "list", "--json")
    assert after.exit_code == 0, after.output
    assert {
        ticket["id"] for ticket in json.loads(after.stdout)["tickets"]
    } == before_ids


def test_real_server_chief_external_work_terse_output_and_actor_rejection(
    server: ServerHandle, tmp_path: Path
) -> None:
    note = _file(tmp_path, "note.md", "Complete report and reconciliation reason")
    success = _file(tmp_path, "success.md", "Success")

    created = _run(
        server,
        "chief",
        "create-ticket-from-external-work",
        "--title",
        "Terse import",
        "--worker-type",
        "coding",
        "--stage",
        "needs_success",
        "--kickoff-note-file",
        note,
    )
    assert created.exit_code == 0, created.output
    ticket_id = created.stdout.split()[0]
    assert ticket_id.startswith("t_")
    assert "external work created" in created.stdout
    assert "needs_success" in created.stdout

    ordinary = _run(
        server,
        "ticket",
        "create",
        "--title",
        "To reconcile",
        "--worker-type",
        "coding",
        "--json",
        actor=None,
    )
    assert ordinary.exit_code == 0, ordinary.output
    ordinary_id = json.loads(ordinary.stdout)["id"]
    kickoff = _run(
        server,
        "ticket",
        "approve",
        ordinary_id,
        "--ceiling",
        "none",
        "--at-cap",
        "propose",
        "--json",
        actor=None,
    )
    assert kickoff.exit_code == 0, kickoff.output
    reconciled = _run(
        server,
        "chief",
        "reconcile-ticket-from-external-work",
        ordinary_id,
        "--stage",
        "needs_approach",
        "--kickoff-note-file",
        note,
        "--success-file",
        success,
    )
    assert reconciled.exit_code == 0, reconciled.output
    assert ordinary_id in reconciled.stdout
    assert "external work reconciled" in reconciled.stdout
    assert "needs_approach" in reconciled.stdout

    rejected = _run(
        server,
        "chief",
        "create-ticket-from-external-work",
        "--title",
        "Rejected import",
        "--worker-type",
        "coding",
        "--stage",
        "needs_success",
        "--kickoff-note-file",
        note,
        "--json",
        actor="worker",
    )
    assert rejected.exit_code == 1
    assert json.loads(rejected.stderr)["error"]["code"] == "agent_forbidden"
