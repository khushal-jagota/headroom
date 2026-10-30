"""One mounted proof of setup, scoped reads, exact send intent and safe refusal.

Component callbacks and HTTP tests separately cannot prove the mounted route joins
these boundaries. Reuse the existing rail host and scripted provider transport;
this case adds no browser host and makes no claim about native/provider operation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Request, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, ServerHandle
from tests.support.server_fixtures import rail_server  # noqa: F401


def _request_body(request: Request) -> dict[str, object]:
    raw = request.post_data_json
    assert isinstance(raw, dict)
    body: dict[str, object] = {}
    for key, value in raw.items():
        assert isinstance(key, str)
        body[key] = value
    return body


def test_ticket_setup_scoped_composer_and_durable_refusal(
    rail_server: ServerHandle,  # noqa: F811
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    api: ApiHelper,
    tmp_path: Path,
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
    # Backend choice previews the already-loaded destination catalog. It neither
    # rereads suggestions nor writes canonical configuration.
    observations = api.direct_post(server, "/api/test/ticket-model-catalog", {})["observations"]
    trigger.click()
    page.locator('[data-conversation-backend="claude"]').click()
    expect(setup).to_have_attribute("data-employee-configuration-backend", "claude")
    after_preview = api.get(server, detail_path)
    for key in ("record_sha256", "employee_backend", "employee_launch_model",
                "employee_launch_reasoning_effort"):
        assert after_preview[key] == before[key]
    page.locator('[data-conversation-backend="codex"]').click()
    expect(setup).to_have_attribute("data-employee-configuration-backend", "codex")
    assert api.direct_post(
        server, "/api/test/ticket-model-catalog", {}
    )["observations"] == observations
    page.locator('[data-conversation-picker-choice="gpt-browser-proof"]').click()
    with page.expect_response(lambda response: response.url.endswith(
        f"/api/tickets/{identity}/employee-configuration"
    )) as saved:
        page.locator('[data-conversation-picker-choice=""]').click()
    assert saved.value.ok, saved.value.text()
    assert _request_body(saved.value.request) == {
        "employee_backend": "codex", "employee_launch_model": "gpt-browser-proof",
        "employee_launch_reasoning_effort": None, "selection_kind": "legacy_unclassified",
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
    first_body = _request_body(first.value.request)
    # The initial launch carries its displayed model; untouched effort/kind stay absent.
    assert first_body["model"] == "gpt-browser-proof"
    assert "selection_kind" not in first_body
    assert "reasoning_effort" not in first_body
    conversation = first.value.json()["conversation_id"]
    receipts = api.get(server, f"/api/test/signals/{conversation}/delivered")["texts"]
    assert any(
        "Mounted unchanged selection reaches the provider transport." in text
        for text in receipts
    )
    api.direct_post(server, f"/api/test/signals/{conversation}/finish", {})
    # A deliberate model and Provider default effort travel as exact send intent.
    composer.locator("[data-conversation-picker-trigger]").click()
    page.locator('[data-conversation-picker-choice="gpt-browser-initial"]').click()
    page.locator('[data-conversation-picker-choice=""]').click()
    box.fill("Mounted explicit model reaches the provider transport.")
    with page.expect_response(lambda response: response.url.endswith(send_path)) as second:
        composer.locator("[data-conversation-send]").click()
    assert second.value.ok, second.value.text()
    assert second.value.json()["fate"] == "started"
    second_body = _request_body(second.value.request)
    assert second_body["model"] == "gpt-browser-initial"
    assert "selection_kind" not in second_body
    # Explicit Provider default is null; untouched effort on the first send was absent.
    assert second_body["reasoning_effort"] is None
    receipts = api.get(server, f"/api/test/signals/{conversation}/delivered")["texts"]
    assert any(
        "Mounted explicit model reaches the provider transport." in text for text in receipts
    )
    api.direct_post(server, f"/api/test/signals/{conversation}/finish", {})

    # Catalog evidence is advisory. The provider owns acceptance, and an explicit
    # provider refusal must survive the real send writer, mounted row and reload.
    api.direct_post(server, f"/api/test/signals/{conversation}/reject-next-prompt", {})
    refused_words = "These exact words survive canonical refusal and reload."
    box.fill(refused_words)
    with page.expect_response(lambda response: response.url.endswith(send_path)) as refused:
        composer.locator("[data-conversation-send]").click()
    assert refused.value.ok, refused.value.text()
    refusal = refused.value.json()
    assert refusal["fate"] == "refused"
    assert refusal["refusal_reason"] == "backend_rejected_prompt"
    assert api.get(server, f"/api/test/signals/{conversation}/delivered")["texts"] == receipts
    page.reload()
    # Opening the transcript is an explicit view choice; reload starts at the dock.
    page.locator("[data-conversation-expand]").click()
    expect(page.locator('[data-conversation-row="prompt_refused"]')).to_contain_text(refused_words)
    events = api.get(server, f"/api/conversation/conversations/{conversation}/events")["events"]
    recorded = [event for event in events if event["kind"] == "prompt_delivery_refused"]
    assert len(recorded) == 1
    assert recorded[0]["payload"]["refusal_reason"] == "backend_rejected_prompt"
    assert recorded[0]["payload"]["refusal_detail"] == "Synthetic provider refused this prompt."
    # Refresh repeats a destination-scoped advisory read, without changing settings.
    composer.locator("[data-conversation-picker-trigger]").click()
    with page.expect_response(lambda response: response.url.endswith(
        "/api/conversation/backends?refresh=true"
    )) as retry:
        page.locator("[data-conversation-picker-refresh]").click()
    assert retry.value.ok, retry.value.text()
    assert _request_body(retry.value.request) == {"ticket_id": identity}
    page.screenshot(path=str(tmp_path / "ticket-model-lifecycle.png"), full_page=True)
