"""Focused browser coverage for Worker management settings."""

from __future__ import annotations

import sqlite3

import httpx
from tests.e2e.conftest import WAIT_MS


def _put_stage_owner(server, ticket_id: str, stage: str, mode: str | None) -> dict:
    response = httpx.put(
        f"{server.base}/api/tickets/{ticket_id}/stage-ownership/{stage}",
        json={"ownership_mode": mode},
        timeout=10.0,
    )
    assert response.status_code < 300, response.text
    return response.json()


def _set_skill_body(page, text: str) -> None:
    page.locator("[data-skill-body-editor]").evaluate(
        """(node, text) => {
          node.replaceChildren(document.createTextNode(text));
          node.dispatchEvent(new InputEvent("input", {
            bubbles: true,
            inputType: "insertText",
            data: text
          }));
        }""",
        text,
    )


def test_workers_index_detail_and_mobile_layout(server, context_factory, open_page) -> None:
    page = open_page(
        context_factory(),
        server,
        "#/workers",
        'section[data-screen="workers"] [data-workers-list]',
        settled=False,
    )
    assert page.locator('[data-screen="workers"] [data-worker-row]').count() >= 3
    coding = page.locator('[data-worker-row][data-worker-id="coding"]')
    assert coding.locator("[data-worker-label]").inner_text() == "Coding"
    assert coding.locator("[data-worker-skill-name]").inner_text() == "panels-worker-coding"
    assert coding.locator("[data-worker-stage-count]").inner_text() == "7 Stages"

    page.click('[data-worker-row][data-worker-id="coding"]')
    page.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    assert page.locator("[data-worker-name]").inner_text() == "Coding"
    assert page.locator("[data-worker-stage-table] [data-worker-stage-row]").count() == 7
    terminal = page.locator('[data-worker-stage-row][data-stage="done"]')
    assert terminal.get_attribute("data-terminal") == "true"
    assert terminal.locator("[data-terminal-owner]").inner_text() == "terminal"
    assert (
        page.locator("[data-skill-read] [data-skill-name]").inner_text() == "panels-worker-coding"
    )
    page.locator("[data-skill-read] [data-skill-body] .markdown-block").wait_for(
        state="visible", timeout=WAIT_MS
    )

    mobile = context_factory().new_page()
    mobile.set_viewport_size({"width": 390, "height": 844})
    mobile.goto(server.base + "/#/workers/coding")
    mobile.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    assert mobile.locator("[data-stage-label]").first.is_visible()
    assert mobile.locator("[data-stage-owner-select]").first.is_visible()
    assert not mobile.locator("[data-gated-field]").first.is_visible()
    mobile.wait_for_function(
        """() => {
          const active = document.querySelector(
            '.shell-links .nav-link.active' +
            '[data-screen="workers"]'
          );
          const statuses = document.querySelector('.shell-statuses');
          if (!active || !statuses) return false;
          const activeRect = active.getBoundingClientRect();
          const statusRect = statuses.getBoundingClientRect();
          return activeRect.width > 0 && activeRect.right <= statusRect.left - 4;
        }""",
        timeout=WAIT_MS,
    )
    overflow = mobile.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0


