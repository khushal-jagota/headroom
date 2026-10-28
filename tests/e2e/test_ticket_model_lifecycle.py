"""One mounted proof of setup, scoped reads, exact send intent and safe refusal.

Component callbacks and HTTP tests separately cannot prove the mounted route joins
these boundaries. Reuse the existing rail host and scripted provider transport;
this case adds no browser host and makes no claim about native/provider operation.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle
from tests.support.server_fixtures import rail_server  # noqa: F401


def test_ticket_setup_scoped_composer_and_durable_refusal(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
) -> None:
    server = rail_server
    api.direct_post(server, "/api/test/ticket-model-catalog", {})
    project = api.direct_post(server, "/api/projects", {"name": "Scoped browser proof"})
    ticket = api.direct_post(server, "/api/tickets", {
        "title": "Exact mounted model intent", "worker_type": "coding",
        "brief": "Offline synthetic provider proof.", "project_id": project["id"],
        "manager": {"kind": "chief", "id": "chief"}, "locale": "vps",
        "employee_backend": "codex", "employee_launch_model": "gpt-browser-initial",
        "employee_launch_reasoning_effort": None, "selection_kind": "concrete",
    })
    identity = str(ticket["id"])
    detail_path = f"/api/tickets?detail=full&id={identity}"
    page = open_page(context_factory(), server, f"#/workspace/{identity}",
                     "[data-employee-configuration-setup]")
    page.set_default_timeout(WAIT_MS)
    setup = page.locator("[data-employee-configuration-setup]")
    picker = setup.locator("[data-employee-configuration-picker]")
    trigger = picker.locator("[data-conversation-picker-trigger]")
    expect(trigger).to_be_enabled()
    before = api.get(server, detail_path)
    trigger.click()
    with page.expect_response(lambda response: response.url.endswith(
        "/api/conversation/backends/model-choices"
    ) and response.request.post_data_json.get("employee_backend") == "claude") as preview:
        page.locator('[data-conversation-backend="claude"]').click()
    assert preview.value.ok, preview.value.text()
    assert preview.value.request.post_data_json == {"ticket_id": identity, "employee_backend": "claude"}
    after_preview = api.get(server, detail_path)
    for key in ("record_sha256", "employee_backend", "employee_launch_model",
                "employee_launch_reasoning_effort"):
        assert after_preview[key] == before[key]
    with page.expect_response(lambda response: response.url.endswith(
        "/api/conversation/backends/model-choices"
    ) and response.request.post_data_json.get("employee_backend") == "codex"):
        page.locator('[data-conversation-backend="codex"]').click()
    with page.expect_response(lambda response: response.url.endswith(
        f"/api/tickets/{identity}/employee-configuration"
    )) as saved:
        page.locator('[data-conversation-picker-choice="gpt-browser-proof"]').click()
    assert saved.value.ok, saved.value.text()
    assert saved.value.request.post_data_json == {
        "employee_backend": "codex", "employee_launch_model": "gpt-browser-proof",
        "employee_launch_reasoning_effort": None, "selection_kind": "concrete",
    }
    page.reload()
    expect(setup).to_have_attribute("data-employee-configuration-model", "gpt-browser-proof")
    expect(setup).to_have_attribute("data-employee-configuration-reasoning", "")
    page.locator("[data-conversation-expand]").click()
    composer = page.locator("[data-conversation-composer]")
    box = composer.locator("[data-conversation-input]")
    expect(box).to_be_visible()
    box.fill("Mounted unchanged selection reaches the provider transport.")
    send_path = f"/api/tickets/{identity}/conversation/send"
    with page.expect_response(lambda response: response.url.endswith(send_path)) as first:
        composer.locator("[data-conversation-send]").click()
    assert first.value.ok, first.value.text()
    assert first.value.json()["fate"] == "started"
    first_body = first.value.request.post_data_json
    assert "model" not in first_body
    assert "selection_kind" not in first_body
    assert "reasoning_effort" not in first_body
    conversation = first.value.json()["conversation_id"]
    receipts = api.get(server, f"/api/test/signals/{conversation}/delivered")["texts"]
    assert any("Mounted unchanged selection reaches the provider transport." in text for text in receipts)
    api.direct_post(server, f"/api/test/signals/{conversation}/finish", {})
    # A deliberate typed row selection is intent even with the same submitted token.
    composer.locator("[data-conversation-picker-trigger]").click()
    page.locator('[data-conversation-picker-choice="gpt-browser-proof"]').click()
    box.fill("Mounted explicit kind reaches the provider transport.")
    with page.expect_response(lambda response: response.url.endswith(send_path)) as second:
        composer.locator("[data-conversation-send]").click()
    assert second.value.ok, second.value.text()
    assert second.value.json()["fate"] == "started"
    second_body = second.value.request.post_data_json
    assert second_body["model"] == "gpt-browser-proof"
    assert second_body["selection_kind"] == "concrete"
    # A model-only choice preserves effort presence; default null stays omitted.
    assert "reasoning_effort" not in second_body
    receipts = api.get(server, f"/api/test/signals/{conversation}/delivered")["texts"]
    assert any("Mounted explicit kind reaches the provider transport." in text for text in receipts)
    api.direct_post(server, f"/api/test/signals/{conversation}/finish", {})

    # Affirmative credential unavailability is safe structured evidence, not absence
    # misrepresented as incompatibility. The real send performs another scoped read.
    api.direct_post(server, "/api/test/ticket-model-catalog", {"credentials_unavailable": True})
    refused_words = "These exact words survive canonical refusal and reload."
    box.fill(refused_words)
    with page.expect_response(lambda response: response.url.endswith(send_path)) as refused:
        composer.locator("[data-conversation-send]").click()
    assert refused.value.ok, refused.value.text()
    refusal = refused.value.json()
    assert refusal["fate"] == "refused"
    assert refusal["model_validation"]["reason"] == "credentials_unavailable"
    assert refusal["model_validation"]["corrective_action"] == "authenticate_provider"
    assert api.get(server, f"/api/test/signals/{conversation}/delivered")["texts"] == receipts
    page.reload()
    expect(page.locator('[data-conversation-row="prompt_refused"]')).to_contain_text(refused_words)
    composer.locator("[data-conversation-picker-trigger]").click()
    page.locator("[data-model-picker-why]").click()
    expect(page.locator(".model-picker-detail")).to_contain_text("credentials_unavailable")
    expect(page.locator(".model-picker-detail")).to_contain_text("Required action: authenticate provider")
    expect(page.locator(".model-picker-detail")).to_contain_text("Check again repeats the same read")
    with page.expect_response(lambda response: response.url.endswith(
        "/api/conversation/backends/model-choices"
    )) as retry:
        page.locator("[data-model-picker-check]").click()
    assert retry.value.ok, retry.value.text()
    assert retry.value.json()["validation"]["corrective_action"] == "authenticate_provider"
