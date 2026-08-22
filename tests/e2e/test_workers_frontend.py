"""Browser proof for the cross-layer Worker selection boundary."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

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
    saved = api.get(server, f"/api/tickets/{ticket}")
    assert {
        "employee_backend": saved["employee_backend"],
        "employee_launch_model": saved["employee_launch_model"],
        "employee_launch_reasoning_effort": saved[
            "employee_launch_reasoning_effort"
        ],
    } == expected
