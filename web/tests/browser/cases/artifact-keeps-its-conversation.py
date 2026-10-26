"""An artifact opened from a conversation keeps that conversation under it.

The press that opens an artifact is claimed across the whole pane, the conversation
layer included. That is a fact about where the handler sits and about the browser
following a link, so neither half can be proved without a real browser.
"""


def run(browser, urls):

    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(urls[0])
    kept = page.locator('[data-pane-kept]')
    conversation = kept.locator("[data-conversation-pane]")
    artifact = kept.locator("[data-ticket-artifact]")
    address = page.locator('[data-address-kept]')

    def press_was_claimed(link, **modifiers):
        """Whether the pane took the press. The window reads the answer last, and stops
        the browser from acting on a press the pane left alone."""
        return link.evaluate(
            """(anchor, modifiers) => {
                let claimed = null;
                const last = event => { claimed = event.defaultPrevented; event.preventDefault(); };
                window.addEventListener('click', last);
                anchor.dispatchEvent(
                    new MouseEvent('click', {bubbles:true, cancelable:true, ...modifiers})
                );
                window.removeEventListener('click', last);
                return claimed;
            }""",
            modifiers,
        )

    # The conversation is the case the owner reported: the artifact belongs to another
    # Ticket and the conversation is the one they are reading.
    opened_at = page.url
    conversation.get_by_role("link", name="the plan", exact=True).click()
    expect(artifact).to_have_attribute("aria-label", "plan.md")
    expect(artifact.get_by_role("heading", name="The plan")).to_be_visible()
    expect(conversation).to_be_visible()
    assert page.url == opened_at, "opening the artifact left the page"

    # Close gives the pane back, with the conversation still there.
    artifact.get_by_role("button", name="Close", exact=True).click()
    expect(artifact).to_have_count(0)
    expect(conversation).to_be_visible()

    # A full conversation covers the artifact, so it steps back to its dock to let it
    # through, and Escape then belongs to the artifact.
    page.evaluate("window.__state('opened')")
    conversation.get_by_role("link", name="the plan", exact=True).click()
    expect(artifact).to_have_count(1)
    expect(conversation).to_have_attribute("data-conversation-state", "rest")
    page.keyboard.press("Escape")
    expect(artifact).to_have_count(0)

    # While the conversation is open over it, Escape is the conversation's.
    conversation.get_by_role("link", name="the plan", exact=True).click()
    page.evaluate("window.__state('opened')")
    page.keyboard.press("Escape")
    expect(artifact).to_have_count(1)
    page.evaluate("window.__state('rest')")
    page.keyboard.press("Escape")
    expect(artifact).to_have_count(0)

    # A document press is claimed on the same pane, and the two kinds that are not ours
    # stay with the browser from either layer.
    document = kept.locator("[data-doc]")
    assert press_was_claimed(document.get_by_role("link", name="Document plan", exact=True))
    expect(artifact).to_have_count(1)
    artifact.get_by_role("button", name="Close", exact=True).click()
    for name in ["Unsupported", "Marked download"]:
        assert not press_was_claimed(document.get_by_role("link", name=name, exact=True))
    assert not press_was_claimed(
        conversation.get_by_role("link", name="the plan", exact=True), metaKey=True
    )
    expect(artifact).to_have_count(0)

    # A pane whose host keeps the artifact writes through that host and shows what comes
    # back, which is how the Chief's artifact rides in the Workspace address.
    address.get_by_role("link", name="Open plan.md", exact=True).click()
    expect(address.locator("[data-ticket-artifact]")).to_have_attribute("aria-label", "plan.md")
    assert page.evaluate("window.__addressed()")["path"] == "plan.md"
    assert page.url == opened_at, "the address pane left the page"
