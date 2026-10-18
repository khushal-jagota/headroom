# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def _run(browser, urls):

    import os

    # Set this to keep a picture of what the assertions checked. The test proves the same
    # thing with or without it.
    screenshot_dir = os.environ.get("UNANSWERED_SEND_SCREENSHOT_DIR")
    if screenshot_dir:
        os.makedirs(screenshot_dir, exist_ok=True)

    PANE = "[data-conversation-pane]"
    INPUT = "[data-conversation-input]"
    THREAD = "[data-conversation-thread]"

    def state(page, expected):
        page.wait_for_selector(f'{PANE}[data-conversation-state="{expected}"]')

    def draft(page):
        return page.locator(INPUT).evaluate("""box => ({
            text: box.value,
            start: box.selectionStart,
            end: box.selectionEnd
        })""")

    def first_visible_line(page):
        return page.locator(THREAD).evaluate("""thread => {
            const top = thread.getBoundingClientRect().top;
            const rows = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')];
            const row = rows.find(candidate => candidate.getBoundingClientRect().bottom > top + 1);
            return row ? {
              text: row.textContent.trim(),
              top: Math.round(row.getBoundingClientRect().top - top),
              scrollTop: Math.round(thread.scrollTop)
            } : null;
        }""")

    def wait_for_anchor(page, held):
        page.wait_for_function(
            """held => {
          const thread = document.querySelector('[data-conversation-thread]');
          const row = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')]
            .find(candidate => candidate.textContent.trim() === held.text);
          if (!row) return false;
          const top = Math.round(row.getBoundingClientRect().top - thread.getBoundingClientRect().top);
          return Math.abs(top - held.top) <= 3;
        }""",
            arg=held,
        )
        restored = first_visible_line(page)
        assert restored is not None
        assert restored["text"] == held["text"]
        assert abs(restored["top"] - held["top"]) <= 3, (held, restored)
        return restored

    context = browser.new_context(viewport={"width": 1000, "height": 760})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(5_000)
    browser_errors = []
    page.on("pageerror", lambda error: browser_errors.append(str(error)))
    page.goto([__file__, *urls][1], wait_until="domcontentloaded")

    # Rest, peeked, and opened are three heights of one mounted conversation.
    state(page, "rest")
    assert page.locator("[data-conversation-rest-bar]").is_visible()
    assert "Line 27:" in page.locator("[data-conversation-rest-line]").inner_text()
    assert page.locator(THREAD).count() == 1
    assert not page.locator(THREAD).is_visible()

    page.locator(INPUT).click()
    state(page, "peeked")
    assert page.locator(THREAD).is_visible()
    assert page.locator("[data-conversation-rest-bar]").count() == 0
    send_mode = page.locator("[data-conversation-send-mode]")
    send_mode_trigger = page.locator("[data-conversation-send-mode-trigger]")
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Steer"
    assert send_mode_trigger.get_attribute("aria-expanded") == "false"
    assert send_mode_trigger.get_attribute("aria-controls") is None

    # The model chooser uses the same one-tab-stop shell and focus contract.
    model_picker = page.locator("[data-conversation-model-picker]")
    model_trigger = model_picker.locator("[data-conversation-picker-trigger]")
    model_label = model_trigger.get_attribute("aria-label")
    assert model_trigger.get_attribute("aria-controls") is None
    model_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    model_controlled = model_trigger.get_attribute("aria-controls")
    assert (
        model_controlled
        and model_picker.locator(f"#{model_controlled}").get_attribute("role") == "listbox"
    )
    assert (
        model_picker.locator("button").evaluate_all(
            "buttons => buttons.filter(button => button.tabIndex === 0).length"
        )
        == 1
    )
    model_picker.locator('[data-conversation-picker-choice="sonnet"]').hover()
    # A pointer that moves onto a row makes that row the active one.
    page.wait_for_function(
        "document.querySelector('[data-conversation-model-picker] [data-conversation-picker-choice=sonnet]')?.getAttribute('data-conversation-picker-active') === 'true'"
    )
    assert model_trigger.get_attribute("aria-label") == model_label
    model_picker.get_by_role("listbox").press("s")
    page.wait_for_function(
        "document.querySelector('[data-conversation-model-picker] [data-conversation-picker-choice=sonnet]')?.getAttribute('data-conversation-picker-active') === 'true'"
    )
    model_picker.get_by_role("listbox").press("Enter")
    page.wait_for_function(
        "document.querySelector('[data-conversation-model-picker] [data-conversation-picker-trigger]')?.getAttribute('aria-label')?.toLowerCase().includes('sonnet')"
    )
    assert "sonnet" in model_trigger.get_attribute("aria-label").lower()
    model_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    model_picker.get_by_role("listbox").press("Home")
    page.wait_for_function(
        "document.querySelector('[data-conversation-model-picker] [data-conversation-picker-choice=opus]')?.getAttribute('data-conversation-picker-active') === 'true'"
    )
    model_picker.get_by_role("listbox").press(" ")
    # Opus takes a reasoning level. Choosing its row retains the popover and moves to
    # the second step. The trigger still shows the committed Sonnet choice until this
    # level completes the configuration.
    page.wait_for_function(
        "document.querySelector('[data-conversation-model-picker] [role=listbox]')?.getAttribute('aria-label') === 'Reasoning efforts'"
    )
    assert "sonnet" in model_trigger.get_attribute("aria-label").lower()
    model_picker.get_by_role("listbox").press("Enter")
    page.wait_for_function(
        "document.querySelector('[data-conversation-model-picker] [data-conversation-picker-trigger]')?.getAttribute('aria-label')?.toLowerCase().includes('opus')"
    )
    assert "opus" in model_trigger.get_attribute("aria-label").lower()

    model_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")

    # The pointer has rested on sonnet since the hover above, and opus is now the chosen
    # model. When the options mount again the browser sends a mouse move at the pointer's
    # resting place. That is the browser reporting geometry, not a person choosing a row,
    # so the chosen model keeps the active mark. Settle first, or the mouse move has not
    # arrived yet and the check proves nothing.
    page.wait_for_timeout(200)
    assert (
        model_picker.locator('[data-conversation-picker-choice="opus"]').get_attribute(
            "data-conversation-picker-active"
        )
        == "true"
    )

    model_picker.get_by_role("listbox").press("End")
    page.wait_for_function(
        "document.querySelector('[data-conversation-model-picker] [data-conversation-picker-choice=sonnet]')?.getAttribute('data-conversation-picker-active') === 'true'"
    )
    model_picker.get_by_role("listbox").press("Enter")
    page.wait_for_function(
        "document.querySelector('[data-conversation-model-picker] [data-conversation-picker-trigger]')?.getAttribute('aria-label')?.toLowerCase().includes('sonnet')"
    )
    assert "sonnet" in model_trigger.get_attribute("aria-label").lower()
    model_trigger.press("ArrowDown")
    page.evaluate("window.__setComposerDisabled(true)")
    assert model_picker.evaluate("root => document.activeElement === root") is True
    page.evaluate("window.__setComposerDisabled(false)")
    page.wait_for_function(
        "document.activeElement?.hasAttribute('data-conversation-picker-trigger')"
    )

    # Reset the fixture so keyboard selection coverage does not alter later send scenarios.
    page.reload(wait_until="domcontentloaded")
    state(page, "rest")
    page.locator(INPUT).click()
    state(page, "peeked")
    send_mode = page.locator("[data-conversation-send-mode]")
    send_mode_trigger = page.locator("[data-conversation-send-mode-trigger]")

    # The delivery chooser follows the product picker contract for pointer, keyboard,
    # focus return, outside dismissal, and disabled state.
    send_mode_trigger.click()
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    send_mode_panel = page.locator("[data-conversation-send-mode-panel]")
    controlled = send_mode_trigger.get_attribute("aria-controls")
    assert controlled and send_mode.locator(f"#{controlled}").get_attribute("role") == "listbox"
    assert (
        send_mode.locator("button").evaluate_all(
            "buttons => buttons.filter(button => button.tabIndex === 0).length"
        )
        == 1
    )
    assert send_mode_panel.get_by_role("option").count() == 3
    assert send_mode_panel.get_by_role("option", name="Steer", exact=True).count() == 1
    assert send_mode_panel.get_by_role("option", name="Queue", exact=True).count() == 1
    assert send_mode_panel.get_by_role("option", name="Send now", exact=True).count() == 1
    assert (
        send_mode_panel.get_by_role("option", name="Steer").get_attribute("aria-selected") == "true"
    )
    send_mode_panel.get_by_role("option", name="Send now").hover()
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Steer"
    send_mode_panel.press("Escape")
    assert page.locator("[data-conversation-send-mode-panel]").count() == 0
    assert send_mode_trigger.get_attribute("aria-controls") is None
    assert send_mode_trigger.evaluate("button => document.activeElement === button") is True

    send_mode_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    send_mode_panel.press("Tab")
    assert page.locator("[data-conversation-send-mode-panel]").count() == 0
    assert send_mode.evaluate("root => !root.contains(document.activeElement)") is True

    send_mode_trigger.focus()
    send_mode_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Steer"
    send_mode.get_by_role("listbox").press("q")
    page.wait_for_function(
        "document.querySelector('[data-conversation-send-mode] [data-conversation-send-mode-choice=queue]')?.getAttribute('data-listbox-picker-active') === 'true'"
    )
    assert (
        send_mode_panel.get_by_role("option", name="Queue").get_attribute(
            "data-listbox-picker-active"
        )
        == "true"
    )
    send_mode.get_by_role("listbox").press("Enter")
    page.wait_for_function(
        "document.querySelector('[data-conversation-send-mode-trigger]')?.getAttribute('aria-label') === 'Message delivery mode: Queue'"
    )
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Queue"
    send_mode_trigger.click()
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    send_mode.get_by_role("listbox").press("Home")
    page.wait_for_function(
        "document.querySelector('[data-conversation-send-mode] [data-conversation-send-mode-choice=steer]')?.getAttribute('data-listbox-picker-active') === 'true'"
    )
    send_mode.get_by_role("listbox").press("ArrowDown")
    page.wait_for_function(
        "document.querySelector('[data-conversation-send-mode] [data-conversation-send-mode-choice=queue]')?.getAttribute('data-listbox-picker-active') === 'true'"
    )
    send_mode.get_by_role("listbox").press(" ")
    page.wait_for_function(
        "document.querySelector('[data-conversation-send-mode-trigger]')?.getAttribute('aria-label') === 'Message delivery mode: Queue'"
    )
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Queue"
    page.wait_for_function(
        "document.activeElement?.hasAttribute('data-conversation-send-mode-trigger')"
    )
    assert send_mode_trigger.evaluate("button => document.activeElement === button") is True

    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="send_now"]').click()
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Send now"
    send_mode_trigger.click()
    page.locator(INPUT).click()
    assert page.locator("[data-conversation-send-mode-panel]").count() == 0

    send_mode_trigger.click()
    page.evaluate("window.__setComposerDisabled(true)")
    assert send_mode_trigger.is_disabled()
    assert page.locator("[data-conversation-send-mode-panel]").count() == 0
    assert send_mode.evaluate("root => document.activeElement === root") is True
    page.evaluate("window.__setComposerDisabled(false)")
    assert not send_mode_trigger.is_disabled()
    page.wait_for_function(
        "document.activeElement?.hasAttribute('data-conversation-send-mode-trigger')"
    )

    # Re-enabling returns focus only while focus remains within the disabled picker.
    send_mode_trigger.click()
    page.evaluate("window.__setComposerDisabled(true)")
    page.wait_for_function(
        "document.querySelector('[data-conversation-send-mode]') === document.activeElement"
    )
    unrelated_control = page.locator("[data-ticket-behind]")
    unrelated_control.focus()
    assert unrelated_control.evaluate("control => document.activeElement === control") is True
    page.evaluate("window.__setComposerDisabled(false)")
    page.wait_for_function(
        "!document.querySelector('[data-conversation-send-mode-trigger]').disabled"
    )
    page.evaluate("window.__settle()")
    active_after_enable = page.evaluate("document.activeElement?.outerHTML")
    assert unrelated_control.evaluate("control => document.activeElement === control") is True, (
        active_after_enable
    )
    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="queue"]').click()

    page.locator(INPUT).fill("the draft stays exactly here")
    page.locator(INPUT).evaluate("box => box.setSelectionRange(9, 9)")
    page.evaluate("window.__rememberConversationInput()")
    expected_draft = {"text": "the draft stays exactly here", "start": 9, "end": 9}

    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    assert draft(page) == expected_draft, (draft(page), expected_draft)
    assert page.evaluate("window.__conversationInputSurvived()") is True
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Queue"

    # A named pair in the header, and the F shortcut, switch the lens in place. Both
    # names stay on the screen and the pressed one says where you are. Editable controls
    # keep ordinary F input, and modified shortcuts do nothing.
    focus_choice = page.locator('[data-conversation-lens-choice="focus"]')
    full_choice = page.locator('[data-conversation-lens-choice="full"]')

    def lens_now():
        assert focus_choice.get_attribute("aria-pressed") != full_choice.get_attribute(
            "aria-pressed"
        ), "exactly one lens is pressed"
        return "focus" if focus_choice.get_attribute("aria-pressed") == "true" else "full"

    assert focus_choice.inner_text() == "Focus"
    assert full_choice.inner_text() == "Full"
    assert lens_now() == "focus"
    full_choice.click()
    assert lens_now() == "full"
    page.keyboard.press("f")
    assert lens_now() == "focus"
    page.keyboard.press("Control+f")
    assert lens_now() == "focus"
    page.locator(INPUT).press("f")
    assert lens_now() == "focus"
    expected_draft = draft(page)

    # Ticket content dismisses opened in one step without replacing or clearing the draft.
    page.locator("[data-ticket-behind]").click()
    state(page, "rest")
    assert draft(page) == expected_draft, (draft(page), expected_draft)
    assert page.evaluate("window.__conversationInputSurvived()") is True

    # A press beside the centered pane has the same dismissal contract.
    page.locator(INPUT).click()
    expected_draft = draft(page)
    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    layer = page.locator("[data-conversation-layer-host]").bounding_box()
    pane = page.locator(PANE).bounding_box()
    assert layer is not None and pane is not None
    page.mouse.click(layer["x"] + 2, layer["y"] + layer["height"] / 2)
    state(page, "rest")
    assert draft(page) == expected_draft, (draft(page), expected_draft)

    # A reader's anchor survives a height change.
    page.locator(INPUT).click()
    state(page, "peeked")
    thread = page.locator(THREAD)
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      return thread && thread.scrollHeight > thread.clientHeight && thread.scrollTop > 0;
    }""")
    thread.hover()
    page.mouse.wheel(0, -550)
    page.wait_for_timeout(50)
    anchored = first_visible_line(page)
    assert anchored is not None
    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    restored = first_visible_line(page)
    assert restored is not None
    assert restored["text"] == anchored["text"]
    assert abs(restored["top"] - anchored["top"]) <= 3, (anchored, restored)

    # Removing a screenful above the reader moves the thread, not their anchored line.
    page.evaluate("window.__dropFirstConversationLines(6)")
    page.wait_for_function("""() =>
      document.querySelectorAll('[data-conversation-row="agent_message"]').length === 22
    """)
    after_removal = wait_for_anchor(page, anchored)
    assert after_removal["scrollTop"] < anchored["scrollTop"], (anchored, after_removal)

    # An existing row growing above the reader produces the equal opposite correction.
    page.evaluate("window.__growFirstConversationLine()")
    page.get_by_text(
        "This existing line became much taller above the reader.", exact=False
    ).wait_for()
    after_growth = wait_for_anchor(page, after_removal)
    assert after_growth["scrollTop"] > after_removal["scrollTop"], (after_removal, after_growth)

    # Latest resumes following, and a newly appended line stays in sight.
    page.locator('button[aria-label="Jump to latest message"]').click()
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      return thread.scrollHeight - thread.scrollTop - thread.clientHeight <= 2;
    }""")
    before = thread.evaluate("node => node.scrollTop")
    page.evaluate("window.__appendConversationLine()")
    page.get_by_text("Newest appended line 28", exact=True).wait_for()
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      const newest = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')].at(-1);
      return newest.getBoundingClientRect().bottom <= thread.getBoundingClientRect().bottom + 1;
    }""")
    assert thread.evaluate("node => node.scrollTop") >= before

    # The task strip and composer remain visible in peeked and opened states.
    page.locator("[data-ticket-behind]").click()
    state(page, "rest")
    page.evaluate("window.__showActivePlan()")
    page.locator(INPUT).click()
    state(page, "peeked")
    assert page.locator("[data-conversation-task-strip]").is_visible()
    assert page.locator("[data-conversation-composer]").is_visible()
    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    assert page.locator("[data-conversation-task-strip]").is_visible()
    assert page.locator("[data-conversation-composer]").is_visible()

    # Return to steer for the send-boundary scenarios below.
    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="steer"]').click()

    # The steer selection and its queued fallback reason reach the held row.
    page.locator(INPUT).fill("default steer")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sentModes?.length === 1")
    assert page.evaluate("window.__sentModes") == ["steer"]
    page.locator('[data-conversation-held-row="composer-fallback"]').wait_for()
    assert (
        "turn did not accept steering"
        in page.locator("[data-conversation-held-stack]").inner_text()
    )

    # An explicit chooser selection reaches the send boundary unchanged.
    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="queue"]').click()
    page.locator(INPUT).fill("explicit queue")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sentModes?.length === 2")
    assert page.evaluate("window.__sentModes") == ["steer", "queue"]
    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="steer"]').click()

    # A run change remains attached to the default steer send. The server-visible
    # fallback is a queued message that explains it is waiting to apply that change.
    page.locator("[data-conversation-picker-model] [data-conversation-picker-trigger]").click()
    page.locator('[data-conversation-picker-choice="sonnet"]').click()
    page.locator(INPUT).fill("steer with a run change")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sentModes?.length === 3")
    assert page.evaluate("window.__sentModes") == ["steer", "queue", "steer"]
    assert page.evaluate("window.__sentRuns[2].model") == "sonnet"
    assert "apply the run change" in page.locator("[data-conversation-held-stack]").inner_text()

    # The server capability controls one provider-neutral steering action.
    page.evaluate("window.__showHeldPrompt(false)")
    page.locator('[data-conversation-held-row="held-1"]').wait_for()
    assert page.locator('[data-conversation-held-promote="steer"]').count() == 0
    page.evaluate("window.__showHeldPrompt(true)")
    steer = page.locator('[data-conversation-held-promote="steer"]')
    steer.wait_for()
    assert steer.inner_text() == "Steer"
    assert "Hermes" not in page.locator("[data-conversation-held-stack]").inner_text()

    # A send the server never answered sits above the composer, never under the rows, and
    # carries the two things this tab can do about it on its own.
    page.evaluate("window.__showUnansweredSend()")
    page.locator('[data-conversation-held-row="local:sent-into-the-dark"]').wait_for()
    assert "no answer came" in page.locator("[data-conversation-held-stack]").inner_text()
    assert page.locator('[data-conversation-outgoing="sent-into-the-dark"]').count() == 0
    assert page.locator('[data-conversation-held-promote="send_now"]').count() == 0
    assert page.locator('[data-conversation-held-promote="steer"]').count() == 0
    send_again = page.locator('[data-conversation-held-send-again="sent-into-the-dark"]')
    send_again.wait_for()
    if screenshot_dir:
        page.locator("[data-conversation-held-stack]").screenshot(
            path=os.path.join(screenshot_dir, "unanswered-send-above-the-composer.png")
        )
    send_again.click()
    page.wait_for_function("window.__sentAgain === 'sent-into-the-dark'")
    page.locator('[data-conversation-held-stop-drawing="sent-into-the-dark"]').click()
    page.wait_for_function("window.__stoppedDrawing === 'sent-into-the-dark'")

    # Opening is an open, not a restore. A reader who moves up the thread and then puts
    # the conversation away comes back to the end of it, which is where the newest work
    # is. Nothing else about keeping their place changes.
    while page.locator(f'{PANE}[data-conversation-state="rest"]').count() == 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(50)
    page.locator(INPUT).click()
    state(page, "peeked")
    page.wait_for_function(
        """() => {
            const t = document.querySelector('[data-conversation-thread]');
            return t && t.scrollHeight - t.scrollTop - t.clientHeight <= 2;
        }"""
    )
    page.locator(THREAD).hover()
    for _ in range(8):
        page.mouse.wheel(0, -400)
    page.wait_for_function(
        """() => {
            const t = document.querySelector('[data-conversation-thread]');
            return t && t.scrollHeight - t.scrollTop - t.clientHeight > 200;
        }"""
    )
    page.keyboard.press("Escape")
    state(page, "rest")
    page.locator(INPUT).click()
    state(page, "peeked")
    page.wait_for_function(
        """() => {
            const t = document.querySelector('[data-conversation-thread]');
            return t && t.scrollHeight - t.scrollTop - t.clientHeight <= 2;
        }"""
    )
    page.keyboard.press("Escape")
    state(page, "rest")

    # The line at rest says what it always said, and says the rest of it without words:
    # independent message attention and turn-status shapes, drawn together.
    page.evaluate("window.__showEveryMark()")
    marks = page.locator("[data-conversation-rest-marks] [data-conversation-mark]")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-mark]').length === 3"
    )
    assert marks.evaluate_all("els => els.map(e => e.dataset.conversationMark)") == [
        "running",
        "reply",
        "failed",
    ]
    assert page.locator("[data-conversation-rest-line]").inner_text() != ""
    assert page.get_by_text("waiting for you", exact=True).count() == 0

    # The right of the composer is what there is to do, in the agreed order. A composer
    # with nothing in it offers no Send at all — not a greyed-out one — and the control
    # that stops the work sits after the microphone, as the approved composer design specifies.
    def submit_order(page):
        return page.locator(".chat-submit > *").evaluate_all(
            """els => els.map(e =>
                e.dataset.conversationStop !== undefined ? 'stop'
              : e.dataset.voiceRecord !== undefined ? 'mic'
              : e.dataset.conversationSend !== undefined ? 'send'
              : 'delivery')"""
        )

    page.evaluate("window.__showQuietComposer()")
    page.locator(INPUT).click()
    state(page, "peeked")
    page.locator(INPUT).fill("")
    page.wait_for_function("() => document.querySelector('[data-conversation-send]') === null")
    assert submit_order(page) == ["delivery", "mic"]

    page.locator(INPUT).fill("something to send")
    page.wait_for_function("() => document.querySelector('[data-conversation-send]') !== null")
    assert submit_order(page) == ["delivery", "mic", "send"]

    page.evaluate("window.__showWorkRunning()")
    page.wait_for_function("() => document.querySelector('[data-conversation-stop]') !== null")
    assert submit_order(page) == ["delivery", "mic", "stop", "send"]
    page.locator(INPUT).fill("")
    page.wait_for_function("() => document.querySelector('[data-conversation-send]') === null")
    assert submit_order(page) == ["delivery", "mic", "stop"]

    # The head carries no worker title and no launch folder. The folder is still one
    # press away, where the card keeps everything else it is not showing.
    assert page.locator(".chat-lbl").count() == 0
    assert page.locator(".chat-head [data-conversation-workspace]").count() == 0

    # Back to rest, where the line lives.
    while page.locator(f'{PANE}[data-conversation-state="rest"]').count() == 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(50)

    # A pending ask stays in the text and creates no conversation attention dot.
    page.evaluate("window.__showRunningAndNeedsYou()")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-mark]').length === 1"
    )
    assert marks.evaluate_all("els => els.map(e => e.dataset.conversationMark)") == ["running"]
    assert page.locator("[data-conversation-rest-line]").inner_text() != ""
    request = page.locator('[data-conversation-ask="ask-mark"]')
    request.wait_for(state="visible")
    assert "may I run this" in request.inner_text()
    assert "Write the permission exercise file" in request.inner_text()
    assert page.evaluate("window.__permissionAnswers") == []

    # The actual pane sends the backend's exact option id only after an explicit answer.
    # Backend waiting and file outcomes are exercised separately against real adapters.
    for option_id in ["allow-file", "deny-file"]:
        page.evaluate("window.__showRunningAndNeedsYou()")
        page.locator(f'[data-conversation-ask-action="{option_id}"]').click()
        page.wait_for_selector('[data-conversation-ask="ask-mark"]', state="detached")
        assert page.evaluate("window.__permissionAnswers") == [
            {"askId": "ask-mark", "optionId": option_id}
        ]

    page.keyboard.press("Escape")
    state(page, "rest")

    # Ordinary output past the read position is not a reply anybody is owed.
    page.evaluate("window.__showOrdinaryOutputOnly()")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-mark]').length === 0"
    )
    assert page.locator("[data-conversation-rest-marks]").count() == 0

    # A completed owner turn uses the hidden ending for structure. The collapsed Focus
    # line shows the reply without a live working timer.
    page.evaluate("window.__showSettledFocusRestLine()")
    state(page, "rest")
    assert page.locator("[data-conversation-rest-line]").inner_text() == "settled reply"
    assert page.locator("[data-conversation-rest-bar] .c2-rest-working").count() == 0

    # Focus keeps settled work folded. Full reveals all commentary and every tool call.
    page.evaluate("window.__showSettledTurn()")
    assert page.get_by_text("hidden commentary", exact=True).count() == 0
    assert page.locator('[data-conversation-row="tool_call"]').count() == 0
    page.locator('[data-conversation-lens-choice="full"]').click()
    assert page.get_by_text("hidden commentary", exact=True).count() == 1
    assert page.locator('[data-conversation-row="tool_call"]').count() == 4
    assert page.locator("[data-conversation-work-fold]").count() == 0
    assert page.locator("[data-conversation-turn-fold]").count() == 0
    assert page.locator("[data-conversation-turn-count]").count() == 0
    assert page.locator("[data-conversation-turn-settled-head]").count() == 1

    # The two endings a reader has to act on are the two the pane spells out: a turn the
    # backend killed, named with the reason it gave, and a turn whose ending the record
    # will never contain.
    page.evaluate("window.__showTurnEndings()")
    page.get_by_text("backend exited", exact=True).wait_for()
    page.get_by_text("turn stopped without an ending", exact=True).wait_for()

    # The composer catalog menu opens from the keyboard, under a pointer left wherever it
    # was. A menu that mounts or reflows under a still pointer receives a mouse event at
    # the pointer's resting place, and that is the browser reporting geometry rather than
    # a person choosing a row. This section parks the pointer, so it runs last and on a
    # fresh page.
    page.reload(wait_until="domcontentloaded")
    state(page, "rest")
    page.locator(INPUT).click()
    state(page, "peeked")

    CATALOG_MENU = "[data-conversation-catalog]"
    CATALOG_ROW = "[data-conversation-catalog-entry]"

    def catalog_rows(page):
        return page.locator(CATALOG_ROW).evaluate_all(
            "rows => rows.map(row => row.getAttribute('data-conversation-catalog-entry'))"
        )

    def highlighted_entry(page):
        return page.evaluate(
            "() => document.querySelector('[data-conversation-catalog-active]')"
            "?.getAttribute('data-conversation-catalog-entry') ?? null"
        )

    def close_catalog_menu(page):
        page.locator(INPUT).press("Control+a")
        page.locator(INPUT).press("Backspace")
        page.wait_for_selector(CATALOG_MENU, state="detached")

    # Where the rows land while the whole catalog shows.
    page.locator(INPUT).type("/")
    page.wait_for_selector(CATALOG_MENU)
    every_entry = catalog_rows(page)
    assert every_entry[0] == "/recap", every_entry
    third_row = page.locator(CATALOG_ROW).nth(2).bounding_box()
    close_catalog_menu(page)

    # Park the pointer over the third row's place. Nothing clicks after this, because a
    # click would move the pointer and the whole point is that it never moves again.
    page.mouse.move(
        third_row["x"] + third_row["width"] / 2, third_row["y"] + third_row["height"] / 2
    )

    # The keyboard opens the menu and owns the highlight. Settle first, or the mouse event
    # has not arrived yet and the check proves nothing.
    page.locator(INPUT).type("/")
    page.wait_for_selector(CATALOG_MENU)
    page.wait_for_timeout(200)
    assert highlighted_entry(page) == every_entry[0], highlighted_entry(page)

    # Backspace widens the open list, so rows slide back under the still pointer. A new
    # list starts its highlight at the top, and geometry does not move it. The list must
    # really grow, or this checks nothing.
    page.locator(INPUT).type("r")
    page.wait_for_function(
        "count => document.querySelectorAll('[data-conversation-catalog-entry]').length < count",
        arg=len(every_entry),
    )
    narrowed = catalog_rows(page)
    page.locator(INPUT).press("Backspace")
    page.wait_for_function(
        "count => document.querySelectorAll('[data-conversation-catalog-entry]').length === count",
        arg=len(every_entry),
    )
    page.wait_for_timeout(200)
    widened = catalog_rows(page)
    assert len(widened) > len(narrowed), (narrowed, widened)
    assert highlighted_entry(page) == widened[0], (highlighted_entry(page), widened)

    # A real pointer move still moves the highlight. Without this the guard above can be
    # satisfied by a menu that never responds to the mouse at all.
    wanted = page.locator(CATALOG_ROW).nth(5)
    wanted_entry = wanted.get_attribute("data-conversation-catalog-entry")
    wanted.hover()
    page.wait_for_function(
        "entry => document.querySelector('[data-conversation-catalog-active]')"
        "?.getAttribute('data-conversation-catalog-entry') === entry",
        arg=wanted_entry,
    )

    # A resting card grows intrinsically inside the bounded conversation layer.
    # Its own height cannot be the ruler for its content cap: that creates a shrinking
    # ResizeObserver feedback loop. Collapse/reopen must preserve the long draft.
    page.reload(wait_until="domcontentloaded")
    state(page, "rest")
    long_draft = "\n".join("Resting draft line " + str(n) for n in range(40))
    page.locator(INPUT).fill(long_draft)
    state(page, "rest")

    def measure_writing_area():
        return page.locator(INPUT).evaluate("""e => ({
          height:e.getBoundingClientRect().height,line:parseFloat(getComputedStyle(e).lineHeight),
          overflow:getComputedStyle(e).overflowY})""")

    def settle_frames():
        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve))))"
        )

    settle_frames()
    resting = measure_writing_area()
    assert resting["height"] >= resting["line"] * 3, resting
    assert resting["height"] <= resting["line"] * 12 + 1, resting
    assert resting["overflow"] == "auto", resting
    for _ in range(3):
        settle_frames()
        assert measure_writing_area() == resting
    page.locator(INPUT).click()
    state(page, "peeked")
    settle_frames()
    peeked = measure_writing_area()
    assert peeked["height"] >= peeked["line"] * 2, peeked
    page.keyboard.press("Escape")
    state(page, "rest")
    settle_frames()
    assert measure_writing_area() == resting
    page.locator(INPUT).click()
    state(page, "peeked")
    settle_frames()
    assert measure_writing_area() == peeked
    assert page.locator(INPUT).input_value() == long_draft
    assert not browser_errors, browser_errors

    # Former mocked E2E queue matrix, in this existing build/server/browser host.
    held = [
        {
            "key": f"held-{number}",
            "heldPromptId": f"held-{number}",
            "content": [{"piece": "text", "text": f"queued message {number}"}],
            "senderLabel": "owner",
            "senderMessageId": f"sender-{number}",
            "sentAtUnixMilliseconds": 1_700_000_000_000 + number,
            "state": "held",
            "queueReason": "requested",
        }
        for number in range(1, 7)
    ]
    actions = []
    page.context.route("**/api/test/queue-matrix", lambda route: route.fulfill(json=held))

    def queue_api(route):
        request = route.request
        path = request.url.split("?", 1)[0]
        if request.method == "DELETE":
            held_id = path.rsplit("/", 1)[-1]
            actions.append([held_id, "discard"])
            held[:] = [row for row in held if row["heldPromptId"] != held_id]
            route.fulfill(json={"discarded": True})
        else:
            assert request.method == "POST" and path.endswith("/promote"), path
            held_id = path.rsplit("/", 2)[-2]
            mode = request.post_data_json["mode"]
            assert mode in ("steer", "send_now"), mode
            actions.append([held_id, mode])
            held[:] = [row for row in held if row["heldPromptId"] != held_id]
            route.fulfill(
                json={"promoted": True, "fate": "injected" if mode == "steer" else "started"}
            )

    page.context.route(
        "**/api/conversation/conversations/browser-fixture/held-prompts/**", queue_api
    )
    desktop = page.context.new_page()
    phone = page.context.new_page()
    phone.set_viewport_size({"width": 390, "height": 844})
    for queued_page in (desktop, phone):
        queued_page.on("pageerror", lambda error: browser_errors.append(str(error)))
        queued_page.goto([__file__, *urls][1], wait_until="domcontentloaded")
        state(queued_page, "rest")
        queued_page.evaluate("window.__loadQueueMatrix()")
        state(queued_page, "opened")
        queued_page.wait_for_selector("[data-conversation-held-row]")
        assert queued_page.locator(
            "[data-conversation-held-row] .chat-qrow-txt"
        ).all_inner_texts() == [f"queued message {number}" for number in range(1, 7)]

    def control_signature(page):
        return page.evaluate(
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
        )

    desktop_controls = control_signature(desktop)
    phone_controls = control_signature(phone)
    if screenshot_dir:
        desktop.screenshot(path=os.path.join(screenshot_dir, "queue-desktop.png"))
        phone.screenshot(path=os.path.join(screenshot_dir, "queue-phone.png"))
    assert desktop_controls == phone_controls, (desktop_controls, phone_controls)
    assert desktop_controls["inputWrap"] == "nowrap"

    phone_layout = phone.evaluate(
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
    )
    assert phone_layout["stackScrollHeight"] > phone_layout["stackHeight"]
    assert phone_layout["textWhiteSpace"] == "nowrap"
    assert phone_layout["documentWidth"] <= phone_layout["viewportWidth"]
    # The queue-only host uses TicketRoute's actual named size container and column
    # rules. A narrow test column must not conceal clipped controls off the phone.
    phone_bounds = phone.evaluate("""() => {
        const column = document.querySelector('.conversation-column').getBoundingClientRect();
        const pane = document.querySelector('[data-conversation-pane]').getBoundingClientRect();
        const controls = [...document.querySelectorAll(
            '[data-conversation-held-row] button, .chat-foot button'
        )].map(button => {
            const bounds = button.getBoundingClientRect();
            return { label: button.getAttribute('aria-label') || button.textContent.trim(),
                left: bounds.left, right: bounds.right, width: bounds.width };
        });
        return { columnWidth: column.width, viewportWidth: innerWidth,
            paneLeft: pane.left, paneRight: pane.right, controls };
    }""")
    assert abs(phone_bounds["columnWidth"] - phone_bounds["viewportWidth"]) <= 1, phone_bounds
    assert (
        0 <= phone_bounds["paneLeft"] < phone_bounds["paneRight"] <= phone_bounds["viewportWidth"]
    ), phone_bounds
    for control in phone_bounds["controls"]:
        assert control["width"] > 0, control
        assert (
            phone_bounds["paneLeft"]
            <= control["left"]
            < control["right"]
            <= phone_bounds["paneRight"]
        ), control

    desktop.click('[data-conversation-held-promote="steer"][data-held-prompt-id="held-1"]')
    desktop.wait_for_function(
        "document.querySelectorAll('[data-conversation-held-row]').length === 5"
    )
    assert actions[-1] == ["held-1", "steer"]
    phone.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    phone.wait_for_function(
        "document.querySelectorAll('[data-conversation-held-row]').length === 5"
    )
    assert "queued message 1" not in phone.inner_text("[data-conversation-held-stack]")
    phone.click('[data-conversation-held-discard="held-2"]')
    phone.wait_for_function(
        "document.querySelectorAll('[data-conversation-held-row]').length === 4"
    )
    assert actions[-1] == ["held-2", "discard"]
    desktop.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    desktop.wait_for_function(
        "document.querySelectorAll('[data-conversation-held-row]').length === 4"
    )
    desktop.click('[data-conversation-held-promote="send_now"][data-held-prompt-id="held-3"]')
    desktop.wait_for_function(
        "document.querySelectorAll('[data-conversation-held-row]').length === 3"
    )
    assert actions[-1] == ["held-3", "send_now"]
    desktop.close()
    phone.close()
    assert not browser_errors, browser_errors

    context.tracing.stop()

    print("conversation-pane-browser.test.mjs: all assertions passed")


def run(browser, urls):
    from tests.e2e.harness import browser_failure_evidence

    with browser_failure_evidence(browser, "conversation-pane-browser"):
        _run(browser, urls)
