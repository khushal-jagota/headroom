from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "panels"


def _run(server, *args: str, actor: str | None = "chief") -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    env["PLAN_SERVER_URL"] = server.base
    if actor is not None:
        env["PLAN_ACTOR"] = actor
    return subprocess.run(
        [str(PLAN_BIN), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=30,
    )


def _file(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_chief_external_work_help_lists_stage_and_worker_type_options(server) -> None:
    # --stage is now a free-form option (validated server-side per Worker type), so --help no
    # longer enumerates coding.s Stages; both external-work commands still surface the
    # --stage option, and create surfaces the required --worker-type option.
    for command in (
        "reconcile-ticket-from-external-work",
        "create-ticket-from-external-work",
    ):
        result = _run(server, "chief", command, "--help", actor=None)
        assert result.returncode == 0, result.stderr
        assert "--stage" in result.stdout
        assert "--field-file" in result.stdout
    create_help = _run(server, "chief", "create-ticket-from-external-work", "--help", actor=None)
    assert "--worker-type" in create_help.stdout


def test_chief_external_work_cli_create_and_reconcile(server, tmp_path: Path) -> None:
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
    assert created.returncode == 0, created.stderr
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
    assert reconciled.returncode == 0, reconciled.stderr
    reconciled_json = json.loads(reconciled.stdout)
    assert reconciled_json["id"] == created_json["id"]
    assert reconciled_json["stage"] == "done"


def test_chief_external_work_cli_carries_new_worker_fields(server, tmp_path: Path) -> None:
    note = _file(tmp_path, "new-worker-note.md", "Design imported outside Panels")
    understanding = _file(tmp_path, "understanding.md", "Bounded worker-design understanding")
    stages = _file(tmp_path, "stages.md", "needs_thinking, needs_drafting")
    thinking = _file(tmp_path, "thinking.md", "Worker reasoning contract")
    runtime_defaults = _file(tmp_path, "runtime-defaults.md", "codex / gpt-5.6-sol / medium")

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
    assert created.returncode == 0, created.stderr
    created_json = json.loads(created.stdout)
    assert created_json["stage"] == "needs_runtime_defaults"
    assert created_json["fields"]["understanding"]["value"] == "Bounded worker-design understanding"
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
    assert reconciled.returncode == 0, reconciled.stderr
    reconciled_json = json.loads(reconciled.stdout)
    assert reconciled_json["stage"] == "needs_drafting"
    assert (
        reconciled_json["fields"]["understanding"]["value"] == "Bounded worker-design understanding"
    )
    assert reconciled_json["fields"]["stages"]["value"] == "needs_thinking, needs_drafting"
    assert reconciled_json["fields"]["thinking"]["value"] == "Worker reasoning contract"
    assert reconciled_json["fields"]["runtime_defaults"]["value"] == (
        "codex / gpt-5.6-sol / medium"
    )


def test_chief_field_file_rejects_ambiguity_before_read_or_request(server) -> None:
    before = _run(server, "ticket", "list", "--json")
    assert before.returncode == 0, before.stderr
    before_ids = {ticket["id"] for ticket in json.loads(before.stdout)["tickets"]}

    cases = (
        (
            ["--field-file", "stages=/missing-one", "--field-file", "stages=/missing-two"],
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
        assert rejected.returncode == 1
        assert json.loads(rejected.stderr)["error"]["message"] == message

    after = _run(server, "ticket", "list", "--json")
    assert after.returncode == 0, after.stderr
    assert {ticket["id"] for ticket in json.loads(after.stdout)["tickets"]} == before_ids


def test_chief_field_file_rejects_command_fixed_keys_before_read_or_request(
    server, tmp_path: Path
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
    assert existing.returncode == 0, existing.stderr
    ticket_id = json.loads(existing.stdout)["id"]
    before = _run(server, "ticket", "list", "--json")
    assert before.returncode == 0, before.stderr
    before_ids = {ticket["id"] for ticket in json.loads(before.stdout)["tickets"]}

    common_fixed_keys = ("stage", "kickoff_note", "recap")
    create_only_fixed_keys = (
        "title",
        "worker_type",
        "employee_backend",
        "priority",
        "deadline",
        "project",
        "project_id",
        "sprint_id",
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
        assert rejected.returncode == 1
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
        assert rejected.returncode == 1
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
    assert api_rejected.returncode == 1
    assert json.loads(api_rejected.stderr)["error"] == {
        "code": "validation",
        "message": "unknown external-work field",
        "detail": {"field": "title"},
    }

    after = _run(server, "ticket", "list", "--json")
    assert after.returncode == 0, after.stderr
    assert {ticket["id"] for ticket in json.loads(after.stdout)["tickets"]} == before_ids


def test_real_server_chief_external_work_terse_output_and_actor_rejection(
    server, tmp_path: Path
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
    assert created.returncode == 0, created.stderr
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
    assert ordinary.returncode == 0, ordinary.stderr
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
    assert kickoff.returncode == 0, kickoff.stderr
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
    assert reconciled.returncode == 0, reconciled.stderr
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
    assert rejected.returncode == 1
    assert json.loads(rejected.stderr)["error"]["code"] == "agent_forbidden"
