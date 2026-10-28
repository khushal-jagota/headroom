# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    import base64
    import os
    import json
    from pathlib import Path

    INPUT = "[data-conversation-input]"
    ATTACHMENTS = "[data-chat-image-name], [data-chat-file-name]"
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    )

    def _unsent_words_outlive_a_rebuild(page, url):
        """Words typed and not sent are the one thing retrying cannot recover.

        Anything above the composer can rebuild it - a brief disconnection did, on the
        owner's machine - so the draft is kept outside the component that draws it.
        """
        page.goto(url + "?draft=outlives-a-rebuild", wait_until="domcontentloaded")
        page.locator(INPUT).fill("")
        page.locator(INPUT).type("half a thought, unsent")
        page.wait_for_timeout(50)
        page.goto(url + "?draft=outlives-a-rebuild", wait_until="domcontentloaded")
        assert page.locator(INPUT).input_value() == "half a thought, unsent"
        page.locator(INPUT).fill("")
        page.wait_for_timeout(50)
        page.goto(url + "?draft=outlives-a-rebuild", wait_until="domcontentloaded")
        assert page.locator(INPUT).input_value() == ""

    def settled(page):
        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )

    def geometry(page):
        result = page.locator(".chat-foot").evaluate("""foot => {
          const bounds = foot.getBoundingClientRect();
          const visible = e => e.getBoundingClientRect().width > 0;
          const buttons = [...foot.querySelectorAll('button')].filter(visible);
          return {overflow:foot.scrollWidth > foot.clientWidth + 1, buttons:buttons.map(button => {
            const b = button.getBoundingClientRect(), s = getComputedStyle(button);
            const glyph = button.querySelector('svg,.chat-send-fill');
            const g = glyph?.getBoundingClientRect();
            return {label:button.getAttribute('aria-label'),square:button.matches('.chat-image,.chat-send'),send:button.matches('.chat-send'),
              pill:button.matches('.listbox-picker-trigger'),w:b.width,h:b.height,cy:b.y+b.height/2,
              outside:b.left < bounds.left-1 || b.right > bounds.right+1,
              background:s.backgroundColor,border:s.borderTopWidth,stop:button.hasAttribute('data-conversation-stop'),glyph:g ? [g.width,g.height] : null,
              offset:g ? [g.x+g.width/2-b.x-b.width/2,g.y+g.height/2-b.y-b.height/2] : null};
          }),chevrons:[...foot.querySelectorAll('.listbox-picker-chevron,.listbox-picker-chevron-drawn')].filter(visible).length};
        }""")
        assert not result["overflow"], result
        assert result["chevrons"] == 0, result
        for b in result["buttons"]:
            assert not b["outside"], b
            if b["square"]:
                size = 28 if b["send"] else 32
                assert abs(b["w"] - size) <= 0.1 and abs(b["h"] - size) <= 0.1, b
                assert b["offset"] and max(abs(n) for n in b["offset"]) <= 0.1, b
                assert b["glyph"] == ([11, 11] if b["stop"] else [14, 14] if b["send"] else [22, 22]), b
            if b["pill"]:
                assert abs(b["h"] - 32) <= 0.1, b
        centers = [b["cy"] for b in result["buttons"]]
        assert max(centers) - min(centers) <= 1, result
        stop = page.locator("[data-conversation-stop]")
        send = page.locator("[data-conversation-send].on")
        if send.count():
            page.wait_for_function("""() => {const e=document.querySelector('[data-conversation-send].on');if(!e)return false;const probe=document.createElement('span');probe.style.color='var(--accent-bright)';e.append(probe);const accent=getComputedStyle(probe).color;probe.remove();return getComputedStyle(e).backgroundColor===accent;}""")
            assert send.evaluate(
                "e => {const probe=document.createElement('span');probe.style.color='var(--accent-bright)';e.append(probe);const accent=getComputedStyle(probe).color;probe.remove();return getComputedStyle(e).backgroundColor === accent}"
            )
        if stop.count():
            style = stop.evaluate(
                "e => ({bg:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderTopWidth})"
            )
            assert style == {"bg": "rgba(0, 0, 0, 0)", "border": "1px"}, style
        return result

    def names(page):
        return page.locator(ATTACHMENTS).evaluate_all(
            "els => els.map(e => e.dataset.chatImageName ?? e.dataset.chatFileName)"
        )

    def attach(page):
        page.locator("[data-conversation-file-input]").set_input_files(
            [
                {"name": "first.png", "mimeType": "image/png", "buffer": PNG},
                {"name": "notes.md", "mimeType": "text/markdown", "buffer": b"notes"},
                {"name": "second.png", "mimeType": "image/png", "buffer": PNG},
                {"name": "run.txt", "mimeType": "text/plain", "buffer": b"log"},
            ]
        )
        page.wait_for_function(
            "() => document.querySelectorAll('[data-chat-image-name], [data-chat-file-name]').length === 4"
        )
        assert names(page) == ["first.png", "notes.md", "second.png", "run.txt"]

    def height(page):
        return page.locator(INPUT).evaluate(
            "e => ({h:e.getBoundingClientRect().height,line:parseFloat(getComputedStyle(e).lineHeight),scroll:e.scrollHeight,overflow:getComputedStyle(e).overflowY,padding:parseFloat(getComputedStyle(e).paddingTop)+parseFloat(getComputedStyle(e).paddingBottom)})"
        )

    page = browser.new_page(viewport={"width": 980, "height": 800})
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    # Local browser capability is enough to render the microphone; never record.
    page.add_init_script(
        "Object.defineProperty(navigator, 'mediaDevices', {value:{getUserMedia:async()=>{window.__voiceAttempts=(window.__voiceAttempts??0)+1;throw Error('permission denied')}}});"
    )
    _unsent_words_outlive_a_rebuild(page, [__file__, *urls][1])
    for width in [980, 390]:
        page.set_viewport_size({"width": width, "height": 800})
        for state in [
            "empty",
            "typing",
            "long",
            "attachments",
            "held",
            "running",
            "ask",
            "refused",
        ]:
            page.goto([__file__, *urls][1], wait_until="domcontentloaded")
            page.evaluate("(state) => window.__setState(state)", state)
            if state in ["typing", "held", "running", "refused"]:
                page.locator(INPUT).fill(
                    "Run the migration against the copied database, then show the diff."
                )
            if state == "long":
                page.locator(INPUT).fill("\n".join("Line " + str(n) for n in range(30)))
            if state == "attachments":
                attach(page)
            settled(page)
            geometry(page)
            # The restored inline controls retain the settled footer bounds.
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                width,
                state,
            )
            if state == "empty":
                assert page.locator("[data-conversation-send]").count() == 0
                assert page.locator(INPUT).get_attribute("placeholder") == "Message the agent..."
                assert page.locator(".chat-foot > [data-conversation-model-picker]").is_visible()
                assert page.locator(".chat-submit > [data-voice-record]").is_visible()
                add = page.locator("[data-conversation-add]")
                assert add.get_attribute("aria-haspopup") == "menu"
                assert add.get_attribute("aria-label") == "Add a picture, a file, a photo, or a command"
                output = os.environ.get("PANELS_RESTORED_INPUT_ARTIFACT_DIR")
                if output:
                    Path(output).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(output) / f"restored-input-{width}.png"))
            if state == "attachments":
                boxes = page.locator(ATTACHMENTS).evaluate_all(
                    "els => els.map(e => e.getBoundingClientRect().top)"
                )
                assert max(boxes) - min(boxes) <= 1, boxes
                page.locator('[data-chat-file-name="notes.md"] button').click()
                assert names(page) == ["first.png", "second.png", "run.txt"]
            if state == "held":
                assert page.locator("[data-conversation-held-stack]").is_visible()
            if state == "ask":
                assert page.locator('[data-conversation-ask-action="approve"]').is_visible()
                assert page.locator("[data-conversation-add]").count() == 0
            if state == "refused":
                assert (
                    page.locator("[data-conversation-error]").inner_text()
                    == "Message refused: connection lost"
                )

    # Inline voice denial preserves the draft without the Add menu.
    page.goto(urls[0], wait_until="domcontentloaded")
    page.locator(INPUT).fill("Keep these words")
    voice = page.locator(".chat-submit > [data-voice-record]")
    assert voice.is_visible()
    voice.click()
    page.wait_for_function("window.__voiceAttempts === 1")
    assert page.locator(".chat-add-menu").count() == 0
    assert page.locator(INPUT).input_value() == "Keep these words"
    assert page.locator("[data-conversation-send]").is_enabled()

    # Image-only, file-only and mixed drafts occupy one identical strip height.
    strip_heights = []
    for selected in [["picture.png"], ["notes.md"], ["picture.png", "notes.md"]]:
        page.goto([__file__, *urls][1])
        page.locator("[data-conversation-file-input]").set_input_files(
            [
                {
                    "name": name,
                    "mimeType": "image/png" if name.endswith(".png") else "text/markdown",
                    "buffer": PNG if name.endswith(".png") else b"notes",
                }
                for name in selected
            ]
        )
        page.wait_for_function(
            "count => document.querySelectorAll('[data-chat-image-name], [data-chat-file-name]').length === count",
            arg=len(selected),
        )
        sizes = page.locator(ATTACHMENTS).evaluate_all(
            "els => els.map(e => e.getBoundingClientRect().height)"
        )
        assert sizes == [52] * len(selected), sizes
        strip_heights.append(
            page.locator("[data-conversation-attachments]").bounding_box()["height"]
        )
    assert max(strip_heights) - min(strip_heights) <= 1, strip_heights

    # Content height follows typing and deletion, caps on a complete line, and scrolls there.
    page.goto([__file__, *urls][1])
    small = height(page)
    page.locator(INPUT).fill("one\ntwo\nthree\nfour")
    settled(page)
    medium = height(page)
    assert medium["h"] > small["h"] + medium["line"] * 2, (small, medium)
    page.locator(INPUT).fill("\n".join("line " + str(n) for n in range(40)))
    settled(page)
    tall = height(page)
    cap = tall["line"] * 6
    assert abs(tall["h"] - tall["padding"] - cap) <= 1, (tall, cap)
    assert tall["overflow"] == "auto" and tall["scroll"] > tall["h"], tall
    page.locator(INPUT).evaluate("e => e.scrollTop = e.scrollHeight")
    assert page.locator(INPUT).evaluate("e => e.scrollTop") > 0
    page.locator("[data-conversation-pane]").evaluate("e => e.style.height = '360px'")
    settled(page)
    shorter = height(page)
    assert abs(shorter["h"] - tall["h"]) <= 0.1, (tall, shorter)
    page.locator(INPUT).fill("short")
    settled(page)
    assert height(page)["h"] <= small["h"] + 1
    assert height(page)["overflow"] == "hidden"

    # Width changes remeasure wrapping without a keypress.
    page.locator("[data-conversation-pane]").evaluate("e => e.style.height = '720px'")
    page.set_viewport_size({"width": 980, "height": 800})
    page.locator(INPUT).fill("Words that wrap across the available composer width. " * 7)
    settled(page)
    wide = height(page)
    page.set_viewport_size({"width": 390, "height": 800})
    settled(page)
    assert height(page)["h"] > wide["h"], (wide, height(page))

    # A trailing newline occupies an empty visual line. Native same-line edits
    # retain selection/focus while the mirror measures soft wrapping and cap changes.
    page.goto(urls[0])
    page.locator(INPUT).fill("one\ntwo\nthree\nfour\nfive\nsix")
    settled(page)
    six_lines = height(page)
    page.locator(INPUT).evaluate(
        "input => {input.focus();input.setSelectionRange(input.value.length,input.value.length)}"
    )
    page.keyboard.type("x")
    page.keyboard.press("Backspace")
    settled(page)
    assert abs(height(page)["h"] - six_lines["h"]) <= 0.1
    assert page.locator(INPUT).evaluate(
        "input => document.activeElement === input && input.selectionStart === input.value.length && input.selectionEnd === input.value.length"
    )
    page.keyboard.press("Shift+Enter")
    settled(page)
    trailing = height(page)
    assert abs(trailing["h"] - six_lines["h"]) <= 0.1, (six_lines, trailing)
    assert six_lines["overflow"] == "hidden", six_lines
    assert trailing["overflow"] == "auto" and trailing["scroll"] > trailing["h"], trailing
    page.keyboard.press("Backspace")
    settled(page)
    assert abs(height(page)["h"] - six_lines["h"]) <= 0.1
    assert height(page)["overflow"] == "hidden"

    # Native text paste grows the actual textarea; mixed drag/drop and image paste
    # travel through browser events into the same ordered attachment strip.
    page.goto([__file__, *urls][1])
    page.locator(INPUT).click()
    page.evaluate("navigator.clipboard.writeText('pasted one\\npasted two\\npasted three')")
    page.keyboard.press("Control+V")
    page.wait_for_function(
        "document.querySelector('[data-conversation-input]').value.includes('pasted three')"
    )
    settled(page)
    assert height(page)["h"] > small["h"] + small["line"], height(page)
    page.locator("[data-conversation-box]").evaluate("""box => {
      const data = new DataTransfer();
      data.items.add(new File(['dropped'], 'dropped.txt', {type:'text/plain'}));
      const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='),c=>c.charCodeAt(0));
      data.items.add(new File([bytes], 'dropped.png', {type:'image/png'}));
      box.dispatchEvent(new DragEvent('drop', {bubbles:true,cancelable:true,dataTransfer:data}));
    }""")
    page.wait_for_function(
        "document.querySelectorAll('[data-chat-image-name], [data-chat-file-name]').length === 2"
    )
    assert names(page) == ["dropped.txt", "dropped.png"]
    page.locator(INPUT).evaluate("""input => {
      const data = new DataTransfer();
      const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='),c=>c.charCodeAt(0));
      data.items.add(new File([bytes], 'pasted.png', {type:'image/png'}));
      input.dispatchEvent(new ClipboardEvent('paste', {bubbles:true,cancelable:true,clipboardData:data}));
    }""")
    page.wait_for_function(
        "document.querySelectorAll('[data-chat-image-name], [data-chat-file-name]').length === 3"
    )
    assert names(page) == ["dropped.txt", "dropped.png", "pasted.png"]
    geometry(page)

    # Refusal restores a single ordered draft; later composition wins over old sends.
    page.goto([__file__, *urls][1])
    attach(page)
    page.locator(INPUT).fill("original draft")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__attempts.length === 1")
    assert page.locator(INPUT).input_value() == ""
    assert names(page) == []
    # The pending-send affordance remains after the successful local draft clear.
    pending_send = page.locator("[data-conversation-send]")
    assert pending_send.is_visible() and pending_send.is_disabled()
    assert pending_send.get_attribute("aria-busy") == "true"
    page.evaluate("window.__resolveSend(false)")
    page.wait_for_function(
        "document.querySelector('[data-conversation-input]').value === 'original draft'"
    )
    assert names(page) == ["first.png", "notes.md", "second.png", "run.txt"]
    page.locator('[data-chat-image-name="first.png"] button').click()
    assert names(page) == ["notes.md", "second.png", "run.txt"]
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__attempts.length === 2")
    page.locator(INPUT).fill("newer draft")
    page.locator("[data-conversation-file-input]").set_input_files(
        {"name": "new.txt", "mimeType": "text/plain", "buffer": b"new"}
    )
    page.locator('[data-chat-file-name="new.txt"]').wait_for()
    page.evaluate("window.__resolveSend(false)")
    settled(page)
    assert page.locator(INPUT).input_value() == "newer draft"
    assert names(page) == ["new.txt"]
    # Canonical picker facts reuse this component host. This proves presentation,
    # callback retry and focus only. No lifecycle endpoint or Ticket mutation runs.
    page.route("**/api/conversation/backends", lambda route: route.fulfill(json={"backends": []}))
    for width in [1280, 390]:
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(urls[0] + "?picker", wait_until="domcontentloaded")
        destination = page.locator("[data-employee-destination-picker] button")
        assert destination.is_disabled()
        assert destination.get_attribute("aria-label") == "Runs on Mac"
        picker = page.locator(".picker-proof > [data-conversation-model-picker]")
        trigger = picker.locator("[data-conversation-picker-trigger]")
        assert "opus → claude-opus-5" in trigger.inner_text()
        assert picker.locator(".listbox-picker-chevron,.listbox-picker-chevron-drawn").count() == 0
        trigger.press("ArrowDown")
        options = picker.get_by_role("option")
        assert options.count() == 14
        assert "current selection" in options.first.get_attribute("aria-label")
        assert "Effort: unknown" in options.first.get_attribute("aria-label")
        assert "Effort: none" in options.nth(1).get_attribute("aria-label")
        panel = picker.locator("[data-conversation-picker-panel]")
        bounds = panel.bounding_box()
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 720
        # The empty support fact does not erase explicit intent on selection.
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__pickerChoices") == [{"model": "claude-opus-5", "effort": "high"}]
        assert trigger.evaluate("e => e === document.activeElement")
        trigger.press("ArrowDown")
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("End")
        assert picker.locator("[data-model-picker-check]").evaluate("e => e === document.activeElement")
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Space")
        why = picker.locator("[data-model-picker-why]")
        assert why.get_attribute("aria-expanded") == "true"
        assert "Reason: catalog_unavailable" in picker.locator(".model-picker-detail").inner_text()
        assert panel.is_visible()
        output = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(output) / f"canonical-picker-{width}.png"))
        why.focus()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_function("window.__pickerReads.length === 1")
        assert picker.get_by_role("listbox").evaluate("e => e === document.activeElement")
        picker.locator("[data-model-picker-check]").click()
        page.wait_for_function("window.__pickerReads.length === 2")
        assert page.evaluate("JSON.stringify(window.__pickerReads[0]) === JSON.stringify(window.__pickerReads[1])")
        # Tokens and inherited state text clear the 4.5:1 text contrast floor.
        contrast = picker.locator(".model-picker-foot").evaluate(r"""e => {
          const rgb=s=>s.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});
          const lum=s=>{const c=rgb(s);return .2126*c[0]+.7152*c[1]+.0722*c[2]};
          const s=getComputedStyle(e),a=lum(s.color),b=lum(s.backgroundColor);
          return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
        }""")
        assert contrast >= 4.5, contrast
        control_boxes = picker.locator("button").evaluate_all("els => els.map(e => ({label:e.getAttribute('aria-label') ?? e.textContent, width:e.getBoundingClientRect().width, height:e.getBoundingClientRect().height})).filter(b => b.width && b.height)")
        control_boxes.append({"label": "destination", **destination.bounding_box()})
        for box in control_boxes:
            assert box["width"] >= 24 and box["height"] >= 24, box
        if output:
            (Path(output) / f"canonical-picker-{width}.json").write_text(json.dumps({
                "scope": "component-only fixture callbacks; no real endpoint, durable Ticket write or connected acceptance",
                "viewport": width, "panel": bounds, "contrast": contrast, "targets": control_boxes,
                "retry_callback_arguments": page.evaluate("window.__pickerReads"),
                "choices": page.evaluate("window.__pickerChoices"),
            }, indent=2))
        for control in [why, picker.locator("[data-model-picker-check]")]:
            box = control.bounding_box()
            assert box["width"] >= 24 and box["height"] >= 24, box
        picker.get_by_role("listbox").focus()
        page.keyboard.press("Escape")
        assert trigger.evaluate("e => e === document.activeElement")
        # Null effort metadata preserves intent too.
        trigger.press("ArrowDown")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__pickerChoices.at(-1)") == {"model": "opus", "effort": "high"}
    page.goto(urls[0] + "?picker&missingRetry", wait_until="domcontentloaded")
    picker = page.locator(".picker-proof > [data-conversation-model-picker]")
    picker.locator("[data-conversation-picker-trigger]").click()
    assert picker.locator("[data-model-picker-check]").is_disabled()
    assert page.evaluate("window.__pickerReads.length") == 0
    assert not errors, errors
    pass  # Shared runner owns the browser lifecycle.
    print("conversation-composer-browser.test.mjs: all assertions passed")
