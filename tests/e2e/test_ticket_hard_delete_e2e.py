from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "panels"
WAIT_MS = 10_000


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


def test_ticket_page_names_confirmation_then_disappears_everywhere(
    server, context_factory, open_page, cli, api
) -> None:
    sprint = cli(
        server,
        "sprint",
        "create",
        "--name",
        "Delete proof sprint",
        "--date-start",
        "2026-07-01",
        "--date-end",
        "2026-07-14",
    )
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--title",
        TITLE,
        "--sprint",
        sprint["id"],
    )["id"]
    cli(server, "day", "add-ticket", ticket_id, "--date", "today")
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Mistaken ticket waiting for deletion.",
        ticket_id=ticket_id,
        stdin="This proposal should disappear.",
    )

    ctx = context_factory()
    page = open_page(
        ctx,
        server,
        f"#/ticket/{ticket_id}",
        f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=True,
    )

    page.once("dialog", lambda dialog: dialog.dismiss())
    page.click("[data-ticket-delete]")
    assert httpx.get(f"{server.base}/api/tickets/{ticket_id}").status_code == 200

    dialog_messages: list[str] = []

    def accept_delete(dialog) -> None:
        dialog_messages.append(dialog.message)
        dialog.accept()

    page.once("dialog", accept_delete)
    page.click("[data-ticket-delete]")
    assert dialog_messages
    assert TITLE in dialog_messages[0]
    assert "permanently" in dialog_messages[0].lower()

    page.wait_for_url(f"{server.base}/#/workspace", timeout=WAIT_MS)
    page.wait_for_selector('[data-screen="workspace"]', timeout=WAIT_MS)
    page.wait_for_function(
        "title => !document.body.innerText.includes(title)", arg=TITLE, timeout=WAIT_MS
    )
    assert httpx.get(f"{server.base}/api/tickets/{ticket_id}").status_code == 404
    assert ticket_id not in {
        entry["entity_id"] for entry in api.get(server, "/api/queues")["approvals"]
    }

    page.click('[data-screen="sprint"]')
    page.wait_for_selector('[data-screen="sprint"]', timeout=WAIT_MS)
    assert TITLE not in page.locator("body").inner_text()

    page.click('[data-screen="review"]')
    page.wait_for_selector('[data-screen="review"]', timeout=WAIT_MS)
    assert TITLE not in page.locator("body").inner_text()
