"""Shared conversation behavior through the real Ticket screen."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast

import httpx
from playwright.sync_api import BrowserContext, Page, Route
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle

from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import (
    ConversationEventPayload,
    PromptEventPayload,
    UserInputAnswer,
    UserInputAnsweredEventPayload,
    UserInputOption,
    UserInputQuestion,
    UserInputRequestedEventPayload,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore


class PhoneLayout(TypedDict):
    stackHeight: int
    stackScrollHeight: int
    textWhiteSpace: str
    documentWidth: int
    viewportWidth: int


def _create_ticket_conversation(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    *,
    conversation_id: str,
    title: str,
) -> str:
    ticket_id: str = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        title,
    )["id"]
    created = httpx.post(
        f"{server.base}/api/conversation/conversations",
        json={
            "conversation_id": conversation_id,
            "model": "e2e-model",
            "backend_key": "codex",
        },
        timeout=10.0,
    )
    assert created.status_code == 201, created.text
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket_id),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (conversation_id, ticket_id),
        )
    return ticket_id


def _append_rows(
    server: ServerHandle, conversation_id: str, *payloads: ConversationEventPayload
) -> None:
    store = ConversationStore(str(server.db_path))

    async def write() -> None:
        for payload in payloads:
            await store.append_event(conversation_id, payload)

    failures: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(write())
        except BaseException as error:  # noqa: BLE001 - re-raised on the browser thread
            failures.append(error)

    writer = threading.Thread(target=run)
    writer.start()
    writer.join(timeout=10)
    assert not writer.is_alive()
    if failures:
        raise failures[0]


def _open_ticket_conversation(
    server: ServerHandle,
    ticket_id: str,
    open_page: Callable[..., Page],
    context: BrowserContext,
    ready_selector: str,
) -> Page:
    page = open_page(
        context,
        server,
        f"#/workspace/{ticket_id}",
        f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _open_conversation_layer(page)
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
    return page


def _open_conversation_layer(page: Page) -> None:
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="peeked"]').wait_for(timeout=WAIT_MS)
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="opened"]').wait_for(timeout=WAIT_MS)


def test_agent_questions_survive_reload_submit_as_one_map_and_replay_answers(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    tmp_path: Path,
) -> None:
    """The Ticket pane consumes durable questions and sends one answer map."""
    conversation_id = "e2e-ticket-agent-questions"
    ticket_id = _create_ticket_conversation(
        server,
        cli,
        conversation_id=conversation_id,
        title="Answer agent questions",
    )
    questions = (
        UserInputQuestion(
            question_id="scope",
            header="Scope",
            question="Which surfaces should change?",
            options=(
                UserInputOption(label="Composer", description="The live composer"),
                UserInputOption(label="Transcript", description="Durable history"),
            ),
            multi_select=True,
            allow_other=True,
        ),
        UserInputQuestion(
            question_id="proof",
            header="Proof",
            question="What proof should be required?",
            options=(UserInputOption(label="Browser test", description="Exercise the full flow"),),
            multi_select=False,
            allow_other=True,
        ),
    )
    _append_rows(
        server,
        conversation_id,
        PromptEventPayload(
            content=text_message_content("ask me the implementation questions"),
            sender_label="owner",
            mode=PromptDeliveryMode.queue,
        ),
        UserInputRequestedEventPayload(request_id="input-1", questions=questions),
    )

    submitted: list[dict[str, Any]] = []
    context = context_factory()

    def running_view(route: Route) -> None:
        response = route.fetch()
        body = response.json()
        body["is_running"] = True
        body["pending_user_input"] = {
            "request_id": "input-1",
            "questions": [
                {
                    "question_id": question.question_id,
                    "header": question.header,
                    "question": question.question,
                    "options": [
                        {"label": option.label, "description": option.description}
                        for option in question.options
                    ],
                    "multi_select": question.multi_select,
                    "allow_other": question.allow_other,
                }
                for question in questions
            ],
        }
        route.fulfill(response=response, json=body)

    context.route(f"**/api/conversation/conversations/{conversation_id}", running_view)

    def take_user_input_answer(route: Route) -> None:
        payload = route.request.post_data_json
        assert payload is not None
        submitted.append(cast(dict[str, Any], payload))
        route.fulfill(status=200, json={"landed": True})

    context.route(
        f"**/api/conversation/conversations/{conversation_id}/user-input-answers",
        take_user_input_answer,
    )
    page = _open_ticket_conversation(
        server,
        ticket_id,
        open_page,
        context,
        "[data-user-input-panel]",
    )
    assert "Approve" not in page.locator("[data-user-input-panel]").inner_text()

    page.reload()
    page.wait_for_selector("[data-conversation-rest-bar]", timeout=WAIT_MS)
    _open_conversation_layer(page)
    page.wait_for_selector("[data-user-input-panel]", timeout=WAIT_MS)
    page.locator("[data-conversation-lens-toggle]").click()
    screenshot = tmp_path / "agent-questions.png"
    page.screenshot(path=str(screenshot))
    assert screenshot.stat().st_size > 0

    page.locator('[data-user-input-option="Composer"]').click()
    page.locator('[data-user-input-option="Transcript"]').click()
    page.locator("[data-user-input-continue]").click()
    page.locator("[data-user-input-other]").fill("Recorded event replay")
    page.locator("[data-user-input-continue]").click()
    page.wait_for_function("() => document.querySelector('[data-user-input-panel]') !== null")
    assert submitted == [
        {
            "request_id": "input-1",
            "answers": {
                "scope": {"answers": ["Composer", "Transcript"]},
                "proof": {"answers": ["Recorded event replay"]},
            },
        }
    ]

    _append_rows(
        server,
        conversation_id,
        UserInputAnsweredEventPayload(
            request_id="input-1",
            answers=(
                UserInputAnswer("scope", ("Composer", "Transcript")),
                UserInputAnswer("proof", ("Recorded event replay",)),
            ),
        ),
    )
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector('[data-conversation-user-input-state="answered"]', timeout=WAIT_MS)
    assert (
        "Recorded event replay"
        in page.locator("[data-conversation-user-input-answers]").inner_text()
    )


def test_canonical_queue_rows_work_across_tabs_and_on_a_phone(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Two Ticket panes draw and act on one server-owned FIFO stack."""
    conversation_id = "e2e-ticket-canonical-queue"
    ticket_id = _create_ticket_conversation(
        server,
        cli,
        conversation_id=conversation_id,
        title="Manage held prompts",
    )
    view = httpx.get(
        f"{server.base}/api/conversation/conversations/{conversation_id}",
        timeout=10.0,
    ).json()
    held = [
        {
            "held_prompt_id": f"held-{number}",
            "text": f"queued message {number}",
            "sender_label": "owner",
            "sender_message_id": f"sender-{number}",
            "sent_at_unix_milliseconds": 1_700_000_000_000 + number,
        }
        for number in range(1, 7)
    ]
    actions: list[tuple[str, str]] = []

    def queue_api(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        view_path = f"/api/conversation/conversations/{conversation_id}"
        if request.method == "GET" and path.endswith(view_path):
            route.fulfill(
                json={**view, "backend_key": "hermes", "is_running": True, "held_prompts": held}
            )
            return
        if request.method == "DELETE" and "/held-prompts/" in path:
            held_prompt_id = path.rsplit("/", 1)[-1]
            actions.append((held_prompt_id, "discard"))
            held[:] = [item for item in held if item["held_prompt_id"] != held_prompt_id]
            route.fulfill(json={"discarded": True})
            return
        if request.method == "POST" and path.endswith("/promote"):
            held_prompt_id = path.rsplit("/", 2)[-2]
            payload = request.post_data_json
            assert payload is not None
            mode = str(payload["mode"])
            actions.append((held_prompt_id, mode))
            held[:] = [item for item in held if item["held_prompt_id"] != held_prompt_id]
            route.fulfill(
                json={
                    "promoted": True,
                    "fate": "injected" if mode == "steer" else "started",
                }
            )
            return
        route.continue_()

    context = context_factory()
    context.route(
        f"**/api/conversation/conversations/{conversation_id}**",
        queue_api,
    )
    desktop = _open_ticket_conversation(
        server,
        ticket_id,
        open_page,
        context,
        "[data-conversation-held-stack]",
    )
    phone = _open_ticket_conversation(
        server,
        ticket_id,
        open_page,
        context,
        "[data-conversation-held-stack]",
    )
    phone.set_viewport_size({"width": 390, "height": 844})

    expected = [f"queued message {number}" for number in range(1, 7)]
    for page in (desktop, phone):
        assert (
            page.locator("[data-conversation-held-row] .chat-qrow-txt").all_inner_texts()
            == expected
        )

    def control_signature(page: Page) -> dict[str, object]:
        return cast(
            dict[str, object],
            page.evaluate(
                """() => {
              const signature = (button) => {
                const style = getComputedStyle(button);
                return {
                  label: button.getAttribute('aria-label') || button.textContent.trim(),
                  className: button.className,
                  borderRadius: style.borderRadius,
                  fontFamily: style.fontFamily,
                  fontSize: style.fontSize,
                  height: style.height,
                  padding: style.padding,
                };
              };
              const row = document.querySelector('[data-conversation-held-row]');
              const foot = document.querySelector('.chat-foot');
              return {
                queue: [...row.querySelectorAll('button')].map(signature),
                input: [...foot.querySelectorAll('button')].map(signature),
                inputWrap: getComputedStyle(foot).flexWrap,
              };
            }"""
            ),
        )

    desktop_controls = control_signature(desktop)
    phone_controls = control_signature(phone)
    assert desktop_controls == phone_controls
    assert desktop_controls["inputWrap"] == "nowrap"

    phone_layout = cast(
        PhoneLayout,
        phone.evaluate(
            "() => {"
            " const stack = document.querySelector('.chat-queue-stack');"
            " const text = document.querySelector('.chat-qrow-txt');"
            " return {"
            "   stackHeight: stack.clientHeight,"
            "   stackScrollHeight: stack.scrollHeight,"
            "   textWhiteSpace: getComputedStyle(text).whiteSpace,"
            "   documentWidth: document.documentElement.scrollWidth,"
            "   viewportWidth: window.innerWidth,"
            " };"
            "}"
        ),
    )
    assert phone_layout["stackScrollHeight"] > phone_layout["stackHeight"]
    assert phone_layout["textWhiteSpace"] == "nowrap"
    assert phone_layout["documentWidth"] <= phone_layout["viewportWidth"]

    desktop.click('[data-conversation-held-promote="steer"][data-held-prompt-id="held-1"]')
    desktop.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 5",
        timeout=WAIT_MS,
    )
    assert actions[-1] == ("held-1", "steer")

    phone.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    phone.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 5",
        timeout=WAIT_MS,
    )
    assert "queued message 1" not in phone.inner_text("[data-conversation-held-stack]")

    phone.click('[data-conversation-held-discard="held-2"]')
    phone.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 4",
        timeout=WAIT_MS,
    )
    assert actions[-1] == ("held-2", "discard")

    desktop.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    desktop.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 4",
        timeout=WAIT_MS,
    )
    desktop.click('[data-conversation-held-promote="send_now"][data-held-prompt-id="held-3"]')
    desktop.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 3",
        timeout=WAIT_MS,
    )
    assert actions[-1] == ("held-3", "send_now")
