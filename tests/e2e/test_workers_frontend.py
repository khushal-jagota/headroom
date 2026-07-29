"""Focused browser coverage for Worker management settings."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import BrowserContext, Page, Route
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle


@pytest.fixture(autouse=True)
def restore_canonical_skill_sources() -> Iterator[None]:
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


def _put_stage_owner(
    server: ServerHandle, ticket_id: str, stage: str, mode: str | None
) -> JsonObject:
    response = httpx.put(
        f"{server.base}/api/tickets/{ticket_id}/stage-ownership/{stage}",
        json={"ownership_mode": mode},
        timeout=10.0,
    )
    assert response.status_code < 300, response.text
    answer: JsonObject = response.json()
    return answer


DESCRIPTION_EDIT = '[data-skill-description] [contenteditable="true"]'
BODY_EDIT = '[data-skill-body] [contenteditable="true"]'


def _replace_inline_edit_text(page: Page, selector: str, text: str) -> None:
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


def _replace_inline_edit_markdown(page: Page, selector: str, source: str) -> None:
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


def _editable_text(page: Page, selector: str) -> str:
    text: str = page.locator(selector).evaluate("(node) => node.textContent")
    return text


def _assert_config_more_active(page: Page) -> None:
    nav = page.locator('.shell-links .nav-link[data-screen="more"]')
    assert nav.inner_text().strip() == "More"
    assert "active" in (nav.get_attribute("class") or "").split()


def _assert_no_horizontal_overflow(page: Page) -> None:
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0


def test_agents_index_renders_when_the_workers_read_answers_last(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    """The Agents index waits on two reads, and the order they answer in is a race.

    Held here so the read the screen looks at first is the last one to answer. A screen is
    only told about the parts of a read it has already looked at, so a gate that stops at
    the first unfinished read never looks at the other one, is never told when it answers,
    and stays on its loading line for good.
    """
    page = context_factory().new_page()
    # No change stream: its connect handler refetches everything, which would wake a
    # screen that had stopped listening and hide the failure this test is for.
    page.route("**/api/changes", lambda route: route.abort())

    def answer_after_the_others(route: Route) -> None:
        time.sleep(0.5)
        route.continue_()

    page.route("**/api/workers", answer_after_the_others)
    page.goto(server.base + "/#/config")
    page.wait_for_selector(
        '[data-screen="config"] [data-workers-list] [data-worker-destination]', timeout=WAIT_MS
    )


def test_chief_detail_edits_skill_independently_and_retries_failure(
    server: ServerHandle, context_factory: Callable[[], BrowserContext], api: ApiHelper
) -> None:
    page = context_factory().new_page()
    requests: list[tuple[str, str]] = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.goto(server.base + "/#/config/chief-of-staff")
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
    body_patch_payloads: list[JsonObject] = []

    def fail_chief_skill(route: Route) -> None:
        nonlocal failed_once
        if route.request.method == "PATCH":
            post_data_json = route.request.post_data_json
            assert post_data_json is not None
            body_patch_payloads.append(post_data_json)
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


def test_shared_worker_skill_detail_edits_independently_and_retries_failure(
    server: ServerHandle, context_factory: Callable[[], BrowserContext], api: ApiHelper
) -> None:
    page = context_factory().new_page()
    requests: list[tuple[str, str]] = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.goto(server.base + "/#/config/worker-skill")
    page.wait_for_selector(
        '[data-agent-detail][data-agent-id="panels-worker"]', timeout=WAIT_MS
    )

    assert page.url.endswith("#/config/worker-skill")
    assert page.locator("[data-role-name]").inner_text() == "Worker skill"
    assert page.locator("[data-role-purpose]").inner_text().strip()
    assert page.locator("[data-launch-defaults]").count() == 0
    assert page.locator("[data-worker-stage-table]").count() == 0
    assert page.locator("[data-skill-name]").inner_text() == "panels-worker"
    assert page.locator("[data-skill-name]").get_attribute("contenteditable") != "true"
    assert page.locator(DESCRIPTION_EDIT).get_attribute("contenteditable") == "true"
    assert page.locator(BODY_EDIT).get_attribute("contenteditable") == "true"
    assert ("GET", server.base + "/api/skills") in requests
    assert not any("/api/workers/panels-worker" in url for _method, url in requests)

    original = next(
        skill
        for skill in api.get(server, "/api/skills")["skills"]
        if skill["name"] == "panels-worker"
    )
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith("/api/skills/panels-worker")
        and response.status < 300
    ):
        _replace_inline_edit_text(
            page, DESCRIPTION_EDIT, "Shared Worker purpose saved independently"
        )
    after_description = next(
        skill
        for skill in api.get(server, "/api/skills")["skills"]
        if skill["name"] == "panels-worker"
    )
    assert after_description["description"] == "Shared Worker purpose saved independently"
    assert after_description["markdown_body"] == original["markdown_body"]

    failed_once = True
    body_patch_payloads: list[JsonObject] = []

    def fail_shared_worker_skill(route: Route) -> None:
        nonlocal failed_once
        if route.request.method == "PATCH":
            post_data_json = route.request.post_data_json
            assert post_data_json is not None
            body_patch_payloads.append(post_data_json)
        if route.request.method == "PATCH" and failed_once:
            failed_once = False
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"test","message":"Worker skill save failed"}}',
            )
            return
        route.continue_()

    page.route("**/api/skills/panels-worker", fail_shared_worker_skill)
    attempted_body = "# Shared Worker retry\n\nPreserve this exact draft"
    _replace_inline_edit_markdown(page, BODY_EDIT, attempted_body)
    page.locator("[data-skill-body] .error-line", has_text="Worker skill save failed").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert body_patch_payloads == [{"markdown_body": attempted_body}]

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith("/api/skills/panels-worker")
        and response.status < 300
    ):
        page.locator(BODY_EDIT).click()
        page.locator("[data-role-name]").click()
    assert body_patch_payloads == [
        {"markdown_body": attempted_body},
        {"markdown_body": attempted_body},
    ]
    saved = next(
        skill
        for skill in api.get(server, "/api/skills")["skills"]
        if skill["name"] == "panels-worker"
    )
    assert saved["description"] == "Shared Worker purpose saved independently"
    assert saved["markdown_body"] == f"\n{attempted_body}\n"

    mobile = context_factory().new_page()
    mobile.set_viewport_size({"width": 390, "height": 844})
    mobile.goto(server.base + "/#/config/worker-skill")
    mobile.wait_for_selector("[data-shared-worker-skill]", timeout=WAIT_MS)
    _assert_config_more_active(mobile)
    _assert_no_horizontal_overflow(mobile)
    assert mobile.locator(DESCRIPTION_EDIT).is_editable()
    assert mobile.locator(BODY_EDIT).is_editable()


def test_worker_selection_persists_from_kickoff_card_context_row(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
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
    page = open_page(context_factory(), server, f"#/ticket/{ticket}", ready)

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


def test_worker_stage_default_save_refreshes_without_change_stream_and_ticket_defaults_hold(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
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
    # Block the change stream: the save must refresh the screen on its own.
    page.route("**/api/changes", lambda route: route.abort())
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.goto(server.base + "/#/config/workers/coding")
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


def test_worker_stage_failed_save_keeps_chosen_row_value(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()

    def fail_stage(route: Route) -> None:
        if route.request.method == "PUT":
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"test","message":"stage save failed"}}',
            )
            return
        route.continue_()

    page.route("**/api/workers/coding/stages/needs_success/default-ownership", fail_stage)
    page.goto(server.base + "/#/config/workers/coding")
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
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
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
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            ("skill-session-keep", ticket_id),
        )

    page = context_factory().new_page()
    page.goto(server.base + "/#/config/workers/coding")
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
    body_patch_payloads: list[JsonObject] = []

    def fail_skill(route: Route) -> None:
        nonlocal failed_once
        if route.request.method == "PATCH":
            post_data_json = route.request.post_data_json
            assert post_data_json is not None
            body_patch_payloads.append(post_data_json)
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
    assert detail["conversation_id"] == "skill-session-keep"
