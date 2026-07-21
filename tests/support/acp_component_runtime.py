"""Exercise the ACP conversation pane as a mounted browser component."""

from __future__ import annotations

import argparse

from playwright.sync_api import Page, sync_playwright


def action_log(page: Page) -> list[dict[str, object]]:
    value = page.evaluate("window.__acpActions")
    if not isinstance(value, list):
        raise AssertionError("ACP runtime harness did not expose an action log")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    arguments = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5_000)
        page.goto(arguments.url, wait_until="networkidle")

        assert page.locator("[data-acp-conversation-pane]").count() == 1
        assert page.locator("[data-acp-transcript]").count() == 1
        assert page.locator("[data-acp-thought]").count() == 1
        assert page.locator("[data-acp-tool='tool-runtime']").count() == 1
        assert page.locator("[data-acp-plan]").count() == 1
        assert page.locator("[data-acp-permission='permission-runtime']").count() == 1
        assert page.locator("[data-acp-composer]").count() == 1
        assert page.get_by_role("status").count() == 1
        assert "waiting for permission" in page.get_by_role("status").inner_text()
        assert "42 / 100 tokens" in page.get_by_role("status").inner_text()
        assert page.get_by_role("alert").count() == 0

        messages = page.locator("[data-acp-message]")
        assert messages.count() == 2
        assert messages.nth(0).get_attribute("data-acp-message") == "human-runtime"
        assert messages.nth(1).get_attribute("data-acp-message") == "agent-runtime"
        assert "Human runtime message" in messages.nth(0).inner_text()
        assert "Agent runtime answer" in messages.nth(1).inner_text()

        thought = page.get_by_role("button", name="Thinking")
        assert thought.get_attribute("aria-expanded") == "false"
        assert page.get_by_text("Private typed thought").count() == 0
        thought.click()
        assert thought.get_attribute("aria-expanded") == "true"
        assert page.get_by_text("Private typed thought").count() == 1

        tool = page.locator("[data-acp-tool='tool-runtime'] > button")
        assert "Runtime tool" in tool.inner_text()
        assert "shell · completed" in tool.inner_text()
        assert tool.get_attribute("aria-expanded") == "false"
        tool.click()
        assert tool.get_attribute("aria-expanded") == "true"
        assert page.locator("[data-acp-diff]").count() == 1
        assert page.get_by_role("table", name="Line changes for runtime.txt").count() == 1
        assert page.get_by_role("cell", name="Deleted").count() == 1
        assert page.get_by_role("cell", name="Added").count() == 1
        terminal = page.get_by_role("region", name="Terminal terminal-runtime")
        assert "released" in terminal.inner_text()
        assert "runtime output" in terminal.inner_text()
        assert "Earlier output was truncated" in terminal.inner_text()
        raw = page.get_by_role("button", name="Raw input and output")
        assert raw.get_attribute("aria-expanded") == "false"
        raw.click()
        assert raw.get_attribute("aria-expanded") == "true"
        assert "runtime-input" in page.locator("#acp-tool-raw-tool-runtime").inner_text()
        assert "runtime-output" in page.locator("#acp-tool-raw-tool-runtime").inner_text()

        plan = page.get_by_role("region", name="Current plan")
        assert "Ship runtime proof" in plan.inner_text()
        assert "in_progress · high" in plan.inner_text()

        permission_group = page.get_by_role("group", name="Permission options")
        options = permission_group.get_by_role("button")
        assert options.all_inner_texts() == ["Allow once", "Always allow", "Reject"]
        assert [options.nth(index).get_attribute("data-permission-kind") for index in range(3)] == [
            "allow_once",
            "allow_always",
            "reject_once",
        ]
        options.nth(1).click()
        assert options.nth(1).get_attribute("aria-pressed") == "true"
        assert all(options.nth(index).is_disabled() for index in range(3))
        permission_action = {
            "type": "permission",
            "requestId": "permission-runtime",
            "optionId": "allow-always",
        }
        assert permission_action in action_log(page)

        delivery_group = page.get_by_role("group", name="Delivery choice")
        assert delivery_group.get_by_role("button", name="Steer").is_disabled()
        send_now = delivery_group.get_by_role("button", name="Send Now")
        send_now.click()
        assert send_now.get_attribute("aria-pressed") == "true"
        page.locator("[data-chat-input]").fill("Runtime follow-up")
        page.locator("[data-chat-send]").click()
        page.wait_for_function("window.__acpActions.some((item) => item.type === 'prompt')")
        assert {
            "type": "prompt",
            "text": "Runtime follow-up",
            "choice": "send_now",
        } in action_log(page)

        queue = page.get_by_role("list", name="Queued prompts")
        assert "Queued runtime prompt" in queue.inner_text()
        queue.get_by_role("button", name="Cancel").click()
        page.get_by_role("button", name="Stop").click()
        page.get_by_role("button", name="New conversation").click()
        actions = action_log(page)
        assert {"type": "cancelQueued", "clientMessageId": "queue-runtime"} in actions
        assert {"type": "cancelActive"} in actions
        assert {"type": "newConversation"} in actions

        receipt = page.locator(".acp-receipt")
        assert "rejected · latest older-key update" in receipt.inner_text()
        assert "queue 2" not in receipt.inner_text()

        page.evaluate("window.__setConversationDeliveryState(false, false, false)")
        composer_input = page.locator("[data-chat-input]")
        composer_input.fill("Retry with ordered images")
        page.locator("[data-chat-image-input]").set_input_files(
            [
                {
                    "name": "first.png",
                    "mimeType": "image/png",
                    "buffer": b"first",
                },
                {
                    "name": "second.png",
                    "mimeType": "image/png",
                    "buffer": b"second",
                },
            ]
        )
        previews = page.locator("[data-chat-image-preview]")
        assert previews.count() == 2
        assert [
            previews.nth(index).get_attribute("data-chat-image-name")
            for index in range(2)
        ] == ["first.png", "second.png"]

        page.locator("[data-chat-send]").click()
        composer_error = page.locator("[data-acp-composer-error]")
        composer_error.wait_for(state="attached")
        assert composer_error.inner_text() == "Conversation is not ready"
        assert composer_input.input_value() == "Retry with ordered images"
        assert previews.count() == 2
        assert page.evaluate("window.__acpRevokedObjectUrls") == []

        page.evaluate("window.__setConversationDeliveryState(true, true, true)")
        steer = page.get_by_role("group", name="Delivery choice").get_by_role(
            "button", name="Steer"
        )
        steer.click()
        assert steer.get_attribute("aria-pressed") == "true"
        page.evaluate("window.__setConversationDeliveryState(true, false, true)")
        assert steer.is_disabled()
        page.locator("[data-chat-send]").click()
        page.wait_for_function(
            "document.querySelector('[data-acp-composer-error]')?.textContent "
            "=== 'Steer is unavailable for this employee'"
        )
        assert composer_error.inner_text() == "Steer is unavailable for this employee"
        assert composer_input.input_value() == "Retry with ordered images"
        assert previews.count() == 2
        assert page.evaluate("window.__acpRevokedObjectUrls") == []

        page.evaluate("window.__setConversationDeliveryState(true, true, true)")
        page.locator("[data-chat-send]").click()
        page.wait_for_function(
            "document.querySelectorAll('[data-chat-image-preview]').length === 0"
        )
        assert composer_input.input_value() == ""
        assert composer_error.count() == 0
        revoked_urls = page.evaluate("window.__acpRevokedObjectUrls")
        assert len(revoked_urls) == 2
        assert len(set(revoked_urls)) == 2

        prompt_attempts = page.evaluate("window.__acpPromptAttempts")[-3:]
        expected_blocks = [
            {"type": "text", "text": "Retry with ordered images"},
            {"type": "image", "data": "Zmlyc3Q=", "mimeType": "image/png"},
            {"type": "image", "data": "c2Vjb25k", "mimeType": "image/png"},
        ]
        assert [attempt["choice"] for attempt in prompt_attempts] == [
            "normal",
            "steer",
            "steer",
        ]
        assert [attempt["blocks"] for attempt in prompt_attempts] == [
            expected_blocks,
            expected_blocks,
            expected_blocks,
        ]
        assert [attempt["result"] for attempt in prompt_attempts] == [
            {"ok": False, "reason": "Conversation is not ready"},
            {
                "ok": False,
                "reason": "Steer is unavailable for this employee",
            },
            {"ok": True, "clientMessageId": "runtime-client"},
        ]

        compaction = page.locator("[data-acp-compaction='compaction-runtime']")
        assert compaction.inner_text() == "Context compacted · explicit"
        assert compaction.get_by_role("button").count() == 0
        assert "Runtime compaction summary" not in compaction.inner_text()
        failed_compaction = page.locator(
            "[data-acp-compaction='compaction-failed-runtime']"
        )
        assert failed_compaction.inner_text() == (
            "Context failed · automatic · Exact runtime compaction failure"
        )
        assert failed_compaction.get_by_role("button").count() == 0
        assert "summary" not in failed_compaction.inner_text().lower()

        page.evaluate("window.__raiseConnectionError()")
        page.get_by_role("alert").wait_for(state="attached")
        assert page.get_by_role("status").count() == 1
        assert page.get_by_role("alert").inner_text() == "Runtime connection failed"
        assert "Runtime connection failed" in page.get_by_role("status").inner_text()

        page.evaluate("window.__raiseProtocolError()")
        page.get_by_role("alert").wait_for(state="attached")
        assert page.get_by_role("status").count() == 1
        assert page.get_by_role("alert").inner_text() == "Agent sent an unsupported update"
        protocol = page.locator("[data-acp-protocol-rejection]")
        protocol_button = protocol.get_by_role("button")
        assert protocol_button.get_attribute("aria-expanded") == "false"
        protocol_button.click()
        assert protocol_button.get_attribute("aria-expanded") == "true"
        assert "Runtime unsupported update" in protocol.inner_text()

        browser.close()

    print("acp_component_runtime.py: mounted interactions and accessibility assertions passed")


if __name__ == "__main__":
    main()
