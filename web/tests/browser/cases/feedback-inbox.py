# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    page = browser.new_page()
    # The global shell reads only the bounded count; the full history belongs to its route.
    page.goto([__file__, *urls][1] + "#/unknown", wait_until="networkidle")
    page.locator('[data-screen="more"]').click()
    page.locator(".fb-count").wait_for()
    shell_requests = page.evaluate("window.__requests()")
    assert any(r["path"] == "/api/feedback/count" for r in shell_requests)
    assert not any(r["path"] == "/api/feedback" for r in shell_requests)
    page.keyboard.press("Escape")
    page.evaluate("window.location.hash = '#/feedback'")
    page.locator('[data-screen="feedback"]').wait_for()
    assert any(r["path"] == "/api/feedback" for r in page.evaluate("window.__requests()"))
    assert page.locator('[data-feedback-note="f_1"]').count() == 1
    page.locator('[data-screen="more"]').click()
    assert page.locator(".fb-count").inner_text() == "1"
    page.keyboard.press("Escape")

    # Closing keeps a device-local draft and restores it.
    trigger = page.locator("[data-feedback-trigger]")
    trigger.click()
    input_box = page.locator("[data-feedback-input]")
    assert input_box.evaluate("element => element === document.activeElement")
    assert page.locator(".fb-context .chip").inner_text() == "Feedback"
    input_box.fill("unfinished")
    page.keyboard.press("Escape")
    assert trigger.get_attribute("data-draft") == "true"
    assert trigger.evaluate("element => element === document.activeElement")
    page.reload(wait_until="networkidle")
    page.locator('[data-screen="feedback"]').wait_for()
    trigger = page.locator("[data-feedback-trigger]")
    assert trigger.get_attribute("data-draft") == "true"
    trigger.click()
    input_box = page.locator("[data-feedback-input]")
    assert input_box.input_value() == "unfinished"

    # Shift+Enter inserts a line; Enter saves with the current route context.
    input_box.fill("first line")
    page.keyboard.press("Shift+Enter")
    input_box.type("second line")
    page.keyboard.press("Enter")
    page.locator("[data-feedback-toast]").wait_for()
    toast_text = page.locator("[data-feedback-toast]").inner_text()
    assert toast_text == "Saved to Feedback · View", repr(toast_text)
    posted = [
        r
        for r in page.evaluate("window.__requests()")
        if r["path"] == "/api/feedback" and r["method"] == "POST"
    ][-1]
    assert posted["body"] == {
        "text": "first line\nsecond line",
        "page_address": "#/feedback",
        "page_label": "Feedback",
    }

    # The toast action returns from another screen to the saved canonical note.
    page.evaluate("location.hash = '#/unknown'")
    page.locator("[data-feedback-toast] a", has_text="View").click()
    page.wait_for_function("location.hash === '#/feedback'")
    page.locator("[data-feedback-note]", has_text="first line\nsecond line").wait_for()

    # Failure leaves the text and changes the retry action.
    trigger.click()
    input_box.fill("fail")
    page.keyboard.press("Enter")
    page.locator(".error-line").wait_for()
    assert input_box.input_value() == "fail"
    assert page.locator("[data-feedback-save]").inner_text() == "Try again"
    page.keyboard.press("Escape")

    # Dismiss moves immediately; Undo reopens through the same canonical action.
    page.locator('[data-feedback-note="f_1"] [data-feedback-action="dismiss"]').click()
    page.locator("[data-feedback-toast] button", has_text="Undo").click()
    page.locator('[data-feedback-note="f_1"]').wait_for()
    paths = [r["path"] for r in page.evaluate("window.__requests()")]
    assert "/api/feedback/f_1/dismiss" in paths
    assert "/api/feedback/f_1/reopen" in paths

    # At phone size the capture becomes a touch-safe sheet above the bottom nav.
    phone_context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    phone = phone_context.new_page()
    phone.goto([__file__, *urls][1] + "#/feedback", wait_until="networkidle")
    phone.locator("[data-feedback-trigger]").click()
    sheet = phone.locator("[data-feedback-capture]").bounding_box()
    nav = phone.locator(".shell-nav").bounding_box()
    assert sheet and nav and sheet["y"] + sheet["height"] <= nav["y"] + 1
    phone_input = phone.locator("[data-feedback-input]")
    assert phone_input.get_attribute("enterkeyhint") == "done"
    assert phone_input.evaluate("element => getComputedStyle(element).fontSize") == "16px"
    assert phone.locator("[data-feedback-save]").is_visible()
    phone_context.close()
    pass  # Shared runner owns the browser lifecycle.
