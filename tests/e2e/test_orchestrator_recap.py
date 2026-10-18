"""Prove the recap edit and durable context survive the real product boundary.

An editor can look saved while the canonical body or its managed links are lost.
Only the built UI against a live server proves edit -> PATCH -> reload together
with the managed preview opener. Lower layers cannot exercise that combination.
All content and managed files belong to this harness's isolated synthetic state.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle, capture_success
from tests.e2e.test_orchestrator_rail import rail_server  # noqa: F401


def test_recap_save_reload_and_durable_context_previews(
    rail_server: ServerHandle,  # noqa: F811
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    tmp_path: Path,
) -> None:
    server = rail_server
    evidence = Path(os.environ.get("PANELS_RECAP_EVIDENCE_DIR", str(tmp_path)))
    project = api.direct_post(
        server, "/api/projects", {"name": "Synthetic recap proof"}
    )
    original = "Original synthetic brief: preserve the agreed scope and every prior decision."
    item = api.direct_post(
        server,
        "/api/items",
        {
            "title": "Keep review context easy to recover",
            "project_id": project["id"],
            "body": original,
        },
    )
    item_id = item["id"]
    base = f"/files/sprint-items/{item_id}"
    notes = "# Working notes\n\nDecision: retain the existing body.\n\nNext step: review the recap."
    for path, content in (
        ("artifacts/original-brief.md", original),
        ("artifacts/working-notes.md", notes),
    ):
        response = httpx.put(
            server.base + base + "/" + path,
            headers={"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": item_id},
            json={"content": content},
            timeout=10,
        )
        assert response.status_code < 300, response.text

    page = open_page(
        context_factory(), server, f"#/workspace/item/{item_id}", "[data-sprint-item-workspace]"
    )
    page.set_viewport_size({"width": 1440, "height": 1000})
    recap = page.get_by_role("textbox", name="Orchestrator recap", exact=True)
    expect(recap).to_contain_text(original)
    replacement = "The recap keeps this review easy to recover. Scope is agreed. Review is next."
    recap.fill(replacement)
    with page.expect_response(
        lambda response: (
            response.url == server.base + f"/api/items/{item_id}"
            and response.request.method == "PATCH"
        ),
        timeout=WAIT_MS,
    ) as saved:
        recap.press("Control+Enter")
    assert saved.value.ok
    assert saved.value.request.post_data_json == {"body": replacement}
    assert api.get(server, f"/api/items/{item_id}/workspace")["body"] == replacement

    page.reload()
    expect(recap).to_contain_text("The recap keeps this review easy to recover.")
    artifacts = page.locator("[data-artifacts-band]")
    if artifacts.get_attribute("open") is None:
        artifacts.locator(":scope > summary").click(timeout=WAIT_MS)
    evidence.mkdir(parents=True, exist_ok=True)
    capture_success(page, str(evidence / "recap-saved-reloaded.png"))

    for path, content, screenshot in (
        ("working-notes.md", "Decision: retain the existing body.", "recap-working-notes.png"),
        ("original-brief.md", original, "recap-preserved-original.png"),
    ):
        artifacts.locator(f'[data-artifact-chip="artifacts/{path}"]').click(timeout=WAIT_MS)
        preview = page.locator("[data-ticket-artifact]")
        expect(preview).to_contain_text(content)
        capture_success(page, str(evidence / screenshot))
        preview.locator("[data-ticket-artifact-close]").click(timeout=WAIT_MS)
        expect(preview).to_have_count(0)

    # Preservation precedes replacement, and both durable files remain readable.
    archived = httpx.get(server.base + base + "/artifacts/original-brief.md", timeout=10)
    assert archived.text == original
    assert httpx.get(server.base + base + "/artifacts/working-notes.md", timeout=10).text == notes
