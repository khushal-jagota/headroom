"""The smallest live-browser proof for a finished Ticket's optional verdict."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle


def _save_verdict(page: Page, rating: int, text: str) -> None:
    page.click(f"[data-verdict-rating='{rating}']")
    page.fill("textarea[aria-label='Verdict text']", text)
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url.endswith("/verdict")
    ):
        page.click("[data-save-verdict]")


def test_finished_ticket_verdict_round_trip_and_reopened_read_only(
    tmp_path: Path,
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
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
        "A finished piece of work",
    )["id"]
    api.direct_post(server, f"/api/tickets/{ticket_id}/stage", {"to_stage": "done"})

    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/workspace/{ticket_id}", ready)
    assert page.locator("[data-ticket-verdict][data-editable='true']").count() == 1

    page.click("[data-add-verdict]")
    assert page.text_content(".ticket-verdict-rating-group legend") == "Rating"
    _save_verdict(page, 4, "The result was clear and useful.")
    page.wait_for_selector("[data-edit-verdict]", timeout=WAIT_MS)
    assert "Great" in page.inner_text("[data-ticket-verdict]")

    page.reload()
    page.wait_for_selector("[data-edit-verdict]", timeout=WAIT_MS)
    assert "The result was clear and useful." in page.inner_text("[data-ticket-verdict]")

    page.click("[data-edit-verdict]")
    _save_verdict(page, 5, "This is exactly how finished work must feel.")
    page.wait_for_selector("[data-edit-verdict]", timeout=WAIT_MS)
    assert "Extraordinary" in page.inner_text("[data-ticket-verdict]")

    page.click("[data-edit-verdict]")
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url.endswith("/verdict")
    ):
        page.click("[data-clear-verdict]")
    page.wait_for_selector("[data-add-verdict]", timeout=WAIT_MS)

    page.click("[data-add-verdict]")
    _save_verdict(page, 5, "This is exactly how finished work must feel.")
    page.wait_for_selector("[data-edit-verdict]", timeout=WAIT_MS)
    evidence = Path(
        os.environ.get(
            "PANELS_VERDICT_EVIDENCE_PATH",
            str(tmp_path / "finished-ticket-verdict.png"),
        )
    )
    evidence.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence), full_page=True)
    assert evidence.stat().st_size > 0

    api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/stage",
        {"to_stage": "needs_closeout"},
    )
    page.wait_for_selector(
        f'{ready}[data-stage="needs_closeout"] [data-ticket-verdict][data-editable="false"]',
        timeout=WAIT_MS,
    )
    assert page.locator("[data-edit-verdict]").count() == 0
    assert "Extraordinary" in page.inner_text("[data-ticket-verdict]")
