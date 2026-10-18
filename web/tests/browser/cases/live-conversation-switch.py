# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.set_default_timeout(5_000)
    page.goto([__file__, *urls][1], wait_until="domcontentloaded")
    page.wait_for_function("window.__tailExists?.('conversation-a')")
    full_choice = page.locator('[data-conversation-lens-choice="full"]')
    focus_choice_first = page.locator('[data-conversation-lens-choice="focus"]')
    assert focus_choice_first.get_attribute("aria-pressed") == "true"
    full_choice.click()
    assert full_choice.get_attribute("aria-pressed") == "true"
    assert focus_choice_first.get_attribute("aria-pressed") == "false"
    # The pair is one control: the keyboard still moves it back and forth.
    page.keyboard.press("f")
    assert focus_choice_first.get_attribute("aria-pressed") == "true"
    page.keyboard.press("f")
    assert full_choice.get_attribute("aria-pressed") == "true"

    page.evaluate("window.__switchConversation('conversation-b')")
    page.wait_for_function("window.__tailExists?.('conversation-b')")
    assert full_choice.get_attribute("aria-pressed") == "true"

    page.locator('[aria-label="Conversation options"]').click()
    page.locator("[data-conversation-new-arm]").click()
    page.locator("[data-conversation-new-confirm]").click()
    assert full_choice.get_attribute("aria-pressed") == "true"
    page.evaluate("window.__switchConversation('conversation-b')")
    page.wait_for_function("window.__tailExists?.('conversation-b')")
    assert full_choice.get_attribute("aria-pressed") == "true"
    focus_choice = page.locator('[data-conversation-lens-choice="focus"]')
    focus_choice.click()
    assert focus_choice.get_attribute("aria-pressed") == "true"

    page.evaluate("""window.__emitConversationRow('conversation-a', {
      conversation_id: 'conversation-a',
      sequence: 9,
      kind: 'prompt',
      payload: { text: 'stale A row', sender_label: 'owner', mode: 'queue' },
      created_at: 9
    })""")
    page.wait_for_timeout(100)
    assert page.get_by_text("stale A row", exact=True).count() == 0
    assert page.evaluate("window.__ownerReads") == []

    page.evaluate("""window.__emitConversationRow('conversation-b', {
      conversation_id: 'conversation-b',
      sequence: 4,
      kind: 'message_to_owner',
      payload: {
        text: 'current B reply',
        sender_label: 'Ticket B',
        sender: { kind: 'ticket', id: 'b' },
        recipient: { kind: 'owner', id: 'owner' }
      },
      created_at: 10
    })""")
    page.get_by_text("current B reply", exact=True).wait_for()
    page.wait_for_function("window.__ownerReads.length === 1")
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4}
    ]
    full_choice.click()
    assert full_choice.get_attribute("aria-pressed") == "true"
    page.evaluate("""window.__emitConversationRow('conversation-b', {
      conversation_id: 'conversation-b',
      sequence: 5,
      kind: 'message_to_owner',
      payload: {
        text: 'current B full reply',
        sender_label: 'Ticket B',
        sender: { kind: 'ticket', id: 'b' },
        recipient: { kind: 'owner', id: 'owner' }
      },
      created_at: 11
    })""")
    page.get_by_text("current B full reply", exact=True).wait_for()
    page.wait_for_function("window.__ownerReads.length === 2")
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4},
        {"conversationId": "conversation-b", "sequence": 5},
    ]

    # A delayed snapshot also loses authority when B opens before it returns. Without
    # the post-await guard, A creates a new stream and replaces B's active feed.
    page.evaluate("window.__delayNextASnapshot()")
    page.evaluate("window.__switchConversation('conversation-a')")
    page.wait_for_function("window.__delayedASnapshotRequested()")
    page.evaluate("window.__switchConversation('conversation-b')")
    page.get_by_text("current B reply", exact=True).wait_for()
    page.evaluate("window.__releaseDelayedASnapshot()")
    page.wait_for_timeout(150)
    assert page.get_by_text("current B reply", exact=True).count() == 1
    assert full_choice.get_attribute("aria-pressed") == "true"
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4},
        {"conversationId": "conversation-b", "sequence": 5},
    ]

    # Snapshot latest_sequence is ahead of delivered rows at startup; it never grants
    # read credit. Rest, focus loss and hidden documents likewise cannot grant credit.
    def emit_message(sequence):
        page.evaluate(
            """sequence => window.__emitConversationRow('conversation-b', {
          conversation_id: 'conversation-b', sequence, kind: 'message_to_owner',
          payload: { text: 'B message ' + sequence, sender_label: 'Ticket B',
            sender: {kind: 'ticket', id: 'b'}, recipient: {kind: 'owner', id: 'owner'} },
          created_at: sequence
        })""",
            sequence,
        )

    page.evaluate("window.__setConversationState('rest')")
    page.wait_for_function("document.querySelector('[data-conversation-state=rest]')")
    emit_message(6)
    page.wait_for_function("document.querySelector('[data-conversation-mark=reply]')")
    assert len(page.evaluate("window.__ownerReads")) == 2
    page.evaluate("window.__setConversationState('peeked')")
    page.wait_for_function("window.__ownerReads.length === 3")
    assert page.evaluate("window.__ownerReads.at(-1).sequence") == 6

    for focused, visible, sequence in [(False, True, 7), (True, False, 8)]:
        page.evaluate("([f,v]) => window.__setAttention(f,v)", [focused, visible])
        emit_message(sequence)
        page.get_by_text("B message " + str(sequence), exact=True).wait_for()
        assert page.evaluate("window.__ownerReads.at(-1).sequence") == sequence - 1
        page.evaluate("window.__setAttention(true,true)")
        page.wait_for_function(
            "sequence => window.__ownerReads.at(-1).sequence === sequence", arg=sequence
        )

    # A response acknowledging one delivered position must not consume a newer
    # message that arrives after the owner closes the transcript.
    page.evaluate("window.__delayNextRead()")
    emit_message(9)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 9")
    page.evaluate("window.__setConversationState('rest')")
    page.wait_for_function("document.querySelector('[data-conversation-state=rest]')")
    emit_message(10)
    page.evaluate("window.__releaseRead()")
    page.wait_for_function("document.querySelector('[data-conversation-mark=reply]')")
    assert page.evaluate("window.__ownerReads.at(-1).sequence") == 9
    page.evaluate("window.__setConversationState('opened')")
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 10")

    page.wait_for_function("window.__readRefreshes === 7")

    def emit_tool(sequence):
        page.evaluate(
            """sequence => window.__emitConversationRow('conversation-b', {
          conversation_id: 'conversation-b', sequence, kind: 'tool_call_started',
          payload: { tool_call_id: 'tool-' + sequence, title: 'Tool ' + sequence,
            tool_kind: 'read', detail: null }, created_at: sequence
        })""",
            sequence,
        )

    # A newer acknowledgment can cover the same message first. The late older response
    # neither repeats invalidation nor moves the accepted position backward.
    page.evaluate("window.__holdRead(11); window.__holdRead(12)")
    emit_message(11)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 11")
    emit_tool(12)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 12")
    assert page.evaluate("window.__readRefreshes") == 7
    page.evaluate("window.__finishRead(12)")
    page.wait_for_function("window.__readRefreshes === 8")
    page.evaluate("window.__finishRead(11)")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__readRefreshes") == 8
    assert page.evaluate("window.__ownerReads.filter(r => r.sequence === 12).length") == 1
    emit_tool(13)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 13")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__readRefreshes") == 8

    # A failed earlier request must not suppress refresh when the overlapping request
    # succeeds, or erase that newer request's pending marker.
    page.evaluate("window.__holdRead(14); window.__holdRead(15)")
    emit_message(14)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 14")
    emit_tool(15)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 15")
    page.evaluate("window.__finishRead(14, false)")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__readRefreshes") == 8
    page.evaluate("window.__setAttention(true,true)")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__ownerReads.filter(r => r.sequence === 15).length") == 1
    page.evaluate("window.__finishRead(15)")
    page.wait_for_function("window.__readRefreshes === 9")

    # A successful response still refreshes canonical mounted queries after switching.
    # It does not write B's watermark into A or assume a reply-required dot cleared.
    page.evaluate("window.__holdRead(16)")
    emit_message(16)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 16")
    page.evaluate("window.__switchConversation('conversation-a')")
    page.wait_for_timeout(100)
    page.evaluate("window.__finishRead(16)")
    page.wait_for_function("window.__readRefreshes === 10")
    page.evaluate("""window.__emitConversationRow('conversation-a', {
      conversation_id: 'conversation-a', sequence: 1, kind: 'message_to_owner',
      payload: {text: 'A first message', sender_label: 'Ticket A',
        sender: {kind: 'ticket', id: 'a'}, recipient: {kind: 'owner', id: 'owner'}},
      created_at: 1
    })""")
    page.wait_for_function(
        "window.__ownerReads.at(-1).conversationId === 'conversation-a' && window.__ownerReads.at(-1).sequence === 1"
    )
    page.wait_for_function("window.__readRefreshes === 11")

    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__tailExists?.('conversation-a')")
    assert full_choice.get_attribute("aria-pressed") == "true"
    pass  # Shared runner owns the browser lifecycle.

    print("live-conversation-switch-browser.test.mjs: all assertions passed")
