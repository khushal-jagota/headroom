# ruff: noqa: E501
"""One persistent feedback composer, entered through desktop or compact navigation."""

import os
from pathlib import Path

from playwright.sync_api import expect


def run(browser, urls):

    page = browser.new_page(viewport={"width": 1280, "height": 844})
    # Only capture's bounded count is global; opening it must not read full history.
    page.goto(urls[0] + "#/unknown", wait_until="networkidle")
    trigger = page.locator("[data-feedback-trigger]")
    trigger.click()
    expect(page.locator("[data-feedback-capture]")).to_have_count(1)
    expect(page.locator("[data-feedback-view]")).to_contain_text("1 open")
    shell_requests = page.evaluate("window.__requests()")
    assert any(r["path"] == "/api/resource-body?key=%2Fapi%2Ffeedback%2Fcount" for r in shell_requests)
    assert not any(r["path"] == "/api/feedback" for r in shell_requests)
    # The count is the inbox entry, replacing the former Feedback icon badge.
    assert page.locator('[aria-label="Feedback"] .count-badge').count() == 0
    page.locator("[data-feedback-view]").click()
    page.wait_for_function("location.hash === '#/feedback'")
    expect(page.locator('[data-feedback-note="f_1"]')).to_be_visible()
    assert any(r["path"] == "/api/feedback" for r in page.evaluate("window.__requests()"))

    # Rendered status owns its transition lifecycle; pure resolution cannot prove
    # healthy removal, rapid reversal or reduced-motion card behavior.
    status = page.locator('.rail-foot .status-card')
    for state, word in [("preparing", "Deploying"), ("problem", "Problem"), ("restarting", "Deploying")]:
        page.evaluate("state => window.__deployment(state)", state)
        expect(status).to_have_text(word)
    page.evaluate("window.__deployment('idle')")
    expect(status).to_have_count(0)
    page.evaluate("window.__connection('reconnecting')")
    expect(status).to_have_text("Reconnecting")
    page.evaluate("window.__connection('connected')")
    expect(status).to_have_count(0)
    page.evaluate("window.__deployment('preparing')")
    expect(status).to_have_text("Deploying")
    page.evaluate("async () => { await window.__deployment('idle'); await window.__deployment('problem'); }")
    expect(status).to_have_text("Problem")
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("window.__deployment('idle')")
    expect(status).to_have_count(0)
    page.evaluate("window.__deployment('preparing')")
    expect(status).to_have_text("Deploying")
    page.emulate_media(reduced_motion="no-preference")

    # Closing keeps a device-local draft and restores it.
    trigger = page.locator("[data-feedback-trigger]")
    trigger.click()
    input_box = page.locator("[data-feedback-input]")
    assert input_box.evaluate("element => element === document.activeElement")
    assert page.locator(".fb-context .chip").inner_text() == "Feedback"
    input_box.fill("unfinished")
    page.keyboard.press("Escape")
    assert page.evaluate("localStorage.getItem('panels.feedback.draft')") == "unfinished"
    assert trigger.evaluate("element => element === document.activeElement")
    page.reload(wait_until="networkidle")
    page.locator('[data-feedback-note="f_1"]').wait_for()
    trigger = page.locator("[data-feedback-trigger]")
    assert page.evaluate("localStorage.getItem('panels.feedback.draft')") == "unfinished"
    trigger.click()
    input_box = page.locator("[data-feedback-input]")
    assert input_box.input_value() == "unfinished"

    expect(page.locator('.rail-foot [data-shell-status]')).to_contain_text("Deploying")
    page.wait_for_function("""() => {
      const capture=document.querySelector('[data-feedback-capture]')?.getBoundingClientRect();
      const foot=document.querySelector('.rail-foot')?.getBoundingClientRect();
      return capture && foot && capture.bottom <= foot.top + 1;
    }""")
    capture = page.locator('[data-feedback-capture]').bounding_box()
    foot = page.locator('.rail-foot').bounding_box()
    assert capture and foot and capture['y'] + capture['height'] <= foot['y'] + 1
    evidence = Path(os.environ["PANELS_SIDEBAR_ARTIFACT_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / "feedback-desktop.png"))

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
    more = phone.locator('[data-screen="more"]')
    more.click()
    phone.locator('[data-shell-more-menu] [aria-label="Feedback"]').click()
    expect(phone.locator('[data-shell-more-menu]')).to_have_count(0)
    expect(phone.locator('[data-feedback-capture]')).to_have_count(1)
    phone.wait_for_function("""() => {
      const sheet=document.querySelector('[data-feedback-capture]')?.getBoundingClientRect();
      const nav=document.querySelector('.workspace-mobile-nav')?.getBoundingClientRect();
      return sheet && nav && sheet.bottom <= nav.top + 1;
    }""")
    sheet = phone.locator("[data-feedback-capture]").bounding_box()
    nav = phone.locator(".workspace-mobile-nav").bounding_box()
    assert sheet and nav and sheet["y"] + sheet["height"] <= nav["y"] + 1, (sheet, nav)
    phone_input = phone.locator("[data-feedback-input]")
    assert phone_input.get_attribute("enterkeyhint") == "done"
    assert phone_input.evaluate("element => getComputedStyle(element).fontSize") == "16px"
    assert phone.locator("[data-feedback-save]").is_visible()
    evidence = Path(os.environ["PANELS_SIDEBAR_ARTIFACT_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)
    phone.screenshot(path=str(evidence / "feedback-mobile.png"))
    phone.keyboard.press("Escape")
    expect(more).to_be_focused()
    # A composer remains the same instance and keeps its draft through a width change.
    phone.locator('[data-screen="more"]').click()
    phone.locator('[data-shell-more-menu] [aria-label="Feedback"]').click()
    phone_input.fill("resized draft")
    phone.set_viewport_size({"width": 900, "height": 844})
    expect(phone_input).to_have_value("resized draft")
    phone.keyboard.press("Escape")
    expect(phone.locator('[data-feedback-trigger]')).to_be_focused()
    phone_context.close()
    pass  # Shared runner owns the browser lifecycle.
