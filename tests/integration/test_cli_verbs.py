"""Process integration: the installed wrapper preserves identity outside a checkout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

from planner.environments.app import _write_app_entrypoints

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_ROOT = REPO_ROOT / "ops" / "panels-environments"
DEPLOYED_APP_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_installed_panels_preserves_worker_identity_for_read_and_write(
    tmp_path: Path,
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Installed CLI worker",
    )["id"]
    home = tmp_path / "operator"
    command_directory = home / ".local" / "bin"
    command_directory.mkdir(parents=True)
    shutil.copy2(OPS_ROOT / "panels", command_directory / "panels")
    app = home / "Deployments" / "Panels" / "current" / "app"
    app.mkdir(parents=True)
    (app / ".venv").symlink_to(Path(sys.prefix))
    _write_app_entrypoints(app, app_sha=DEPLOYED_APP_SHA)
    outside_checkout = tmp_path / "outside-checkout"
    outside_checkout.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(
        {
            "HOME": str(home),
            "PATH": f"{command_directory}:/usr/bin:/bin",
            "PLAN_SERVER_URL": server.base,
            "PLAN_TICKET_ID": ticket_id,
            "PLAN_ACTOR": "worker",
            "PLAN_APP_ROOT": "/ambient/wrong-app",
            "PLAN_APP_SHA": "76543210fedcba9876543210fedcba9876543210",
            "PYTHONPATH": "/ambient/package",
        }
    )

    local_status = subprocess.run(
        ["panels", "environment", "status", "--json"],
        cwd=outside_checkout,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(local_status.stdout)["app"] == {
        "state": "healthy",
        "summary": "configured app identity",
        "sha": DEPLOYED_APP_SHA,
    }

    identity = subprocess.run(
        ["panels", "worker", "my-ticket", "--json"],
        cwd=outside_checkout,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(identity.stdout)["header"]["id"] == ticket_id

    proposal = subprocess.run(
        [
            "panels",
            "worker",
            "propose",
            "--json",
        ],
        input="Success through installed panels.",
        cwd=outside_checkout,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(proposal.stdout)["id"] == ticket_id
    detail = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert detail["pending_proposal"]["body"] == "Success through installed panels."
