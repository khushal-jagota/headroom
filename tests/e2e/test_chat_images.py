"""Browser coverage for image-only chat attachments in the shared composer."""

from __future__ import annotations

import base64

from playwright.sync_api import Page

WAIT_MS = 10_000
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLv"
    "AAAAAElFTkSuQmCC"
)


def _image_file(name: str = "chat-image.png") -> dict[str, object]:
    return {"name": name, "mimeType": "image/png", "buffer": PNG}


def _wait_chat_text(page: Page, who: str, text: str) -> None:
    page.wait_for_function(
        "({ who, text }) => Array.from(document.querySelectorAll(`[data-chat-msg=\"${who}\"]`))"
        ".some(el => el.textContent.includes(text))",
        arg={"who": who, "text": text},
        timeout=WAIT_MS,
    )


def _wait_image_geometry(page: Page, scope: str) -> dict[str, object]:
    selector = f"{scope} [data-chat-msg='you'] [data-file-preview-kind='image'] img"
    page.wait_for_function(
        "selector => { const images = document.querySelectorAll(selector); "
        "const image = images[images.length - 1]; if (!image) return false; "
        "const rect = image.getBoundingClientRect(); "
        "return image.complete && image.naturalWidth > 0 && image.naturalHeight > 0 "
        "&& rect.width > 0 && rect.height > 0; }",
        arg=selector,
        timeout=WAIT_MS,
    )
    return page.locator(selector).last.evaluate(
        "image => { const rect = image.getBoundingClientRect(); return {"
        "src: image.getAttribute('src'), naturalWidth: image.naturalWidth, "
        "naturalHeight: image.naturalHeight, width: rect.width, height: rect.height }; }"
    )


def test_chat_image_control_is_directly_beside_slash_and_text_only_send_still_works(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Image composer placement")["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )

    footer = page.locator("[data-chat] .chat-foot")
    assert footer.locator(":scope > [data-chat-slash] + [data-chat-image]").count() == 1
    assert footer.locator(":scope > [data-chat-image-input][type='file']").count() == 1
    assert footer.locator("[data-chat-image-input]").get_attribute("accept") == "image/*"
    positions = footer.locator(
        ":scope > [data-chat-slash], :scope > [data-chat-image]"
    ).evaluate_all(
        "buttons => buttons.map(button => { const rect = button.getBoundingClientRect(); "
        "return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }; })"
    )
    assert len(positions) == 2
    assert positions[0]["left"] < positions[1]["left"]
    assert 0 <= positions[1]["left"] - positions[0]["right"] <= 8
    assert positions[0]["top"] < positions[1]["bottom"]
    assert positions[1]["top"] < positions[0]["bottom"]

    page.fill("[data-chat] [data-chat-input]", "ordinary text is unchanged")
    page.click("[data-chat] [data-chat-send]")
    _wait_chat_text(page, "you", "ordinary text is unchanged")
    _wait_chat_text(page, "planner", "echo: ordinary text is unchanged")
    state = api.get(server, f"/api/chat/{ticket_id}/state")
    assert [message["text"] for message in state["messages"]] == [
        "ordinary text is unchanged",
        "echo: ordinary text is unchanged",
    ]


def test_pending_image_does_not_change_slash_command_behavior(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Image and slash command")["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )
    writes: list[tuple[str, dict]] = []
    page.on(
        "request",
        lambda request: writes.append((request.url, request.post_data_json))
        if request.method == "POST" and f"/api/chat/{ticket_id}/" in request.url
        else None,
    )

    page.locator("[data-chat-image-input]").set_input_files(_image_file())
    page.fill("[data-chat-input]", "/status")
    page.click("[data-chat-send]")
    _wait_chat_text(page, "system", "exec: /status")

    assert writes == [
        (f"{server.base}/api/chat/{ticket_id}/turns", {"text": "/status", "mode": "command"})
    ]
    assert page.locator("[data-chat-image]").get_attribute("data-chat-image-pending") == "true"
    assert api.get(server, f"/api/chat/{ticket_id}/state")["messages"][0]["text"] == "/status"


