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

        # Structure: pane, transcript, task strip, permission prompt, composer.
        assert page.locator("[data-acp-conversation-pane]").count() == 1
        assert page.locator("[data-acp-transcript]").count() == 1
        assert page.locator("[data-acp-task-strip]").count() == 1
        assert page.locator("[data-acp-permission='permission-runtime']").count() == 1
        assert page.locator("[data-acp-composer]").count() == 1

        # Header is silent identity + usage. The retired status strip is gone;
        # usage carries no "tokens" word and no cost (none on the wire here).
        assert page.locator(".chat-lbl").inner_text() == "Runtime employee"
        assert page.locator(".chat-usage").inner_text() == "42 / 100"
        assert "tokens" not in page.locator(".chat-usage").inner_text()
        assert page.get_by_role("status").count() == 0
        # The one header exception in play: a pending permission speaks "waiting
        # for you"; connection is healthy so no trouble dot shows.
        assert page.locator(".chat-state").inner_text() == "waiting for you"
        assert page.locator(".chat-conn-dot").count() == 0

        # Messages keep their voices: right-aligned user bubble, bubble-less agent.
        messages = page.locator("[data-acp-message]")
        assert messages.count() == 2
        assert messages.nth(0).get_attribute("data-acp-message") == "human-runtime"
        assert messages.nth(1).get_attribute("data-acp-message") == "agent-runtime"
        assert "Human runtime message" in messages.nth(0).inner_text()
        assert "Agent runtime answer" in messages.nth(1).inner_text()

        # A stanza is one beat of work: a Thought line plus the steps it drove.
        # Settled turn -> the word is "Thought"; the first line of thought text is
        # the italic preview. Collapsed by default: no body, no steps in the DOM.
        stanza = page.locator("[data-acp-stanza]")
        assert stanza.count() == 1
        think = stanza.get_by_role("button").first
        assert think.locator(".acp-think-word").inner_text() == "Thought"
        assert think.locator(".acp-think-preview").inner_text() == "Private typed thought"
        assert think.get_attribute("aria-expanded") == "false"
        assert stanza.locator(".acp-think-body").count() == 0
        assert stanza.locator("[data-acp-step='tool-runtime']").count() == 0

        # Expanding the stanza reveals the full thought body and its steps.
        think.click()
        assert think.get_attribute("aria-expanded") == "true"
        assert stanza.locator(".acp-think-body").count() == 1
        step = page.locator("[data-acp-step='tool-runtime']")
        assert step.count() == 1
        assert "Runtime tool" in step.inner_text()
        # Right-aligned status mark: completed reads as a green check.
        assert step.locator(".acp-step-mark.acp-mark-ok").inner_text() == "✓"

        # Expanding the step reveals its detail (diff + terminal well). Raw JSON
        # input/output is dropped: no toggle, no raw payload text anywhere.
        assert step.get_attribute("aria-expanded") == "false"
        step.click()
        assert step.get_attribute("aria-expanded") == "true"
        assert page.locator("[data-acp-diff]").count() == 1
        assert page.get_by_role("table", name="Line changes for runtime.txt").count() == 1
        assert page.get_by_role("cell", name="Deleted").count() == 1
        assert page.get_by_role("cell", name="Added").count() == 1
        terminal = page.get_by_role("region", name="Terminal terminal-runtime")
        assert "released" in terminal.inner_text()
        assert "runtime output" in terminal.inner_text()
        assert "Earlier output was truncated" in terminal.inner_text()
        assert page.get_by_role("button", name="Raw input and output").count() == 0
        assert page.get_by_text("runtime-input").count() == 0
        assert page.get_by_text("runtime-output").count() == 0

        # Task pill: a plan exists and the turn is active, so the centred pill
        # reads "{done} / {total} tasks" with the entries in a popover. The
        # spinner rides only while thinking/compacting, not while parked on a
        # permission, so none shows here.
        task_pill = page.locator(".task-pill")
        assert task_pill.get_attribute("aria-label") == "0 of 1 tasks complete"
        assert task_pill.inner_text() == "0 / 1 tasks"
        assert task_pill.locator(".acp-spin").count() == 0
        assert "Ship runtime proof" in page.locator(".task-pop").text_content()

        # Compaction seams are centred flat dividers with lowercase mono labels;
        # no token counts, no summary, no disclosure button.
        compaction = page.locator("[data-acp-compaction='compaction-runtime']")
        assert compaction.inner_text() == "context compacted · explicit"
        assert compaction.get_by_role("button").count() == 0
        assert "summary" not in compaction.inner_text().lower()
        failed_compaction = page.locator("[data-acp-compaction='compaction-failed-runtime']")
        assert failed_compaction.inner_text() == (
            "context compaction failed · Exact runtime compaction failure"
        )
        assert failed_compaction.get_by_role("button").count() == 0
        assert "summary" not in failed_compaction.inner_text().lower()

        # Permission: reject gathers left, allow right, last allow is the filled
        # primary. No status text. Selecting disables every option (opacity only).
        option_group = page.get_by_role("group", name="Permission options")
        options = option_group.get_by_role("button")
        assert options.all_inner_texts() == ["Reject", "Allow once", "Always allow"]
        assert [options.nth(index).get_attribute("data-permission-kind") for index in range(3)] == [
            "reject_once",
            "allow_once",
            "allow_always",
        ]
        assert page.locator(".acp-permission-allow.primary").inner_text() == "Always allow"
        assert page.locator(".acp-permission-status").count() == 0
        page.locator(".acp-permission-allow.primary").click()
        assert page.locator(".acp-permission-allow.primary").get_attribute("aria-pressed") == "true"
        assert all(options.nth(index).is_disabled() for index in range(3))
        assert page.locator(".acp-permission-status").count() == 0
        assert {
            "type": "permission",
            "requestId": "permission-runtime",
            "optionId": "allow-always",
        } in action_log(page)

        # Delivery choice: a segmented control shown only while a turn is active,
        # left of the send control. Steer is disabled (not hidden) when the
        # employee can't steer. The active-turn control is Stop, so a follow-up is
        # submitted with Enter and carries the chosen delivery.
        delivery_group = page.get_by_role("group", name="Delivery")
        assert delivery_group.get_by_role("button", name="steer").is_disabled()
        send_now = delivery_group.get_by_role("button", name="send now")
        send_now.click()
        assert send_now.get_attribute("aria-pressed") == "true"
        page.locator("[data-chat-input]").fill("Runtime follow-up")
        page.locator("[data-chat-input]").press("Enter")
        page.wait_for_function("window.__acpActions.some((item) => item.type === 'prompt')")
        assert {
            "type": "prompt",
            "text": "Runtime follow-up",
            "choice": "send_now",
        } in action_log(page)

        # Queue tray: queued prompts render fused above the box with a per-item
        # cancel; delivery markers no longer live in the transcript.
        queue = page.get_by_role("list", name="Queued prompts")
        assert "Queued runtime prompt" in queue.inner_text()
        queue.get_by_role("button", name="Cancel queued prompt").click()
        # Send <-> Stop is one control; while active it is the red-outline Stop.
        page.get_by_role("button", name="Stop the turn").click()
        actions = action_log(page)
        assert {"type": "cancelQueued", "clientMessageId": "queue-runtime"} in actions
        assert {"type": "cancelActive"} in actions

        # Only failures speak below the box: the latest receipt was rejected.
        receipt = page.locator(".chat-receipt")
        assert "rejected · latest older-key update" in receipt.inner_text()
        assert "queue 2" not in receipt.inner_text()

        # New conversation lives in the header overflow menu behind a Confirm step.
        page.get_by_role("button", name="Conversation options").click()
        page.get_by_role("menuitem", name="New conversation").click()
        page.get_by_role("menuitem", name="Confirm").click()
        assert {"type": "newConversation"} in action_log(page)

        # Composer error handling across delivery state, image ordering, and the
        # steer guard — a rejected send keeps the draft and previews intact.
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

        # Idle -> the control is Send; submitting with no cursor is rejected.
        page.locator("[data-chat-send]").click()
        composer_error = page.locator("[data-acp-composer-error]")
        composer_error.wait_for(state="attached")
        assert composer_error.inner_text() == "Conversation is not ready"
        assert composer_input.input_value() == "Retry with ordered images"
        assert previews.count() == 2
        assert page.evaluate("window.__acpRevokedObjectUrls") == []

        page.evaluate("window.__setConversationDeliveryState(true, true, true)")
        steer = page.get_by_role("group", name="Delivery").get_by_role("button", name="steer")
        steer.click()
        assert steer.get_attribute("aria-pressed") == "true"
        page.evaluate("window.__setConversationDeliveryState(true, false, true)")
        assert steer.is_disabled()
        # Active -> the control is Stop; submit the steer draft with Enter.
        composer_input.press("Enter")
        page.wait_for_function(
            "document.querySelector('[data-acp-composer-error]')?.textContent "
            "=== 'Steer is unavailable for this employee'"
        )
        assert composer_error.inner_text() == "Steer is unavailable for this employee"
        assert composer_input.input_value() == "Retry with ordered images"
        assert previews.count() == 2
        assert page.evaluate("window.__acpRevokedObjectUrls") == []

        page.evaluate("window.__setConversationDeliveryState(true, true, true)")
        composer_input.press("Enter")
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

        # The retired status strip's screen-reader alert behaviour is preserved
        # invisibly: new errors fire an off-screen role="alert" and no visible
        # role="status" strip returns.
        status_alert = page.locator("[data-acp-alert]")
        page.evaluate("window.__raiseConnectionError()")
        status_alert.wait_for(state="attached")
        assert page.get_by_role("status").count() == 0
        assert status_alert.inner_text() == "Runtime connection failed"
        # Connection trouble also raises the silent red dot beside the name.
        assert page.locator(".chat-conn-dot").count() == 1

        page.evaluate("window.__raiseProtocolError()")
        status_alert.wait_for(state="attached")
        assert page.get_by_role("status").count() == 0
        assert status_alert.inner_text() == "Agent sent an unsupported update"
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
