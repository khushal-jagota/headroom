"""Connected approval, revision and artifact proof for the approved page interiors.

The real server writers and SSE must agree with the redesigned controls. The isolated
conversation adapter avoids provider prompts. This does not replace the Luna lifecycle
proof, nor does it authorize production cutover.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
from playwright.sync_api import BrowserContext, Page, Route, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.e2e.test_approval_groups_message_dots import _conversation
from tests.e2e.test_orchestrator_rail import rail_server  # noqa: F401

# pytest resolves this explicitly imported fixture by name.

EVIDENCE = Path(
    "/home/vps/Deployments/Panels/current/data/files/tickets/t_9fh1vzxc/artifacts/page-interiors"
)


def _propose(server: ServerHandle, ticket_id: str, body: str) -> None:
    response = httpx.post(
        server.base + f"/api/tickets/{ticket_id}/propose",
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
        json={"body": body, "recap": "The next result is ready for review."},
        timeout=10,
    )
    assert response.is_success, response.text


def _measure(page: Page, selector: str) -> JsonObject:
    measured: JsonObject = page.locator(selector).evaluate("""element => {
      const title = element.querySelector('[data-feedback-page-title]');
      const recap = element.querySelector('[data-recap], [data-sprint-item-brief]');
      const scope = element.querySelector('.worker-ceiling-line [data-scope-ceiling]');
      const scopeStyle = scope && getComputedStyle(scope);
      return {width: innerWidth, title: getComputedStyle(title).fontSize,
        recap: getComputedStyle(recap).fontSize,
        scope: scopeStyle && {font: scopeStyle.fontFamily, size: scopeStyle.fontSize,
          border: scopeStyle.borderTopWidth, background: scopeStyle.backgroundColor,
          shadow: scopeStyle.boxShadow, radius: scopeStyle.borderRadius},
        overflow: document.documentElement.scrollWidth > innerWidth};
    }""")
    return measured


def test_page_approval_revision_artifacts_and_responsive_layout(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    project = api.direct_post(server, "/api/projects", {"name": "Page proof", "priority": "P1"})
    item = api.direct_post(
        server,
        "/api/items",
        {
            "title": "Verify the complete retirement product",
            "priority": "P1",
            "project_id": project["id"],
            "body": "Review the real connected product before release.",
        },
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Verify approval and revision on the approved Worker page",
            "worker_type": "coding",
            "kickoff_note": "The brief is ready for review.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    unrelated_project = api.direct_post(
        server, "/api/projects", {"name": "Unrelated proof", "priority": "P2"}
    )
    unrelated = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Worker from a different project",
            "worker_type": "coding",
            "project_id": unrelated_project["id"],
            "kickoff_note": "Not in the selected project.",
            "orchestrator": {"kind": "chief", "id": "chief"},
        },
    )
    ticket_id = str(ticket["id"])
    _conversation(server, "page-worker")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute("UPDATE tickets SET conversation_id=? WHERE id=?", ("page-worker", ticket_id))
        conn.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            ("page-worker", ticket_id),
        )
    artifact = server.db_path.parent / "files" / "tickets" / ticket_id / "proof.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Connected artifact\n\nThe preview stays over the same Worker.")
    (artifact.parent / "unlinked.md").write_text("# Unlinked managed artifact")
    api.direct_patch(
        server,
        f"/api/tickets/{ticket_id}",
        {
            "recap": "Verify approval and conversation boundaries in the new document.",
        },
    )
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/item/{item['id']}/{ticket_id}",
        '[data-screen="ticket"]',
    )
    page.set_default_timeout(WAIT_MS)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    requests: list[JsonObject] = []
    page.on(
        "request",
        lambda request: (
            requests.append(
                {
                    "method": request.method,
                    "url": request.url,
                    "body": request.post_data_json,
                }
            )
            if request.method in {"POST", "PATCH"} and "/api/tickets/" in request.url
            else None
        ),
    )
    worker = page.locator('[data-screen="ticket"]')
    rail_row = page.locator(f'.board-workspace-left [data-ticket-id="{ticket_id}"]')
    expect(rail_row).to_be_visible()
    expect(rail_row.locator(".stage-mark")).to_have_count(0)
    expect(worker.locator("[data-send-back]")).to_have_count(0)
    pending_type: list[JsonObject] = []
    for width in (1440, 1024, 820, 560):
        page.set_viewport_size({"width": width, "height": 1100})
        pending_type.append(
            worker.evaluate("""element => {
          const scope = getComputedStyle(element.querySelector('[data-pending-ceiling] button'));
          return {width: innerWidth,
          body: getComputedStyle(element.querySelector('.proposal-prose p')).fontSize,
          label: getComputedStyle(element.querySelector('[data-stage-fold-open]')).fontSize,
          scopeFont: scope.fontFamily,
          scopeBorder: scope.borderTopWidth,
          scopeBackground: scope.backgroundColor,
          scopeShadow: scope.boxShadow};
        }""")
        )
        page.screenshot(path=str(EVIDENCE / f"worker-pending-{width}.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1100})
    # The page offers one approval action. Opening it alone cannot mutate the stage.
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith(f"/{ticket_id}/accept/brief")
        )
    ) as accepted:
        worker.get_by_role("button", name="Approve", exact=True).click()
        assert api.get(server, f"/api/tickets?detail=full&id={ticket_id}")["stage"] == "needs_brief"
        page.locator('[data-ceiling-picker-choice="needs_success_condition"]').click()
        page.locator('[data-scope-holder="chief"]').click()
    assert accepted.value.ok, accepted.value.text()
    canonical = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert canonical["stage"] == "needs_success_condition"
    assert canonical["ceiling"] == "needs_success_condition"
    assert canonical["ceiling_holder"] == {"kind": "chief", "id": "chief"}
    assert canonical["pending_proposal"] is None
    expect(worker).to_have_attribute("data-stage", "needs_success_condition")
    expect(rail_row).to_have_count(0)
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    api.direct_patch(
        server,
        f"/api/tickets/{ticket_id}",
        {
            "field_values": {
                "brief": f"The agreed brief. [Proof](/files/tickets/{ticket_id}/proof.md)"
            },
        },
    )
    # The direct owner explicitly reroutes the next review to User. Chief remains
    # the saved reviewer until this separate authorized PATCH. This browser proof
    # does not claim that a Chief-addressed proposal enters the owner's queue.
    api.direct_patch(
        server, f"/api/tickets/{ticket_id}", {"ceiling_holder": {"kind": "owner", "id": "owner"}}
    )
    owner_rerouted = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert owner_rerouted["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert owner_rerouted["stage"] == "needs_success_condition"
    _propose(server, ticket_id, "The product preserves the actual approval contract.")
    owner_proposal = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert owner_proposal["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert owner_proposal["pending_proposal"]["field"] == "success_condition"
    expect(rail_row).to_be_visible()
    expect(rail_row.locator(".stage-mark")).to_have_count(0)
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    worker.locator("[data-send-back]").click()
    revision = "Preserve exact conversation dots and prove the saved reviewer."
    worker.locator("[data-revision-note]").fill(revision)
    with page.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith(f"/{ticket_id}/reject")
        )
    ) as rejected:
        worker.locator("[data-revision-send]").click()
    assert rejected.value.ok, rejected.value.text()
    canonical = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert canonical["stage"] == "needs_success_condition"
    assert canonical["pending_proposal"] is None
    with sqlite3.connect(server.db_path) as conn:
        feedback = conn.execute(
            "SELECT stage, feedback_json FROM ticket_revision_feedback WHERE ticket_id=?",
            (ticket_id,),
        ).fetchone()
    assert feedback is not None and feedback[0] == "needs_success_condition"
    assert json.loads(feedback[1])[0]["message"] == revision
    expect(worker.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    # The artifacts fold remains a reliable closed access point. Keyboard opening
    # and in-place preview do not navigate away from the canonical Worker route.
    artifacts = worker.locator("[data-artifacts-band]")
    expect(artifacts).not_to_have_attribute("open", "")
    artifacts.locator(":scope > summary").focus()
    page.keyboard.press("Enter")
    expect(artifacts).to_have_attribute("open", "")
    expect(artifacts.get_by_role("link").filter(has_text="unlinked")).to_be_visible()
    artifacts.get_by_role("link").filter(has_text="proof").click()
    expect(page.locator("[data-ticket-artifact]")).to_be_visible()
    expect(page.locator("[data-ticket-artifact]")).to_contain_text("Connected artifact")
    assert ticket_id in page.evaluate("location.hash")
    page.keyboard.press("Escape")
    expect(page.locator("[data-ticket-artifact]")).to_have_count(0)
    measurements: list[JsonObject] = []
    for width in (1440, 1024, 820, 560):
        page.set_viewport_size({"width": width, "height": 1100})
        measurements.append({"surface": "worker", **_measure(page, '[data-screen="ticket"]')})
        page.screenshot(path=str(EVIDENCE / f"worker-{width}.png"), full_page=True)
    page.goto(server.base + f"/#/workspace/item/{item['id']}")
    pane_selector = f'[data-sprint-item-workspace="{item["id"]}"]'
    pane = page.locator(pane_selector)
    expect(pane).to_be_visible()
    expect(pane.locator('[data-workspace-group="awaiting_reply"]')).to_have_count(0)
    expect(pane.locator('[data-workspace-section="today"]')).to_have_count(0)
    for width in (1440, 1024, 820, 560):
        page.set_viewport_size({"width": width, "height": 1100})
        measurements.append({"surface": "orchestrator", **_measure(page, pane_selector)})
        page.screenshot(path=str(EVIDENCE / f"orchestrator-{width}.png"), full_page=True)
    pane.locator(".page-eyebrow a").first.click()
    expect(page.locator('[data-screen="backlog"]')).to_be_visible()
    expect(page.locator(f'[data-ticket-id="{ticket_id}"]')).to_be_visible()
    expect(page.locator(f'[data-ticket-id="{unrelated["id"]}"]')).to_have_count(0)
    page.get_by_role("link", name="All Workers", exact=True).click()
    expect(page.locator(f'[data-ticket-id="{unrelated["id"]}"]')).to_be_visible()
    assert all(row["body"] == "15px" and row["label"] == "13px" for row in pending_type), (
        pending_type
    )
    assert all(
        "system-ui" in row["scopeFont"]
        and row["scopeBorder"] == "0px"
        and row["scopeBackground"] == "rgba(0, 0, 0, 0)"
        and row["scopeShadow"] == "none"
        for row in pending_type
    ), pending_type
    assert all(
        "system-ui" in row["scope"]["font"]
        and row["scope"]["size"] == "15px"
        and row["scope"]["border"] == "0px"
        and row["scope"]["background"] == "rgba(0, 0, 0, 0)"
        and row["scope"]["shadow"] == "none"
        for row in measurements
        if row["surface"] == "worker"
    ), measurements
    assert errors == [], errors
    assert console_errors == [], console_errors
    assert all(
        row["title"] == "28px" and row["recap"] == "17px" and not row["overflow"]
        for row in measurements
    ), measurements
    assert len([request for request in requests if request["url"].endswith("/accept/brief")]) == 1
    assert len([request for request in requests if request["url"].endswith("/reject")]) == 1
    (EVIDENCE / "connected-browser.json").write_text(
        json.dumps(
            {
                "measurements": measurements,
                "pending_type": pending_type,
                "page_errors": errors,
                "console_errors": console_errors,
                "requests": requests,
                "revision_feedback": json.loads(feedback[1]),
                "sender_facts": {
                    "approve": "direct owner browser action saves Chief principal",
                    "reroute": "separate direct owner PATCH explicitly selects owner principal",
                    "propose": "worker-authenticated POST carries this ticket ID",
                    "revision": "direct owner browser reject stores feedback for existing session",
                    "chief_prompt_delivery": "not exercised here; separate actual Luna proof",
                },
                "isolation": (
                    "temporary rail-proof.db, deterministic conversation adapter, "
                    "no background workers or provider prompts"
                ),
            },
            indent=2,
        )
    )

    shutil.copyfile(server.log_path, EVIDENCE / "isolated-server.log")


def test_waiting_ceiling_previews_reroute_raise_and_rejects_stale_revision(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    """A ceiling draft cannot silently approve a different visible proposal.

    This uses the real accept writer. A scheduling barrier lets an authenticated
    Worker replace its proposal after the owner commits the visible draft but before
    the HTTP writer sees it. The stale request must leave the new proposal untouched.
    """
    server = rail_server
    project = api.direct_post(server, "/api/projects", {"name": "Ceiling proof", "priority": "P2"})
    item = api.direct_post(
        server,
        "/api/items",
        {
            "title": "Review the explicit ceiling contract",
            "project_id": project["id"],
        },
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Preview each waiting ceiling effect",
            "worker_type": "coding",
            "kickoff_note": "The brief is agreed.",
            "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        },
    )
    ticket_id = str(ticket["id"])
    api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/accept/brief",
        {
            "next_ceiling": "needs_success_condition",
            "next_holder": {"kind": "owner", "id": "owner"},
        },
    )
    _propose(server, ticket_id, "The visible proposal requires explicit approval.")
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/item/{item['id']}/{ticket_id}",
        "[data-worker-live-card]",
    )
    page.set_default_timeout(WAIT_MS)
    page.set_viewport_size({"width": 1440, "height": 1100})
    worker = page.locator('[data-screen="ticket"]')
    control = worker.locator("[data-reviewer-control] [data-scope-ceiling]")
    before = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    original = before["pending_proposal"]
    assert original is not None
    revision = original["revision"]
    control.click()
    panel = page.locator("[data-ceiling-picker-panel]:visible")
    expect(panel.locator('[data-ceiling-part="ceiling"]')).not_to_be_disabled()
    expect(panel.locator("[data-ceiling-effect]")).to_contain_text("Nothing is approved")
    expect(panel.locator('[data-ceiling-picker-choice="needs_plan"]')).to_contain_text("approves")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / "waiting-ceiling-stage-options.png"), full_page=True)
    panel.locator('[data-ceiling-picker-choice="needs_success_condition"]').click()
    panel.locator('[data-scope-holder="owner"]').click()
    expect(panel).to_have_count(0)
    unchanged = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert unchanged["stage"] == before["stage"]
    assert unchanged["pending_proposal"]["body"] == original["body"]
    assert unchanged["pending_proposal"]["revision"] == revision
    # Choosing only the reviewer reroutes, without accepting the proposal.
    control.click()
    panel = page.locator("[data-ceiling-picker-panel]:visible")
    panel.locator('[data-ceiling-part="holder"]').click()
    page.keyboard.press("o")
    expect(panel.locator("[data-ceiling-effect]")).to_contain_text("Sends this")
    expect(panel.locator("[data-ceiling-effect]")).to_contain_text("Orchestrator")
    expect(panel.locator("[data-ceiling-effect]")).to_contain_text("Nothing is approved")
    before_commit = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert before_commit["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert before_commit["pending_proposal"]["revision"] == revision
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / "waiting-ceiling-reroute-preview.png"), full_page=True)
    with page.expect_response(
        lambda response: (
            response.request.method == "PATCH" and response.url.endswith(f"/tickets/{ticket_id}")
        )
    ) as rerouted_response:
        page.keyboard.press("Enter")
    assert rerouted_response.value.ok, rerouted_response.value.text()
    rerouted = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert rerouted["ceiling_holder"] == {"kind": "sprint_item", "id": item["id"]}
    assert rerouted["stage"] == before["stage"]
    assert rerouted["ceiling"] == before["ceiling"]
    for field in ("body", "field", "created_at"):
        assert rerouted["pending_proposal"][field] == original[field]
    assert rerouted["pending_proposal"]["revision"] > revision
    expect(control).to_contain_text("Orchestrator")
    visible_revision = rerouted["pending_proposal"]["revision"]
    stale_payloads: list[JsonObject] = []
    newer_body = "This newer authenticated Worker proposal must stay pending."

    def replace_before_accept(route: Route) -> None:
        payload = route.request.post_data_json
        assert isinstance(payload, dict)
        stale_payloads.append(payload)
        _propose(server, ticket_id, newer_body)
        route.continue_()

    accept_url = f"**/api/tickets/{ticket_id}/accept/success_condition"
    page.route(accept_url, replace_before_accept, times=1)
    control.click()
    panel = page.locator("[data-ceiling-picker-panel]:visible")
    panel.locator('[data-ceiling-picker-choice="needs_plan"]').click()
    expect(panel.locator("[data-ceiling-effect]")).to_contain_text("Approves this")
    expect(panel.locator("[data-ceiling-effect]")).to_contain_text("Plan")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / "waiting-ceiling-approval-preview.png"), full_page=True)
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith(f"/{ticket_id}/accept/success_condition")
        )
    ) as stale_response:
        panel.locator('[data-scope-holder="owner"]').click()
    assert not stale_response.value.ok
    assert stale_payloads[0]["expected_proposal_revision"] == visible_revision
    after_stale = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert after_stale["stage"] == before["stage"]
    assert after_stale["ceiling"] == before["ceiling"]
    assert after_stale["ceiling_holder"] == rerouted["ceiling_holder"]
    assert after_stale["pending_proposal"]["body"] == newer_body
    assert not after_stale["field_values"].get("success_condition")
    # The refreshed visible proposal can be approved with its own revision.
    expect(worker.locator(".proposal-prose")).to_contain_text(newer_body)
    control.click()
    panel = page.locator("[data-ceiling-picker-panel]:visible")
    panel.locator('[data-ceiling-picker-choice="needs_plan"]').click()
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith(f"/{ticket_id}/accept/success_condition")
        )
    ) as accepted:
        panel.locator('[data-scope-holder="owner"]').click()
    assert accepted.value.ok, accepted.value.text()
    final = api.get(server, f"/api/tickets?detail=full&id={ticket_id}")
    assert final["stage"] == "needs_what_changes"
    assert final["ceiling"] == "needs_plan"
    assert final["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert final["pending_proposal"] is None
    assert final["field_values"]["success_condition"] == newer_body
    expect(worker).to_have_attribute("data-stage", "needs_what_changes")
    (EVIDENCE / "waiting-ceiling-browser.json").write_text(
        json.dumps(
            {
                "initial_revision": revision,
                "rerouted_revision": visible_revision,
                "stale_request": stale_payloads[0],
                "stale_response": stale_response.value.json(),
                "new_revision": after_stale["pending_proposal"]["revision"],
                "final_stage": final["stage"],
                "final_ceiling": final["ceiling"],
                "continuation_limit": (
                    "No background worker in this browser fixture; actual post-raise "
                    "continuation requires separate runtime evidence."
                ),
            },
            indent=2,
        )
    )
