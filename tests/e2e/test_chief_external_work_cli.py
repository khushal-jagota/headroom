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


def test_chief_external_work_state_choices_exclude_unsettled_kickoff(server) -> None:
    for command in (
        "reconcile-ticket-from-external-work",
        "create-ticket-from-external-work",
    ):
        result = _run(server, "chief", command, "--help", actor=None)
        assert result.returncode == 0, result.stderr
        assert "needs_success" in result.stdout
        assert "needs_kickoff" not in result.stdout


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
        "--state",
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
    assert created_json["state"] == "needs_plan"

    reconciled = _run(
        server,
        "chief",
        "reconcile-ticket-from-external-work",
        created_json["id"],
        "--state",
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
    assert reconciled_json["state"] == "done"


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
        "--state",
        "needs_success",
        "--kickoff-note-file",
        note,
    )
    assert created.returncode == 0, created.stderr
    ticket_id = created.stdout.split()[0]
    assert ticket_id.startswith("t_")
    assert "external work created" in created.stdout
    assert "needs_success" in created.stdout

    ordinary = _run(server, "ticket", "create", "--title", "To reconcile", "--json", actor=None)
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
        "--state",
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
        "--state",
        "needs_success",
        "--kickoff-note-file",
        note,
        "--json",
        actor="worker",
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stderr)["error"]["code"] == "agent_forbidden"
