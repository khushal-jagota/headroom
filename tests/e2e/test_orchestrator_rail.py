"""Prove rail writes, invalidation and navigation against an isolated live server.

Settle/Reopen can hide outstanding work or replace the open Worker if the HTTP write,
board projection, SSE invalidation and route selection disagree. Frontend mocks and
backend tests cannot prove those boundaries together. Helper tests own grouping rules;
this small browser proof owns the connected behavior and keyboard/pointer access.
No backend prompts occur: deterministic durable messages provide unread evidence.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import (
    WAIT_MS,
    ApiHelper,
    JsonObject,
    ServerHandle,
    capture_success,
)
from tests.support.connected_rows import _bind, _conversation, _message, _post
from tests.support.server_fixtures import rail_server as rail_server

from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind

EVIDENCE = Path("/home/vps/Deployments/Panels/current/data/files/tickets/t_9fh1vzxc/artifacts/rail")
LONG_TITLE = "Review the entire Worker title when the adjustable rail clips this long approval row"


def _seed(
    server: ServerHandle,
    api: ApiHelper,
) -> tuple[JsonObject, JsonObject, JsonObject, JsonObject]:
    project = api.direct_post(server, "/api/projects", {"name": "Rail proof"})
    item = api.direct_post(
        server,
        "/api/managers",
        {
            "title": "Review the connected retirement rail",
            "project_id": project["id"],
        },
    )
    empty = api.direct_post(
        server,
        "/api/managers",
        {
            "title": "Empty manager stays visible",
            "project_id": project["id"],
        },
    )
    ticket = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": LONG_TITLE,
            "worker_type": "coding",
            "brief": "Review the brief.",
            "manager": {"kind": "manager", "id": item["id"]},
        },
    )
    # Empty and Blocked children belong in the hover details, not attention groups.
    for title, blockers in (
        ("Quiet ordinary Worker", []),
        ("Blocked ordinary Worker", [ticket["id"]]),
    ):
        child = api.direct_post(
            server,
            "/api/tickets",
            {
                "title": title,
                "worker_type": "coding",
                "brief": "Agreed brief.",
                "manager": {"kind": "manager", "id": item["id"]},
                "blocked_by_ticket_ids": blockers,
            },
        )
        api.direct_post(
            server,
            f"/api/tickets/{child['id']}/accept/brief",
            {"next_ceiling": "none", "next_holder": {"kind": "owner", "id": "owner"}},
        )
    question = api.direct_post(
        server,
        "/api/tickets",
        {
            "title": "Answer only Worker with no direct message",
            "worker_type": "coding",
            "brief": "Agreed brief.",
            "manager": {"kind": "manager", "id": item["id"]},
        },
    )
    api.direct_post(
        server,
        f"/api/tickets/{question['id']}/accept/brief",
        {"next_ceiling": "none", "next_holder": {"kind": "owner", "id": "owner"}},
    )
    _conversation(server, "rail-question")
    _bind(server, "ticket", question["id"], "rail-question")
    _post(server, "/api/test/signals/rail-question/start", {})
    _post(server, "/api/test/signals/rail-question/questions", {})
    _conversation(server, "rail-unread")
    _bind(server, "ticket", ticket["id"], "rail-unread")
    _message(
        server, "rail-unread", Principal(PrincipalKind.ticket, ticket["id"]), "Rail direct message"
    )
    for conversation in ("rail-chief", "rail-supervisor"):
        _conversation(server, conversation)
    _bind(server, "agent", item["manager"]["agent_key"], "rail-supervisor")
    _bind(server, "agent", "chief_of_staff", "rail-chief")
    return item, empty, ticket, question


def test_settle_reopen_keeps_outstanding_work_and_worker_address(
    rail_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    item, empty, ticket, question = _seed(server, api)
    context = context_factory()
    context.add_init_script("""
      window.__railOrchestratorFields = {};
      const Source = window.EventSource;
      window.EventSource = class extends Source {
        constructor(...args) {
          super(...args);
          for (const type of ['snapshot', 'patch']) this.addEventListener(type, event => {
            const frame = JSON.parse(event.data);
            for (const record of frame.content?.records ?? frame.upserts ?? []) {
              if (record.kind === 'manager')
                window.__railOrchestratorFields[record.id] = Object.keys(record.value).sort();
            }
          });
        }
      };
    """)
    page = open_page(context, server, "#/workspace?view=managers", '[data-screen="workspace"]')
    page.set_default_timeout(WAIT_MS)
    page.set_viewport_size({"width": 1440, "height": 1000})
    left = page.locator(".board-workspace-left")
    card = left.locator(f'[data-manager="{item["id"]}"]')
    row = card.locator(f'[data-ticket-id="{ticket["id"]}"]')
    expect(left.locator("[data-chief-destination]")).to_be_visible(timeout=WAIT_MS)
    expect(card).to_be_visible(timeout=WAIT_MS)
    expect(left.locator(".manager-card").first).to_have_class(re.compile(r"\bchief\b"))
    expect(
        left.locator(".manager-card.chief").get_by_role("button", name="Settle", exact=False)
    ).to_have_count(0)
    expect(card.locator(".manager-eyebrow")).to_contain_text("Rail proof")
    expect(left.locator(f'[data-manager="{empty["id"]}"]')).to_be_visible()
    api.direct_patch(server, f"/api/managers/{empty['id']}/settled", {"settled": True})
    expect(row.get_by_role("img", name="Unread message")).to_be_visible(timeout=WAIT_MS)
    expect(card.locator(".item-head .stage-mark")).to_have_count(0)
    expect(left.locator("[data-chief-destination] .stage-mark")).to_have_count(0)
    question_row = card.locator(f'[data-ticket-id="{question["id"]}"]')
    expect(question_row).to_be_visible(timeout=WAIT_MS)
    expect(question_row).to_contain_text("Answer only Worker with no direct message")
    expect(question_row.get_by_role("img")).to_have_count(1)
    expect(question_row.get_by_role("img")).to_have_attribute("aria-label", "Working")
    expect(card.locator('[data-bucket-key="awaiting_approval"]')).to_contain_text(LONG_TITLE)
    row.click()
    worker_hash = page.evaluate("location.hash")
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    with page.expect_response(
        lambda response: (
            response.url.endswith(f"/api/managers/{item['id']}/settled")
            and response.request.method == "PATCH"
        )
    ) as write:
        card.get_by_role("button", name=f"Settle {item['title']}", exact=True).click()
    assert write.value.ok
    # Keep this membership proof outside the intentional Worker-hover freeze.
    page.mouse.move(1000, 50)
    expect(card).to_have_count(0, timeout=WAIT_MS)
    assert write.value.json()["settled"] is True
    assert page.evaluate("location.hash") == worker_hash
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    # Settlement changes arrangement only. Existing global views retain attention.
    page.goto(server.base + "/#/review")
    expect(page.locator(f'[data-review-card][data-ticket-id="{ticket["id"]}"]')).to_be_visible(
        timeout=WAIT_MS
    )
    # The retired global list redirects to Workspace. Settled parents retain child access.
    page.goto(server.base + "/#/backlog?project=" + item["project_id"])
    page.wait_for_function("location.hash === '#/workspace'", timeout=WAIT_MS)
    expect(page.locator("[data-backlog-tickets]")).to_have_count(0)
    left.get_by_role("button", name="Settled", exact=False).click()
    lean_title = left.locator(f'[data-settled-item="{empty["id"]}"] .settled-title')
    expect(lean_title).to_be_visible(timeout=WAIT_MS)
    page.wait_for_function(
        "id => JSON.stringify(window.__railOrchestratorFields[id]) === "
        "JSON.stringify(['conversation_id','created_at','settled','title'])",
        arg=empty["id"],
        timeout=WAIT_MS,
    )
    lean_title.hover()
    lean_title.focus()
    page.evaluate(
        "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    expect(page.locator("[data-rail-hover-panel]")).to_have_attribute("aria-hidden", "true")
    capture_success(page, str(EVIDENCE / "settled-lean-no-popover.png"))
    scoped_parent = left.locator(f'[data-settled-item="{item["id"]}"]')
    scoped_parent.locator(".settled-title").click()
    scoped_pane = page.locator(f'[data-manager-workspace="{item["id"]}"]')
    scoped_row = scoped_pane.locator(f'[data-worker-ticket-id="{ticket["id"]}"]')
    # An absent row can mean loading, so inspect the mounted fold before toggling it.
    scoped_group = scoped_pane.locator('[data-workspace-group="awaiting_approval"]')
    expect(scoped_group).to_be_visible(timeout=WAIT_MS)
    if scoped_group.get_attribute("open") is None:
        scoped_group.locator(":scope > summary").click()
    expect(scoped_row).to_be_visible(timeout=WAIT_MS)
    expect(scoped_row).to_contain_text(LONG_TITLE)
    scoped_row.click()
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    page.go_back()
    expect(scoped_row).to_be_visible(timeout=WAIT_MS)
    page.goto(server.base + "/" + worker_hash)
    page.reload()
    expect(page.locator(f'[data-screen="ticket"][data-ticket-id="{ticket["id"]}"]')).to_be_visible()
    assert page.evaluate("location.hash") == worker_hash
    left.get_by_role("button", name="Settled", exact=False).click()
    settled = left.locator(f'[data-settled-item="{item["id"]}"]')
    expect(settled).to_be_visible()
    settled.get_by_role("button", name="Reopen", exact=False).click()
    expect(card).to_be_visible(timeout=WAIT_MS)
    with sqlite3.connect(server.db_path) as conn:
        assert (
            conn.execute("SELECT settled FROM managers WHERE id=?", (item["id"],)).fetchone()[0]
            == 0
        )
    expect(row.get_by_role("img", name="Unread message")).to_be_visible()
    capture_success(page, str(EVIDENCE / "rail-1440.png"))
    _message(server, "rail-chief", CHIEF_PRINCIPAL, "Chief own direct message")
    _message(
        server,
        "rail-supervisor",
        Principal(PrincipalKind.manager, item["id"]),
        "Supervisor own direct message",
    )
    page.reload()
    expect(card.locator(".item-head .stage-mark")).to_be_visible(timeout=WAIT_MS)
    expect(left.locator("[data-chief-destination] .stage-mark")).to_be_visible(timeout=WAIT_MS)
    expect(row.get_by_role("img", name="Unread message")).to_be_visible()
    api.direct_post(
        server, "/api/conversation/conversations/rail-chief/owner-read", {"through_sequence": 1}
    )
    expect(left.locator("[data-chief-destination] .stage-mark")).to_have_count(0, timeout=WAIT_MS)
    expect(card.locator(".item-head .stage-mark")).to_be_visible()
    expect(row.get_by_role("img", name="Unread message")).to_be_visible()
    capture_success(page, str(EVIDENCE / "own-conversation-marks.png"))
