# ruff: noqa: E501
"""Shared browser case with canonical navigation-source coverage."""


def run(browser, urls):
    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.set_default_timeout(5000)
    # Arm before each trigger: polling a locator first could consume the entrance.
    page.add_init_script("""window.__armReadyFrame = text => {
      window.__readyFrame = null;
      const sample = () => {
        const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
        let node;
        while ((node=walker.nextNode())) {
          if (node.textContent.trim() !== text) continue;
          const painted=node.parentElement;
          if (!painted.closest('[data-conversation-thread]') || !painted.getClientRects().length) continue;
          const ancestors=[];
          for (let element=painted;element;element=element.parentElement) {
            const style=getComputedStyle(element);
            ancestors.push({tag:element.tagName,classes:element.className,
              opacity:+style.opacity,transform:style.transform,
              animations:element.getAnimations().filter(a=>a.playState!=='finished').map(a=>a.animationName || 'script')});
          }
          window.__readyFrame={text,ancestors}; return;
        }
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    };""")

    def arm_ready(text):
        page.evaluate("text=>window.__armReadyFrame(text)", text)

    def assert_ready():
        page.wait_for_function("window.__readyFrame !== null")
        frame = page.evaluate("window.__readyFrame")
        assert frame['ancestors'], frame
        assert all(node['opacity'] == 1 and node['transform'] == 'none' and not node['animations']
                   for node in frame['ancestors']), frame

    page.goto([__file__, *urls][1], wait_until="domcontentloaded")
    page.wait_for_function("window.__tailExists?.('conversation-a')")
    page.wait_for_function("window.__navigation?.state.connection === 'subscribed'")
    assert page.evaluate("window.__sameNavigation") is True
    assert page.evaluate("window.__navigationSnapshots") == 1
    assert page.evaluate("window.__navigationStreams") == 1
    full_choice = page.locator('[data-conversation-lens-choice="full"]')
    focus_choice_first = page.locator('[data-conversation-lens-choice="focus"]')
    assert focus_choice_first.get_attribute("aria-pressed") == "true"
    full_choice.click()
    assert full_choice.get_attribute("aria-pressed") == "true"
    assert focus_choice_first.get_attribute("aria-pressed") == "false"
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
    page.evaluate(
        "window.__emitConversationRow('conversation-a', {\n      conversation_id: 'conversation-a',\n      sequence: 9,\n      kind: 'prompt',\n      payload: { text: 'stale A row', sender_label: 'owner', mode: 'queue' },\n      created_at: 9\n    })"
    )
    page.wait_for_timeout(100)
    assert page.get_by_text("stale A row", exact=True).count() == 0
    assert page.evaluate("window.__ownerReads") == []
    assert page.evaluate("window.__navigationSnapshots") == 1
    arm_ready("current B reply")
    page.evaluate(
        "window.__emitConversationRow('conversation-b', {\n      conversation_id: 'conversation-b',\n      sequence: 4,\n      kind: 'message_to_owner',\n      payload: {\n        text: 'current B reply',\n        sender_label: 'Ticket B',\n        sender: { kind: 'ticket', id: 'b' },\n        recipient: { kind: 'owner', id: 'owner' }\n      },\n      created_at: 10\n    })"
    )
    assert_ready()
    page.get_by_text("current B reply", exact=True).wait_for()
    page.wait_for_function("window.__ownerReads.length === 1")
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4}
    ]
    page.evaluate("""() => {
      window.__armReadyFrame('current B reply');
      document.querySelector('[data-conversation-lens-choice="full"]').click();
    }""")
    assert_ready()
    assert full_choice.get_attribute("aria-pressed") == "true"
    page.evaluate(
        "window.__emitConversationRow('conversation-b', {\n      conversation_id: 'conversation-b',\n      sequence: 5,\n      kind: 'message_to_owner',\n      payload: {\n        text: 'current B full reply',\n        sender_label: 'Ticket B',\n        sender: { kind: 'ticket', id: 'b' },\n        recipient: { kind: 'owner', id: 'owner' }\n      },\n      created_at: 11\n    })"
    )
    page.get_by_text("current B full reply", exact=True).wait_for()
    page.wait_for_function("window.__ownerReads.length === 2")
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4},
        {"conversationId": "conversation-b", "sequence": 5},
    ]
    page.evaluate("window.__delayNextASnapshot()")
    page.evaluate("window.__switchConversation('conversation-a')")
    page.wait_for_function("window.__delayedASnapshotRequested()")
    page.evaluate("""() => {
      window.__armReadyFrame('current B reply');
      window.__switchConversation('conversation-b');
    }""")
    assert_ready()
    page.get_by_text("current B reply", exact=True).wait_for()
    page.evaluate("window.__releaseDelayedASnapshot()")
    page.wait_for_timeout(150)
    assert page.get_by_text("current B reply", exact=True).count() == 1
    assert full_choice.get_attribute("aria-pressed") == "true"
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4},
        {"conversationId": "conversation-b", "sequence": 5},
    ]

    def emit_message(sequence):
        page.evaluate(
            "sequence => window.__emitConversationRow('conversation-b', {\n          conversation_id: 'conversation-b', sequence, kind: 'message_to_owner',\n          payload: { text: 'B message ' + sequence, sender_label: 'Ticket B',\n            sender: {kind: 'ticket', id: 'b'}, recipient: {kind: 'owner', id: 'owner'} },\n          created_at: sequence\n        })",
            sequence,
        )

    page.evaluate("window.__setConversationState('rest')")
    page.wait_for_function("document.querySelector('[data-conversation-state=rest]')")
    emit_message(6)
    page.wait_for_function("document.querySelector('[data-conversation-mark=reply]')")
    assert len(page.evaluate("window.__ownerReads")) == 2
    arm_ready("B message 6")
    page.evaluate("window.__setConversationState('peeked')")
    assert_ready()
    page.wait_for_function("window.__ownerReads.length === 3")
    assert page.evaluate("window.__ownerReads.at(-1).sequence") == 6
    for focused, visible, sequence in [(False, True, 7), (True, False, 8)]:
        page.evaluate("([f,v]) => window.__setAttention(f,v)", [focused, visible])
        emit_message(sequence)
        if visible:
            page.get_by_text("B message " + str(sequence), exact=True).wait_for()
        else:
            # Hidden pages keep authoritative rest facts and release transcript bodies.
            assert page.get_by_text("B message " + str(sequence), exact=True).count() == 0
        assert page.evaluate("window.__ownerReads.at(-1).sequence") == sequence - 1
        page.evaluate("""sequence => {
          window.__armReadyFrame('B message ' + sequence);
          window.__setAttention(true,true);
        }""", sequence)
        assert_ready()
        page.wait_for_function(
            "sequence => window.__ownerReads.at(-1).sequence === sequence", arg=sequence
        )
    page.evaluate("window.__delayNextRead()")
    emit_message(9)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 9")
    page.evaluate("window.__setConversationState('rest')")
    page.wait_for_function("document.querySelector('[data-conversation-state=rest]')")
    emit_message(10)
    page.evaluate("window.__releaseRead()")
    page.wait_for_function("document.querySelector('[data-conversation-mark=reply]')")
    assert page.evaluate("window.__ownerReads.at(-1).sequence") == 9
    arm_ready("B message 10")
    page.evaluate("window.__setConversationState('opened')")
    assert_ready()
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 10")
    page.wait_for_function("window.__readRefreshes === 7")
    page.wait_for_function("window.__navigationSnapshots === 8")

    def emit_tool(sequence):
        page.evaluate(
            "sequence => window.__emitConversationRow('conversation-b', {\n          conversation_id: 'conversation-b', sequence, kind: 'tool_call_started',\n          payload: { tool_call_id: 'tool-' + sequence, title: 'Tool ' + sequence,\n            tool_kind: 'read', detail: null }, created_at: sequence\n        })",
            sequence,
        )

    page.evaluate("window.__holdRead(11); window.__holdRead(12)")
    emit_message(11)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 11")
    emit_tool(12)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 12")
    assert page.evaluate("window.__readRefreshes") == 7
    page.evaluate("window.__finishRead(12)")
    page.wait_for_function("window.__navigationSnapshots === 9")
    page.wait_for_function("window.__readRefreshes === 8")
    page.evaluate("window.__finishRead(11)")
    page.wait_for_function("window.__navigationSnapshots === 10")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__readRefreshes") == 8
    assert page.evaluate("window.__ownerReads.filter(r => r.sequence === 12).length") == 1
    emit_tool(13)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 13")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__readRefreshes") == 8
    page.wait_for_function("window.__navigationSnapshots === 11")
    page.evaluate("window.__holdRead(14); window.__holdRead(15)")
    emit_message(14)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 14")
    emit_tool(15)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 15")
    page.evaluate("window.__finishRead(14, false)")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__readRefreshes") == 8
    assert page.evaluate("window.__navigationSnapshots") == 11
    page.evaluate("window.__setAttention(true,true)")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__ownerReads.filter(r => r.sequence === 15).length") == 1
    page.evaluate("window.__finishRead(15)")
    page.wait_for_function("window.__readRefreshes === 9")
    page.wait_for_function("window.__navigationSnapshots === 12")
    page.evaluate("window.__holdRead(16)")
    emit_message(16)
    page.wait_for_function("window.__ownerReads.at(-1).sequence === 16")
    page.evaluate("window.__switchConversation('conversation-a')")
    page.wait_for_timeout(100)
    page.evaluate("window.__finishRead(16)")
    page.wait_for_function("window.__readRefreshes === 10")
    page.wait_for_function("window.__navigationSnapshots === 13")
    page.evaluate(
        "window.__emitConversationRow('conversation-a', {\n      conversation_id: 'conversation-a', sequence: 1, kind: 'message_to_owner',\n      payload: {text: 'A first message', sender_label: 'Ticket A',\n        sender: {kind: 'ticket', id: 'a'}, recipient: {kind: 'owner', id: 'owner'}},\n      created_at: 1\n    })"
    )
    page.wait_for_function(
        "window.__ownerReads.at(-1).conversationId === 'conversation-a' && window.__ownerReads.at(-1).sequence === 1"
    )
    page.wait_for_function("window.__readRefreshes === 11")
    page.wait_for_function("window.__navigationSnapshots === 14")
    assert page.evaluate("window.__navigationStreams - window.__closedNavigationStreams") == 1
    page.evaluate(
        "Object.assign(window.__navigationBoundaries, {'conversation-a':1,'conversation-b':16})"
    )
    page.evaluate("window.__navigation.owner.recover()")
    page.wait_for_function(
        "window.__navigation.state.effective.conversations.get('conversation-b').message_for_owner.state === 'fresh'"
    )
    page.evaluate("window.__switchConversation('conversation-a')")
    page.wait_for_function("window.__tailExists('conversation-a')")
    page.evaluate("window.__holdRead(17); window.__holdRead(18)")
    for sequence in [17, 18]:
        page.evaluate(
            "sequence => window.__emitConversationRow('conversation-a', {\n          conversation_id:'conversation-a',sequence,kind:'message_to_owner',\n          payload:{text:'A delayed read '+sequence,sender_label:'Ticket A',sender:{kind:'ticket',id:'a'},recipient:{kind:'owner',id:'owner'}},created_at:sequence})",
            sequence,
        )
        page.wait_for_function(
            "sequence => window.__ownerReads.some(read=>read.conversationId==='conversation-a' && read.sequence===sequence)",
            arg=sequence,
        )
    page.evaluate("window.__switchConversation('conversation-b')")
    page.wait_for_function("window.__tailExists('conversation-b')")
    page.evaluate(
        "window.__navigationBoundaries['conversation-a']=17; window.__holdNextNavigation(); window.__finishRead(18)"
    )
    page.wait_for_function("window.__beforeRecover.at(-1)['conversation-a'].state==='unknown'")
    before_recovery = page.evaluate("window.__beforeRecover.at(-1)")
    assert before_recovery["conversation-a"]["state"] == "unknown"
    assert before_recovery["conversation-b"]["state"] == "fresh"
    page.evaluate("window.__releaseNavigation()")
    page.wait_for_function("window.__navigation.state.connection==='subscribed'")
    assert (
        page.evaluate(
            "window.__navigation.state.effective.conversations.get('conversation-a').message_for_owner.state"
        )
        == "unknown"
    )
    refreshes = page.evaluate("window.__readRefreshes")
    previous_snapshots = page.evaluate("window.__navigationSnapshots")
    page.evaluate("window.__finishRead(17)")
    page.wait_for_function("count=>window.__navigationSnapshots>count", arg=previous_snapshots)
    page.wait_for_function("window.__navigation.state.connection==='subscribed'")
    assert (
        page.evaluate(
            "window.__navigation.state.effective.conversations.get('conversation-a').message_for_owner.state"
        )
        == "unknown"
    )
    assert page.evaluate("window.__readRefreshes") == refreshes
    page.evaluate(
        "window.__navigationBoundaries['conversation-a']=18; window.__navigation.owner.recover()"
    )
    restored = page.evaluate(
        "window.__navigation.state.effective.conversations.get('conversation-a').message_for_owner"
    )
    assert restored["state"] == "fresh" and restored["provenance"]["boundary"] == 18
    page.evaluate("window.__unmountHost()")
    assert page.evaluate("window.__navigation.state.connection") == "disposed"
    assert page.evaluate("window.__navigationStreams - window.__closedNavigationStreams") == 0
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__tailExists?.('conversation-a')")
    # A fresh page starts Focus; explicit Full survives only within the mounted pane.
    assert focus_choice_first.get_attribute("aria-pressed") == "true"
    assert full_choice.get_attribute("aria-pressed") == "false"
    pass
    print("live-conversation-switch-browser.test.mjs: all assertions passed")
