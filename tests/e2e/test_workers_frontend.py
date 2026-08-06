"""Focused browser coverage for Worker management settings."""

from __future__ import annotations

import sqlite3
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

PICKER_BACKENDS = {
    "backends": [
        {
            "backend_key": "claude",
            "installed": True,
            "executable_path": "/fixture/claude",
            "version": "1",
            "identity": None,
            "available_models": [
                {
                    "model_id": "claude-fixture",
                    "display_name": "Claude fixture",
                    "reasoning_effort_options": [],
                }
            ],
            "reasoning_effort_options": [],
            "default_model_id": "claude-fixture",
            "default_reasoning_effort": None,
            "update_advisory": None,
            "diagnoses": [],
        },
        {
            "backend_key": "codex",
            "installed": True,
            "executable_path": "/fixture/codex",
            "version": "1",
            "identity": None,
            "available_models": [
                {
                    "model_id": "codex-fixture",
                    "display_name": "Codex fixture",
                    "reasoning_effort_options": ["low", "high"],
                }
            ],
            "reasoning_effort_options": ["low", "high"],
            "default_model_id": "codex-fixture",
            "default_reasoning_effort": "high",
            "update_advisory": None,
            "diagnoses": [],
        },
        {
            "backend_key": "hermes",
            "installed": True,
            "executable_path": "/fixture/hermes",
            "version": "1",
            "identity": None,
            "available_models": [
                {
                    "model_id": "hermes-fixture",
                    "display_name": "Hermes fixture",
                    "reasoning_effort_options": [],
                }
            ],
            "reasoning_effort_options": [],
            "default_model_id": "hermes-fixture",
            "default_reasoning_effort": None,
            "update_advisory": None,
            "diagnoses": [],
        },
    ]
}


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
    context = context_factory()
    context.route(
        "**/api/conversation/backends**",
        lambda route: route.fulfill(json=PICKER_BACKENDS),
    )
    page = open_page(context, server, f"#/ticket/{ticket}", ready)

    # The unified picker lives inside the Kickoff approval card's context row.
    row = '[data-approval-block][data-field="kickoff"] [data-approval-context-row]'
    picker = page.locator(f"{row} [data-employee-configuration-picker]")
    trigger = picker.locator("[data-conversation-picker-trigger]")
    trigger.wait_for(state="visible", timeout=WAIT_MS)
    current = page.get_attribute(
        f"{row} [data-employee-configuration-setup]", "data-employee-configuration-backend"
    )
    target = "codex" if current != "codex" else "claude"
    expected = (
        {
            "employee_backend": "codex",
            "employee_launch_model": "codex-fixture",
            "employee_launch_reasoning_effort": "high",
        }
        if target == "codex"
        else {
            "employee_backend": "claude",
            "employee_launch_model": "claude-fixture",
            "employee_launch_reasoning_effort": None,
        }
    )

    trigger.click()
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url.endswith(f"/api/tickets/{ticket}/employee-configuration")
        and response.status < 300
    ) as saved_response:
        picker.locator(f'[data-conversation-backend="{target}"]').click()
    assert saved_response.value.request.post_data_json == expected
    page.wait_for_selector(
        f"{row} [data-employee-configuration-setup]"
        f'[data-employee-configuration-backend="{target}"]',
        timeout=WAIT_MS,
    )
    saved = api.get(server, f"/api/tickets/{ticket}")
    assert {
        "employee_backend": saved["employee_backend"],
        "employee_launch_model": saved["employee_launch_model"],
        "employee_launch_reasoning_effort": saved[
            "employee_launch_reasoning_effort"
        ],
    } == expected


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


def test_worker_kickoff_ceiling_suggestion_uses_manifest_options_and_saves(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    api: ApiHelper,
) -> None:
    page = context_factory().new_page()
    page.goto(server.base + "/#/config/workers/coding")
    page.wait_for_selector('[data-worker-detail][data-worker-id="coding"]', timeout=WAIT_MS)
    selector = "[data-suggested-next-ceiling]"
    picker = page.locator(selector)
    assert picker.input_value() == "needs_success"
    assert picker.locator('option[value="needs_kickoff"]').count() == 0
    assert picker.locator('option[value="none"]').count() == 0
    assert picker.locator('option[value="done"]').count() == 1

    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url.endswith("/api/workers/coding/suggested-next-ceiling")
    ):
        picker.select_option("needs_plan")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.value === 'needs_plan'",
        arg=selector,
        timeout=WAIT_MS,
    )
    assert api.get(server, "/api/workers/coding")["settings"][
        "suggested_next_ceiling"
    ] == "needs_plan"


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
