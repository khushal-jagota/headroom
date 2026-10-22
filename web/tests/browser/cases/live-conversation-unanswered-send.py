# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    INPUT = "[data-conversation-input]"

    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.set_default_timeout(5_000)
    page.goto([__file__, *urls][1], wait_until="domcontentloaded")
    page.wait_for_function("window.__tailOpened?.() === 1")

    # A send the server never answers keeps its bubble and offers actions in place.
    page.locator(INPUT).fill("did this arrive")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sends?.length === 1")
    sent_id = page.evaluate("window.__sends[0].sender_message_id")
    bubble = page.locator("[data-conversation-outgoing='" + sent_id + "']")
    bubble.wait_for()
    bubble.evaluate("node => window.__firstBubble = node")
    page.wait_for_function("window.__firstBubble.innerText.includes('no answer came')")
    assert page.locator("[data-conversation-held-stack]").count() == 0

    # Neither of the record's own promotions is offered, because there is no held prompt.
    assert page.locator('[data-conversation-held-promote="send_now"]').count() == 0
    assert page.locator('[data-conversation-held-promote="steer"]').count() == 0

    # The server coming back is the cue to ask the record again. Nothing here guesses the
    # answer: the read is what settles it, and this pane only decides when to look.
    reads_before = page.evaluate("window.__recordReads")
    page.evaluate("window.__setServerReachable(true)")
    page.wait_for_function("window.__recordReads > " + str(reads_before))

    # Send again is a fresh message with its own name, and it takes the uncertain copy
    # away only once the send is away.
    page.evaluate("window.__theServerAnswers(true)")
    page.locator("[data-conversation-held-send-again='" + sent_id + "']").click()
    page.wait_for_function("window.__sends?.length === 2")
    assert page.evaluate("window.__sends[1].sender_message_id") != sent_id
    assert page.evaluate("window.__sends[1].content[0].text") == "did this arrive"
    assert page.evaluate("document.querySelector('[data-conversation-outgoing=\"' + window.__sends[1].sender_message_id + '\"]') === window.__firstBubble")
    page.wait_for_function("!window.__firstBubble.innerText.includes('no answer came')")

    # Stopping a copy from being drawn needs no held prompt either.
    page.evaluate("window.__theServerAnswers(false)")
    page.locator(INPUT).fill("and this one")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sends?.length === 3")
    second_id = page.evaluate("window.__sends[2].sender_message_id")
    page.locator("[data-conversation-held-stop-drawing='" + second_id + "']").click()
    page.wait_for_function("id => !document.querySelector('[data-conversation-outgoing=\"' + id + '\"]')", arg=second_id)

    # A send still on the wire when the page goes away, coming back to a server that is
    # also away. It says nothing at first, because at that point nothing is known.
    page.evaluate("window.__theSendHangs(true)")
    page.locator(INPUT).fill("in flight when the page went")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sends?.length === 4")
    page.evaluate("window.__theServerIsDown(true)")
    page.reload(wait_until="domcontentloaded")
    page.get_by_text("in flight when the page went", exact=True).wait_for()
    assert page.locator("[data-conversation-held-stack]").count() == 0

    # The server comes back. Nothing asked it yet, so nothing has been told — and then the
    # read happens, the record turns out not to have the message, and only now is it true
    # to say that nobody ever said whether it arrived.
    page.evaluate("window.__theServerIsDown(false)")
    page.evaluate("window.__setServerReachable(true)")
    page.wait_for_function("Array.from(document.querySelectorAll('[data-conversation-outgoing]')).some(node => node.innerText.includes('no answer came'))")
    pass  # Shared runner owns the browser lifecycle.

    print("live-conversation-unanswered-send-browser.test.mjs: all assertions passed")
