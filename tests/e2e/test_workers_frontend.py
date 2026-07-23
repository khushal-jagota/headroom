"""Focused browser coverage for Worker management settings."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
from tests.e2e.conftest import WAIT_MS


@pytest.fixture(autouse=True)
def restore_canonical_skill_sources():
    """Keep browser skill-edit tests from leaking edits into the repository tree."""
    root = Path(__file__).resolve().parents[2] / "src" / "planner" / "skills"
    snapshots = {
        path: path.read_bytes()
        for path in root.glob("*/SKILL.md")
        if path.is_file()
    }
    yield
    for path, contents in snapshots.items():
        path.write_bytes(contents)


def _put_stage_owner(server, ticket_id: str, stage: str, mode: str | None) -> dict:
    response = httpx.put(
        f"{server.base}/api/tickets/{ticket_id}/stage-ownership/{stage}",
        json={"ownership_mode": mode},
        timeout=10.0,
    )
    assert response.status_code < 300, response.text
    return response.json()


DESCRIPTION_EDIT = '[data-skill-description] [contenteditable="true"]'
BODY_EDIT = '[data-skill-body] [contenteditable="true"]'


def _replace_inline_edit_text(page, selector: str, text: str) -> None:
    page.locator(selector).click()
    page.locator(selector).evaluate(
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
    page.locator("[data-worker-name]").click()


def _replace_inline_edit_markdown(page, selector: str, source: str) -> None:
    page.locator(selector).click()
    page.locator(selector).evaluate(
        r"""(node, source) => {
          const lines = source.split("\n");
          const block = document.createElement("div");
          block.className = "markdown-block";
          const heading = document.createElement("h1");
          heading.textContent = lines.shift().replace(/^# /, "");
          block.appendChild(heading);
          while (lines[0] === "") lines.shift();
          const paragraph = document.createElement("p");
          paragraph.textContent = lines.join("\n");
          block.appendChild(paragraph);
          node.replaceChildren(block);
          node.dispatchEvent(new InputEvent("input", {
            bubbles: true,
            inputType: "insertText",
            data: source
          }));
        }""",
        source,
    )
    page.locator("[data-worker-name]").click()


def _editable_text(page, selector: str) -> str:
    return page.locator(selector).evaluate("(node) => node.textContent")


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
        page.locator("[data-skill-content] [data-skill-name]").inner_text()
        == "panels-worker-coding"
    )
    assert page.locator("[data-skill-edit-button]").count() == 0
    assert page.locator("[data-skill-save-button]").count() == 0
    assert page.locator("[data-skill-cancel-button]").count() == 0
    assert page.locator(DESCRIPTION_EDIT).get_attribute("contenteditable") == "true"
    assert page.locator(BODY_EDIT).get_attribute("contenteditable") == "true"
    assert page.locator("[data-skill-name]").get_attribute("contenteditable") != "true"

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


def test_worker_selection_persists_from_kickoff_card_context_row(
    server, context_factory, open_page, cli, api
) -> None:
    ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Worker choice in card",
        "--kickoff-note",
        "Choose the worker",
    )["id"]
    ready = f'section[data-screen="ticket"][data-ticket-id="{ticket}"]'
    page = open_page(context_factory(), server, f"#/ticket/{ticket}", ready, settled=True)

    # The worker pills live inside the Kickoff approval card's context row.
    row = '[data-approval-block][data-field="kickoff"] [data-approval-context-row]'
    selector = f"{row} [data-employee-configuration-worker] select"
    page.wait_for_selector(selector, timeout=WAIT_MS)
    current = page.get_attribute(
        f"{row} [data-employee-configuration-setup]", "data-employee-configuration-backend"
    )
    target = "codex" if current != "codex" else "claude"
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url.endswith(f"/api/tickets/{ticket}/employee-configuration")
        and response.status < 300
    ):
        page.select_option(selector, target)
    page.wait_for_selector(
        f"{row} [data-employee-configuration-setup]"
        f'[data-employee-configuration-backend="{target}"]',
        timeout=WAIT_MS,
    )
    assert api.get(server, f"/api/tickets/{ticket}")["employee_backend"] == target


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


def test_worker_skill_edit_save_failure_and_session_stability(
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
    assert page.locator("[data-skill-edit-button]").count() == 0
    assert page.locator("[data-skill-save-button]").count() == 0
    assert page.locator("[data-skill-cancel-button]").count() == 0
    assert page.locator(DESCRIPTION_EDIT).get_attribute("contenteditable") == "true"
    assert page.locator(BODY_EDIT).get_attribute("contenteditable") == "true"
    assert page.locator("[data-skill-name]").inner_text() == "panels-worker-coding"
    assert page.locator("[data-skill-name]").get_attribute("contenteditable") != "true"
    canonical = api.get(server, "/api/workers/coding")["settings"]["specialist_skill"]
    assert _editable_text(page, DESCRIPTION_EDIT) == canonical["description"]
    assert "Candidate description" not in _editable_text(page, DESCRIPTION_EDIT)
    assert "Candidate body" not in _editable_text(page, BODY_EDIT)

    original = canonical
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith("/api/workers/coding/skill")
    ):
        _replace_inline_edit_text(page, DESCRIPTION_EDIT, "Saved description")
    after_description = api.get(server, "/api/workers/coding")["settings"]["specialist_skill"]
    assert after_description["description"] == "Saved description"
    assert after_description["markdown_body"] == original["markdown_body"]

    failed_once = True
    body_patch_payloads: list[dict] = []

    def fail_skill(route) -> None:
        nonlocal failed_once
        if route.request.method == "PATCH":
            body_patch_payloads.append(route.request.post_data_json)
        if route.request.method == "PATCH" and failed_once:
            failed_once = False
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"test","message":"skill save failed"}}',
            )
            return
        route.continue_()

    page.route("**/api/workers/coding/skill", fail_skill)

    attempted_body = "# Saved body\n\nSession unchanged exact source"
    _replace_inline_edit_markdown(page, BODY_EDIT, attempted_body)
    page.locator("[data-skill-body] .error-line", has_text="skill save failed").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert body_patch_payloads == [{"markdown_body": attempted_body}]

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith("/api/workers/coding/skill")
        and response.status < 300
    ):
        page.locator(BODY_EDIT).click()
        page.locator("[data-worker-name]").click()
    assert body_patch_payloads == [
        {"markdown_body": attempted_body},
        {"markdown_body": attempted_body},
    ]
    saved = api.get(server, "/api/workers/coding")["settings"]["specialist_skill"]
    assert saved["description"] == "Saved description"
    assert saved["markdown_body"] == f"\n{attempted_body}\n"
    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["employee_session_id"] == "skill-session-keep"
