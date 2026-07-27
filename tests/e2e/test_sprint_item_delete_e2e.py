from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
from tests.e2e.harness import JsonObject, ServerHandle

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "panels"


def _cli_process(server: ServerHandle, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    env["PLAN_SERVER_URL"] = server.base
    return subprocess.run(
        [str(PLAN_BIN), *args, "--json"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=30,
    )


def test_sprint_item_delete_cli_requires_yes_refuses_children_and_deletes(
    server: ServerHandle, cli: Callable[..., JsonObject]
) -> None:
    child_item_id = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Protected item",
        "--project",
        "Vylo",
    )["id"]
    child_ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Protected work",
        "--sprint-item",
        child_item_id,
    )["id"]

    no_confirmation = _cli_process(server, "sprint", "item", "delete", child_item_id)
    assert no_confirmation.returncode == 1
    assert json.loads(no_confirmation.stderr)["error"]["message"] == (
        "permanent deletion requires --yes"
    )

    child_refusal = _cli_process(
        server, "sprint", "item", "delete", child_item_id, "--yes"
    )
    assert child_refusal.returncode == 1
    assert json.loads(child_refusal.stderr)["error"] == {
        "code": "validation",
        "message": "sprint item has child tickets",
        "detail": {
            "sprint_item_id": child_item_id,
            "ticket_ids": [child_ticket_id],
        },
    }
    assert httpx.get(f"{server.base}/api/items/{child_item_id}").status_code == 200

    deletable_item_id = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Redundant item",
        "--project",
        "Vylo",
    )["id"]
    deleted = cli(
        server,
        "sprint",
        "item",
        "delete",
        deletable_item_id,
        "--yes",
    )
    assert deleted["ok"] is True
    assert deleted["sprint_item_id"] == deletable_item_id
    assert httpx.get(f"{server.base}/api/items/{deletable_item_id}").status_code == 404
