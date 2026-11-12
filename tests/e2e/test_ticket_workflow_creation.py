"""One connected proof of custom workflow API → saved Ticket → Ticket page display.

The browser has no Ticket creation form. Custom stages arrive through the API, so
this proves the saved custom stage reaches the canonical reader and the Ticket page.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ServerHandle, capture_success
from tests.support.connected_rows import _post


def test_adapted_preset_creates_an_independent_custom_workflow(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    preset = httpx.get(server.base + "/api/worker-types/coding", timeout=10)
    assert preset.is_success, preset.text
    record = preset.json()
    workflow = {
        "label": "Custom workflow",
        "guidance": record.get("guidance") or "",
        "stages": copy.deepcopy(record["stages"]),
        "fields": copy.deepcopy(record["fields"]),
    }
    # A new stage sits before Consequences.
    consequences = next(
        index
        for index, stage in enumerate(workflow["stages"])
        if stage["id"] == "needs_consequences"
    )
    workflow["stages"].insert(
        consequences,
        {
            "id": "needs_evidence_comparison",
            "label": "Evidence comparison",
            "gating_field": "evidence_comparison",
            "is_terminal": False,
            "ownership_mode": "worker",
            "instructions": "Compare only the two supplied records.",
        },
    )
    workflow["fields"].insert(
        consequences, {"id": "evidence_comparison", "label": "Comparison result"}
    )
    saved = _post(
        server,
        "/api/tickets",
        {
            "title": "Review the sample records",
            "brief": "Compare the supplied records and record the result.",
            "worker_type": "coding",
            "workflow": workflow,
            "manager": {"kind": "chief", "id": "chief"},
        },
    )
    assert saved["workflow"]["worker_profile_id"] is None
    assert any(
        step["label"] == "Evidence comparison"
        and step["instructions"] == "Compare only the two supplied records."
        for step in saved["workflow"]["stages"]
    )
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{saved['id']}",
        f'[data-screen="ticket"][data-ticket-id="{saved["id"]}"]',
    )
    page.locator("[data-stage-fold-open]").click()
    expect(page.get_by_text("Comparison result", exact=True).first).to_be_visible(timeout=WAIT_MS)
    page.get_by_text("Comparison result", exact=True).first.scroll_into_view_if_needed()
    capture_success(page, "custom-workflow-ticket.png")
    read = httpx.get(
        server.base + "/api/tickets", params={"id": saved["id"], "detail": "full"}, timeout=10
    )
    assert read.is_success, read.text
    assert read.json()["id"] == saved["id"]
    assert read.json()["workflow"] == saved["workflow"]
