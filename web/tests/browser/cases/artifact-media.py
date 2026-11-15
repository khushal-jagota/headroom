"""An embedded HTML artifact is an inline link, and a picture opens the expanded view.

The risk is layout and focus that only a real browser computes: the link staying in its
line of text, no frame being drawn, the viewer covering the page above everything, Esc
closing it and focus returning to the picture that opened it, at desktop and phone widths.
"""

import os

from playwright.sync_api import expect


def run(browser, urls):
    errors = []
    page = browser.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(urls[0])
    fixture = page.locator("[data-artifact-media-fixture]")
    for width in (1100, 390):
        page.set_viewport_size({"width": width, "height": 900})
        link = fixture.locator("a.file-preview-artifact-link")
        expect(link).to_have_text("the review plan")
        expect(link).to_have_attribute(
            "href", "#/preview?source=ticket&ticket=t_media&path=plan.html"
        )
        assert fixture.locator("iframe").count() == 0
        # The link sits in its sentence: one line box with the words around it.
        assert link.evaluate(
            "a => { const p = a.closest('p'); return p.getBoundingClientRect().height"
            " < 2 * parseFloat(getComputedStyle(p).lineHeight); }"
        ), "the artifact link must stay inline"
        wrapper = "a => getComputedStyle(a.closest('.file-preview')).display"
        assert link.evaluate(wrapper) == "inline"

        expect(fixture.locator(".file-preview-grid [data-media-open]")).to_have_count(2)
        picture = fixture.locator("[data-media-open]").first
        expect(picture.locator("img")).to_be_visible()
        box = picture.bounding_box()
        assert box and box["width"] <= min(width, 448) + 1, box
        proof = os.environ.get("PANELS_UI_PROOF_DIR")
        if proof:
            page.screenshot(path=f"{proof}/artifact-media-{width}.png", full_page=True)
        picture.click()
        viewer = page.locator("[data-media-viewer]")
        expect(viewer).to_be_visible()
        if proof:
            page.wait_for_timeout(400)
            page.screenshot(path=f"{proof}/artifact-media-viewer-{width}.png")
        expect(viewer).to_have_attribute("role", "dialog")
        expect(viewer).to_have_attribute("aria-modal", "true")
        expect(viewer.locator("img")).to_have_attribute("src", "/files/tickets/t_media/wide.svg")
        assert page.evaluate("getComputedStyle(document.body).overflow") == "hidden"
        page.keyboard.press("Escape")
        expect(viewer).to_have_count(0)
        expect(picture).to_be_focused()

        # Pictures pasted into a message share the box, the grid and the viewer.
        pasted = page.locator("[data-pasted-fixture] .file-preview-grid [data-media-open]")
        expect(pasted).to_have_count(2)
        pasted.first.click()
        expect(viewer.locator("img")).to_have_attribute("alt", "one.png")
        page.keyboard.press("Escape")
        expect(viewer).to_have_count(0)
        expect(pasted.first).to_be_focused()

    assert errors == [], errors
    page.close()
