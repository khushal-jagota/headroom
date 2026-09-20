"""Browser proof for the cross-layer Worker selection boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Page, Route
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle


@pytest.fixture(autouse=True)
def restore_canonical_skill_sources() -> Iterator[None]:
    """Keep the skill-edit test from leaking edits into the repository tree."""
    root = Path(__file__).resolve().parents[2] / "src" / "planner" / "skills"
    snapshots = {
        path: path.read_bytes() for path in root.glob("*/SKILL.md") if path.is_file()
    }
    yield
    for path, contents in snapshots.items():
        path.write_bytes(contents)


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


def test_chief_skill_edit_retries_the_exact_rendered_draft(
    server: ServerHandle, context_factory: Callable[[], BrowserContext], api: ApiHelper
) -> None:
    page = context_factory().new_page()
    page.goto(server.base + "/#/config/chief-of-staff")
    page.wait_for_selector('[data-agent-detail]', timeout=WAIT_MS)

    original = api.get(server, "/api/workers")["chief_of_staff"]["skill"]
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith("/api/workers/chief-of-staff/skill")
        and response.status < 300
    ):
        _replace_inline_edit_text(page, DESCRIPTION_EDIT, "Chief purpose saved")
    saved_description = api.get(server, "/api/workers")["chief_of_staff"]["skill"]
    assert saved_description["description"] == "Chief purpose saved"
    assert saved_description["markdown_body"] == original["markdown_body"]

    failed_once = True
    body_patch_payloads: list[JsonObject] = []

    def fail_first_save(route: Route) -> None:
        nonlocal failed_once
        if route.request.method == "PATCH":
            payload = route.request.post_data_json
            assert payload is not None
            body_patch_payloads.append(payload)
        if route.request.method == "PATCH" and failed_once:
            failed_once = False
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"test","message":"Chief skill save failed"}}',
            )
            return
        route.continue_()

    page.route("**/api/workers/chief-of-staff/skill", fail_first_save)
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
    assert saved["description"] == "Chief purpose saved"
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
    page = open_page(context, server, f"#/workspace/{ticket}", ready)

    row = '[data-approval-block][data-field="kickoff"] [data-approval-context-row]'
    picker = page.locator(f"{row} [data-employee-configuration-picker]")
    trigger = picker.locator("[data-conversation-picker-trigger]")
    trigger.wait_for(state="visible", timeout=WAIT_MS)
    current = page.get_attribute(
        f"{row} [data-employee-configuration-setup]",
        "data-employee-configuration-backend",
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
    saved = api.get(server, f"/api/tickets?detail=full&id={ticket}")
    assert {
        "employee_backend": saved["employee_backend"],
        "employee_launch_model": saved["employee_launch_model"],
        "employee_launch_reasoning_effort": saved[
            "employee_launch_reasoning_effort"
        ],
    } == expected