def test_worker_stage_default_save_refreshes_without_socket_and_ticket_defaults_hold(
    server, context_factory, cli, api
) -> None:
    existing_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Existing default holds",
    )["id"]
    existing = api.get(server, f"/api/tickets/{existing_ticket}")
    assert existing["stage"] == "needs_success"
    assert existing["default_stage_ownership_mode"] == "worker"
    assert existing["effective_stage_ownership_mode"] == "worker"

    page = context_factory().new_page()
    requests: list[tuple[str, str]] = []
    page.route_web_socket("**/api/events*", lambda _socket: None)
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.goto(server.base + "/#/workers/coding")
    page.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    requests.clear()

    with page.expect_response(
        lambda response: (
            response.request.method == "PUT"
            and "/api/workers/coding/stages/needs_success/default-ownership" in response.url
        )
    ):
        page.select_option(
            '[data-worker-stage-row][data-stage="needs_success"] [data-stage-owner-select]',
            "user",
        )
    page.wait_for_function(
        """() => document.querySelector(
          '[data-worker-stage-row][data-stage="needs_success"] [data-stage-owner-select]'
        )?.value === 'user'""",
        timeout=WAIT_MS,
    )
    assert requests.count(("GET", server.base + "/api/workers/coding")) == 1
    assert requests.count(("GET", server.base + "/api/worker-types")) == 0

    unchanged = api.get(server, f"/api/tickets/{existing_ticket}")
    assert unchanged["default_stage_ownership_mode"] == "worker"
    assert unchanged["effective_stage_ownership_mode"] == "worker"

    override = _put_stage_owner(server, existing_ticket, "needs_success", "paired")
    assert override["default_stage_ownership_mode"] == "worker"
    assert override["effective_stage_ownership_mode"] == "paired"

    new_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "New default enters",
    )["id"]
    fresh = api.get(server, f"/api/tickets/{new_ticket}")
    assert fresh["stage"] == "needs_success"
    assert fresh["default_stage_ownership_mode"] == "user"
    assert fresh["effective_stage_ownership_mode"] == "user"


def test_worker_stage_failed_save_keeps_chosen_row_value(server, context_factory) -> None:
    page = context_factory().new_page()

    def fail_stage(route) -> None:
        if route.request.method == "PUT":
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"test","message":"stage save failed"}}',
            )
            return
        route.continue_()

    page.route("**/api/workers/coding/stages/needs_success/default-ownership", fail_stage)
    page.goto(server.base + "/#/workers/coding")
    page.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    selector = '[data-worker-stage-row][data-stage="needs_success"] [data-stage-owner-select]'
    page.select_option(selector, "paired")
    page.locator(
        '[data-worker-stage-row][data-stage="needs_success"] [data-stage-owner-error]'
    ).wait_for(state="visible", timeout=WAIT_MS)
    assert page.locator(selector).input_value() == "paired"
    assert (
        "stage save failed"
        in page.locator(
            '[data-worker-stage-row][data-stage="needs_success"] [data-stage-owner-error]'
        ).inner_text()
    )


def test_worker_skill_edit_candidate_save_failure_retention_and_session_stability(
    server, context_factory, cli, api
) -> None:
    rejected = httpx.put(
        f"{server.base}/api/workers/coding/skill",
        json={
            "name": "renamed-skill",
            "description": "Candidate description",
            "markdown_body": "# Candidate body\n\nDraft stays editable\n",
        },
        timeout=10.0,
    )
    assert rejected.status_code == 400

    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Worker skill session stays",
    )["id"]
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET employee_session_id = ? WHERE id = ?",
            ("skill-session-keep", ticket_id),
        )

    page = context_factory().new_page()
    page.goto(server.base + "/#/workers/coding")
    page.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    assert "Candidate description" not in page.locator("[data-skill-read]").inner_text()
    page.click("[data-skill-edit-button]")
    page.wait_for_selector("[data-skill-editor]", timeout=WAIT_MS)
    assert page.locator("[data-skill-description-input]").input_value() == "Candidate description"
    assert "Candidate body" in page.locator("[data-skill-body-editor]").inner_text()

    failed_once = True

    def fail_skill(route) -> None:
        nonlocal failed_once
        if route.request.method == "PUT" and failed_once:
            failed_once = False
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"test","message":"skill save failed"}}',
            )
            return
        route.continue_()

    page.route("**/api/workers/coding/skill", fail_skill)
    page.fill("[data-skill-description-input]", "Saved description")
    _set_skill_body(page, "# Saved body\n\nSession unchanged\n")
    page.click("[data-skill-save-button]")
    page.locator("[data-skill-error]", has_text="skill save failed").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert page.locator("[data-skill-description-input]").input_value() == "Saved description"
    assert "Saved body" in page.locator("[data-skill-body-editor]").inner_text()

    page.click("[data-skill-save-button]")
    page.wait_for_selector("[data-skill-read]", timeout=WAIT_MS)
    page.locator("[data-skill-description]", has_text="Saved description").wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.locator("[data-skill-body] .markdown-block", has_text="Saved body").wait_for(
        state="visible", timeout=WAIT_MS
    )
    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["employee_session_id"] == "skill-session-keep"