def test_ticket_chat_text_and_image_render_immediately_after_navigation_and_reload(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Ticket image chat")["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )
    turn_payloads: list[dict] = []
    page.on(
        "request",
        lambda request: turn_payloads.append(request.post_data_json)
        if request.method == "POST" and request.url.endswith(f"/api/chat/{ticket_id}/turns")
        else None,
    )

    page.locator("[data-chat] [data-chat-image-input]").set_input_files(_image_file())
    assert page.locator("[data-chat] [data-chat-image]").get_attribute(
        "data-chat-image-pending"
    ) == "true"
    page.fill("[data-chat] [data-chat-input]", "What is in this image?")
    page.click("[data-chat] [data-chat-send]")

    first_metrics = _wait_image_geometry(page, "[data-chat]")
    state = api.get(server, f"/api/chat/{ticket_id}/state")
    human_text = state["messages"][0]["text"]
    assert human_text.startswith("What is in this image?\n\n![Attached image](/files/chats/")
    assert first_metrics["src"] in human_text
    assert turn_payloads == [
        {
            "text": "What is in this image?",
            "mode": "message",
            "image_reference": first_metrics["src"],
        }
    ]
    assert page.locator("[data-chat] [data-chat-image]").get_attribute(
        "data-chat-image-pending"
    ) is None

    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(server.base + f"/#/ticket/{ticket_id}")
    page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    navigation_metrics = _wait_image_geometry(page, "[data-chat]")
    assert navigation_metrics["src"] == first_metrics["src"]

    page.reload()
    page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    reload_metrics = _wait_image_geometry(page, "[data-chat]")
    assert reload_metrics["src"] == first_metrics["src"]


def test_chief_chat_image_only_renders_immediately_after_navigation_and_reload(
    server, context_factory, open_page, api
) -> None:
    entity_id = "agent_panels_chief_of_staff"
    page = open_page(
        context_factory(),
        server,
        "#/chief",
        'section[data-screen="chief"] [data-chat-input]',
        settled=False,
    )
    turn_payloads: list[dict] = []
    page.on(
        "request",
        lambda request: turn_payloads.append(request.post_data_json)
        if request.method == "POST" and request.url.endswith(f"/api/chat/{entity_id}/turns")
        else None,
    )

    page.locator("[data-chat-image-input]").set_input_files(_image_file("chief.png"))
    assert page.locator("[data-chat-send]").is_enabled()
    page.click("[data-chat-send]")

    first_metrics = _wait_image_geometry(page, 'section[data-screen="chief"]')
    state = api.get(server, f"/api/chat/{entity_id}/state")
    assert state["messages"][0]["text"] == f"![Attached image]({first_metrics['src']})"
    assert turn_payloads == [
        {"text": "", "mode": "message", "image_reference": first_metrics["src"]}
    ]

    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(server.base + "/#/chief")
    page.wait_for_selector('section[data-screen="chief"] [data-chat-input]', timeout=WAIT_MS)
    navigation_metrics = _wait_image_geometry(page, 'section[data-screen="chief"]')
    assert navigation_metrics["src"] == first_metrics["src"]

    page.reload()
    page.wait_for_selector('section[data-screen="chief"] [data-chat-input]', timeout=WAIT_MS)
    reload_metrics = _wait_image_geometry(page, 'section[data-screen="chief"]')
    assert reload_metrics["src"] == first_metrics["src"]


def test_invalid_image_selection_shows_one_error_and_creates_no_turn(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Reject invalid image selection")[
        "id"
    ]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )
    writes: list[str] = []
    page.on(
        "request",
        lambda request: writes.append(request.url)
        if request.method == "POST" and f"/api/chat/{ticket_id}/" in request.url
        else None,
    )

    page.locator("[data-chat] [data-chat-image-input]").set_input_files(
        {"name": "not-an-image.txt", "mimeType": "text/plain", "buffer": b"not an image"}
    )
    page.wait_for_selector("[data-chat-panel] > .error-line", timeout=WAIT_MS)
    assert page.locator("[data-chat-panel] > .error-line").count() == 1
    assert "image" in page.locator("[data-chat-panel] > .error-line").inner_text().lower()
    assert writes == []
    assert api.get(server, f"/api/chat/{ticket_id}/state")["messages"] == []


def test_pending_image_survives_upload_and_turn_start_failures(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Retain failed image")["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )
    error_body = '{"error":{"code":"validation","message":"image upload failed"}}'
    page.route(
        f"**/api/chat/{ticket_id}/images",
        lambda route: route.fulfill(
            status=400, content_type="application/json", body=error_body
        ),
    )
    page.locator("[data-chat] [data-chat-image-input]").set_input_files(_image_file())
    page.click("[data-chat] [data-chat-send]")
    page.wait_for_selector("[data-chat-panel] > .error-line", timeout=WAIT_MS)
    assert page.locator("[data-chat] [data-chat-image]").get_attribute(
        "data-chat-image-pending"
    ) == "true"
    assert api.get(server, f"/api/chat/{ticket_id}/state")["messages"] == []

    page.unroute(f"**/api/chat/{ticket_id}/images")
    page.route(
        f"**/api/chat/{ticket_id}/turns",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":{"code":"gateway_offline","message":"turn start failed"}}',
        ),
    )
    page.click("[data-chat] [data-chat-send]")
    page.wait_for_function(
        "() => document.querySelector('[data-chat-panel] > .error-line')?.textContent"
        ".includes('turn start failed')",
        timeout=WAIT_MS,
    )
    assert page.locator("[data-chat] [data-chat-image]").get_attribute(
        "data-chat-image-pending"
    ) == "true"
    assert api.get(server, f"/api/chat/{ticket_id}/state")["messages"] == []
