from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "panels"


TITLE = "Mistaken ticket delete browser proof"


def _cli_process(server, *args: str) -> subprocess.CompletedProcess[str]:
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


def test_ticket_delete_cli_requires_yes_and_deletes(server, cli) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "CLI hard delete")["id"]

    refused = _cli_process(server, "ticket", "delete", ticket_id)
    assert refused.returncode == 1
    assert json.loads(refused.stderr)["error"]["message"] == (
        "permanent deletion requires --yes"
    )
    assert httpx.get(f"{server.base}/api/tickets/{ticket_id}").status_code == 200

    deleted = cli(server, "ticket", "delete", ticket_id, "--yes")
    assert deleted["ok"] is True
    assert deleted["ticket_id"] == ticket_id
    assert httpx.get(f"{server.base}/api/tickets/{ticket_id}").status_code == 404


def _assert_no_delete_control(page) -> None:
    assert page.get_by_role("button", name="Delete ticket", exact=True).count() == 0
    assert page.locator("[data-ticket-delete]").count() == 0


def test_ticket_ui_has_no_delete_control(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--title",
        TITLE,
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})

    ctx = context_factory()
    page = open_page(
        ctx,
        server,
        f"#/ticket/{ticket_id}",
        f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=True,
    )
    _assert_no_delete_control(page)

    page.goto(f"{server.base}/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=10_000)
    page.click(f'[data-card][data-ticket-id="{ticket_id}"]')
    page.wait_for_selector(
        'section[data-screen="workspace"] '
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        timeout=10_000,
    )
    _assert_no_delete_control(page)
