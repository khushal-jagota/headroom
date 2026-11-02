"""Prove the shared compact card reaches the real atomic approval writer.

Mock browser tests cannot establish that edited text, reviewer and proposal revision
survive Ticket/Review's separate request adapters. This isolated real-server proof
checks both adapters and a replacement racing the request, without live state or agents.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from playwright.sync_api import BrowserContext, Page, Route, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.support.server_fixtures import rail_server  # noqa: F401

BODY = "The compact approval card preserves the visible proposal."


def _propose(server: ServerHandle, ticket_id: str, body: str) -> None:
    response = httpx.post(
        server.base + f"/api/tickets/{ticket_id}/propose",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
        json={"body": body, "recap": "Review the same isolated proposal on both hosts."},
        timeout=10,
    )
    assert response.is_success, response.text


@pytest.mark.parametrize("host", ["ticket"])
def test_shared_compact_card_real_transaction_and_stale_revision(
    host: str,
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    project = api.direct_post(server, "/api/projects", {"name": "Approval proof"})
    item = api.direct_post(
        server,
        "/api/items",
        {"title": "Named orchestrator must not appear", "project_id": project["id"]},
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Restore the compact approval card",
            "worker_type": "coding",
            "project_id": project["id"],
            "brief": "The brief is agreed.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    ticket_id = str(ticket["id"])
    owner = {"kind": "owner", "id": "owner"}
    manager = {"kind": "manager", "id": item["id"]}
    api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/accept/brief",
        {"next_ceiling": "needs_what_changes", "next_holder": owner},
    )
    _propose(server, ticket_id, BODY)
    path = f"#/workspace/item/{item['id']}/{ticket_id}" if host == "ticket" else "#/review"
    page = open_page(context_factory(), server, path, "[data-approval-block]")
    page.set_default_timeout(WAIT_MS)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    card = page.locator("[data-approval-block]")
    panel = page.locator("[data-ceiling-picker-panel]:visible")
    page.set_viewport_size({"width": 1440, "height": 1100})
    original = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    # The heading's reviewer gesture performs only a reroute, leaving the proposal parked.
    card.locator("[data-reviewer-heading]").click()
    with page.expect_response(
        lambda response: (
            response.request.method == "PATCH" and response.url.endswith(f"/tickets/{ticket_id}")
        )
    ) as reroute:
        panel.locator(f'[data-scope-holder="{item["id"]}"]').click()
    assert reroute.value.ok, reroute.value.text()
    rerouted = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert rerouted["ceiling_holder"] == manager
    assert rerouted["stage"] == original["stage"]
    assert rerouted["ceiling"] == original["ceiling"]
    assert rerouted["pending_proposal"]["body"] == BODY
    assert not rerouted["field_values"].get("what_changes")
    # Put the same ask back in the owner's Review queue through the canonical writer.
    api.direct_patch(server, f"/api/tickets/{ticket_id}", {"ceiling_holder": owner})
    page.reload()
    expect(card).to_be_visible()
    visible = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    stale_payloads: list[JsonObject] = []
    replacement = "A newer Worker proposal must survive the stale browser request."

    def replace_before_accept(route: Route) -> None:
        payload = route.request.post_data_json
        assert isinstance(payload, dict)
        stale_payloads.append(payload)
        _propose(server, ticket_id, replacement)
        route.continue_()

    accept_url = f"**/api/tickets/{ticket_id}/accept/what_changes"
    page.route(accept_url, replace_before_accept, times=1)
    card.locator("[data-accept]").click()
    panel.locator('[data-ceiling-picker-choice="needs_implementation"]').click()
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith(f"/{ticket_id}/accept/what_changes")
        )
    ) as stale:
        panel.locator('[data-scope-holder="owner"]').click()
    assert not stale.value.ok, stale.value.text()
    assert "visible proposal changed" in stale.value.text()
    assert (
        stale_payloads[0]["expected_proposal_revision"] == visible["pending_proposal"]["revision"]
    )
    after_stale = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert after_stale["pending_proposal"]["body"] == replacement
    for field in ("stage", "ceiling", "ceiling_holder", "field_values"):
        assert after_stale[field] == visible[field]
    expect(card.locator("[data-edit]")).to_contain_text(replacement)
    edited = "The owner edited and atomically accepted this exact replacement."
    card.locator("[data-edit]").fill(edited)
    card.locator("[data-edit]").press("Control+Enter")
    card.locator("[data-accept]").click()
    panel.locator('[data-ceiling-picker-choice="needs_implementation"]').click()
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith(f"/{ticket_id}/accept/what_changes")
        )
    ) as accepted:
        panel.locator('[data-scope-holder="owner"]').click()
    assert accepted.value.ok, accepted.value.text()
    accepted_payload = accepted.value.request.post_data_json
    assert isinstance(accepted_payload, dict)
    assert accepted_payload["edited_body"] == edited
    assert (
        accepted_payload["expected_proposal_revision"]
        == after_stale["pending_proposal"]["revision"]
    )
    final = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert final["pending_proposal"] is None
    assert final["field_values"]["what_changes"] == edited
    assert final["stage"] == "needs_implementation"
    assert final["ceiling"] == "needs_implementation"
    assert final["ceiling_holder"] == owner
    assert errors == []
