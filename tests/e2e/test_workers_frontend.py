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
ARTIFACT_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "files"
    / "tickets"
    / "t_fvrfhk2k"
    / "artifacts"
)


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
    page.locator("[data-role-name]").click()


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
    page.locator("[data-role-name]").click()


def _editable_text(page, selector: str) -> str:
    return page.locator(selector).evaluate("(node) => node.textContent")


def _assert_agents_nav_active_and_clear(page) -> None:
    nav = page.locator('.shell-links .nav-link[data-screen="agents"]')
    assert nav.inner_text() == "Agents"
    assert nav.get_attribute("href") == "#/agents"
    assert "active" in (nav.get_attribute("class") or "").split()
    page.wait_for_function(
        """() => {
          const active = document.querySelector(
            '.shell-links .nav-link.active[data-screen="agents"]'
          );
          const statuses = document.querySelector('.shell-statuses');
          if (!active || !statuses) return false;
          const activeRect = active.getBoundingClientRect();
          const statusRect = statuses.getBoundingClientRect();
          return activeRect.width > 0 && activeRect.right <= statusRect.left - 4;
        }""",
        timeout=WAIT_MS,
    )


def _assert_no_horizontal_overflow(page) -> None:
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0


def test_agents_routes_navigation_and_mobile_controls(server, context_factory) -> None:
    legacy_index = context_factory().new_page()
    legacy_index.goto(server.base + "/#/workers")
    legacy_index.wait_for_url(server.base + "/#/agents", timeout=WAIT_MS)
    legacy_index.wait_for_selector('[data-screen="agents"] [data-agents-section]', timeout=WAIT_MS)
    _assert_agents_nav_active_and_clear(legacy_index)

    legacy_worker = context_factory().new_page()
    legacy_worker.goto(server.base + "/#/workers/coding")
    legacy_worker.wait_for_url(server.base + "/#/agents/workers/coding", timeout=WAIT_MS)
    legacy_worker.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    _assert_agents_nav_active_and_clear(legacy_worker)

    for invalid_hash in ("#/agents/not-a-role", "#/agents/workers"):
        invalid = context_factory().new_page()
        invalid.goto(server.base + f"/{invalid_hash}")
        invalid.wait_for_selector(".quiet-line", timeout=WAIT_MS)
        assert invalid.locator(".quiet-line").inner_text() == "no such screen"

    mobile_index = context_factory().new_page()
    mobile_index.set_viewport_size({"width": 390, "height": 844})
    mobile_index.goto(server.base + "/#/agents")
    mobile_index.wait_for_selector("[data-agent-configure]", timeout=WAIT_MS)
    _assert_agents_nav_active_and_clear(mobile_index)
    _assert_no_horizontal_overflow(mobile_index)
    assert mobile_index.locator("[data-agent-configure]").is_visible()
    assert mobile_index.locator("[data-agent-configure]").get_attribute("href") == (
        "#/agents/chief-of-staff"
    )
    assert mobile_index.locator('[data-worker-row][data-worker-id="coding"]').is_visible()

    mobile_index.locator("[data-agent-configure]").click()
    mobile_index.wait_for_url(server.base + "/#/agents/chief-of-staff", timeout=WAIT_MS)
    mobile_index.wait_for_selector("[data-agent-detail]", timeout=WAIT_MS)
    _assert_agents_nav_active_and_clear(mobile_index)
    _assert_no_horizontal_overflow(mobile_index)
    assert mobile_index.get_by_label("Chief of Staff backend").is_visible()
    assert mobile_index.locator(DESCRIPTION_EDIT).is_editable()
    assert mobile_index.locator(BODY_EDIT).is_editable()


def test_agents_index_worker_detail_and_mobile_layout(server, context_factory, open_page) -> None:
    page = open_page(
        context_factory(),
        server,
        "#/agents",
        'section[data-screen="agents"] [data-workers-list]',
        settled=False,
    )
    assert page.locator('[data-screen="agents"] [data-agents-section]').count() == 1
    assert page.locator('[data-screen="agents"] [data-workers-section]').count() == 1
    assert page.locator('[data-screen="agents"] [data-worker-row]').count() >= 3
    assert page.locator("[data-skills-home]").count() == 0
    chief = page.locator('[data-agent-card][data-agent-id="chief_of_staff"]')
    assert chief.locator("[data-agent-label]").inner_text() == "Chief of Staff"
    assert chief.locator("[data-agent-purpose]").inner_text().strip()
    assert chief.locator("[data-agent-skill-name]").inner_text() == "panels-chief-of-staff"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=ARTIFACT_DIR / "agents-page-desktop.png", full_page=True)
    coding = page.locator('[data-worker-row][data-worker-id="coding"]')
    assert coding.locator("[data-worker-label]").inner_text() == "Coding"
    assert coding.locator("[data-worker-skill-name]").inner_text() == "panels-worker-coding"
    assert coding.locator("[data-worker-stage-count]").inner_text() == "7 Stages"

    page.click('[data-worker-row][data-worker-id="coding"]')
    page.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    assert page.locator("[data-role-name]").inner_text() == "Coding"
    assert page.url.endswith("#/agents/workers/coding")
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
    mobile.goto(server.base + "/#/agents/workers/coding")
    mobile.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    assert mobile.locator("[data-stage-label]").first.is_visible()
    assert mobile.locator("[data-stage-owner-select]").first.is_visible()
    assert not mobile.locator("[data-gated-field]").first.is_visible()
    _assert_agents_nav_active_and_clear(mobile)
    _assert_no_horizontal_overflow(mobile)
    mobile.screenshot(path=ARTIFACT_DIR / "agents-worker-detail-mobile.png", full_page=True)


def test_chief_detail_edits_skill_independently_and_retries_failure(
    server, context_factory, api
) -> None:
    page = context_factory().new_page()
    requests: list[tuple[str, str]] = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.goto(server.base + "/#/agents/chief-of-staff")
    page.wait_for_selector('[data-agent-detail]', timeout=WAIT_MS)

    assert page.locator("[data-role-name]").inner_text() == "Chief of Staff"
    assert page.locator("[data-role-purpose]").inner_text().strip()
    assert page.locator("[data-worker-stage-table]").count() == 0
    assert page.locator("[data-skill-name]").inner_text() == "panels-chief-of-staff"
    assert page.locator("[data-skill-name]").get_attribute("contenteditable") != "true"
    assert page.locator(DESCRIPTION_EDIT).get_attribute("contenteditable") == "true"
    assert page.locator(BODY_EDIT).get_attribute("contenteditable") == "true"
    assert ("GET", server.base + "/api/workers") in requests
    assert not any(
        method == "GET" and "/api/workers/chief_of_staff" in url
        for method, url in requests
    )

    original = api.get(server, "/api/workers")["chief_of_staff"]["skill"]
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith("/api/workers/chief-of-staff/skill")
        and response.status < 300
    ):
        _replace_inline_edit_text(page, DESCRIPTION_EDIT, "Chief purpose saved independently")
    after_description = api.get(server, "/api/workers")["chief_of_staff"]["skill"]
    assert after_description["description"] == "Chief purpose saved independently"
    assert after_description["markdown_body"] == original["markdown_body"]

    failed_once = True
    body_patch_payloads: list[dict] = []

    def fail_chief_skill(route) -> None:
        nonlocal failed_once
        if route.request.method == "PATCH":
            body_patch_payloads.append(route.request.post_data_json)
        if route.request.method == "PATCH" and failed_once:
            failed_once = False
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"test","message":"Chief skill save failed"}}',
            )
            return
        route.continue_()

    page.route("**/api/workers/chief-of-staff/skill", fail_chief_skill)
    attempted_body = "# Chief retry\n\nPreserve this exact draft"
    _replace_inline_edit_markdown(page, BODY_EDIT, attempted_body)
    page.locator("[data-skill-body] .error-line", has_text="Chief skill save failed").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert body_patch_payloads == [{"markdown_body": attempted_body}]

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith("/api/workers/chief-of-staff/skill")
        and response.status < 300
    ):
        page.locator(BODY_EDIT).click()
        page.locator("[data-role-name]").click()
    assert body_patch_payloads == [
        {"markdown_body": attempted_body},
        {"markdown_body": attempted_body},
    ]
    saved = api.get(server, "/api/workers")["chief_of_staff"]["skill"]
    assert saved["description"] == "Chief purpose saved independently"
    assert saved["markdown_body"] == f"\n{attempted_body}\n"


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
    page.goto(server.base + "/#/agents/workers/coding")
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
    page.goto(server.base + "/#/agents/workers/coding")
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
    page.goto(server.base + "/#/agents/workers/coding")
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
        page.locator("[data-role-name]").click()
    assert body_patch_payloads == [
        {"markdown_body": attempted_body},
        {"markdown_body": attempted_body},
    ]
    saved = api.get(server, "/api/workers/coding")["settings"]["specialist_skill"]
    assert saved["description"] == "Saved description"
    assert saved["markdown_body"] == f"\n{attempted_body}\n"
    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["employee_session_id"] == "skill-session-keep"
