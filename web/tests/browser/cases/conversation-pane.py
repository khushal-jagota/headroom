# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def _catalogs(browser, url):
    from urllib.parse import parse_qs, urlsplit

    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1100, "height": 800})
    requests = []
    def history(route):
        args = parse_qs(urlsplit(route.request.url).query)
        offset = int(args.get("offset", [0])[0])
        selected = args.get("selected", [None])[0]
        prefix = args.get("prefix", [None])[0]
        active = int(selected[1:]) if selected else None
        if prefix:
            active = next((index for index in range(135) if f"History {index:03}".lower().startswith(prefix)), None)
        if active is not None:
            offset = active // 60 * 60
        requests.append(("history", offset))
        route.fulfill(json={"revision":"one", "offset":offset, "total":135, "active_index":active,
            "entries":[{"conversation_id":f"c{index:03}","created_at":index,"label":f"History {index:03}"} for index in range(offset,min(offset+60,135))]})
    def artifacts(route):
        args = parse_qs(urlsplit(route.request.url).query)
        offset = int(args.get("offset", [0])[0])
        limit = int(args.get("limit", [60])[0])
        directory = args.get("directory", [""])[0]
        count = 3 if directory else 135
        rows = []
        for index in range(offset, min(count, offset+limit)):
            folder = not directory and index == 0
            path = "folder" if folder else f"{directory+'/' if directory else ''}file{index:03}.md"
            rows.append({"key":path,"path":path,"label":path,"kind":"3" if folder else "md",
                "href":None if folder else f"/files/tickets/t_catalog/{path}","directory":"folder" if folder else None,"child_count":3 if folder else 0})
        requests.append(("artifacts", offset))
        route.fulfill(json={"revision":"one","directory":directory,"offset":offset,"total":count,"entries":rows})
    try:
        page.route("**/api/conversation-history/**", history)
        page.route("**/api/artifact-catalog/**", artifacts)
        page.goto(url+"?catalogs=1", wait_until="domcontentloaded")
        trigger = page.get_by_role("button",name="Prior conversation",exact=True)
        trigger.click()
        box = page.get_by_role("listbox")
        expect(box.get_by_role("option")).to_have_count(61)
        box.press("End")
        expect(page.locator("[data-listbox-picker-active]")).to_have_attribute("data-listbox-picker-value","c134")
        expect(box.get_by_role("option")).to_have_count(15)
        box.press("Enter")
        expect(page.locator("[data-history-selection]")).to_have_text("c134")
        trigger.click()
        box.press("Home")
        expect(page.locator("[data-listbox-picker-active]")).to_have_attribute("data-listbox-picker-value","__current__")
        for key in ("h", "i", "s", "t", "o", "r", "y", " ", "1", "2", "3"):
            box.press(key)
        expect(page.locator("[data-listbox-picker-active]")).to_have_attribute("data-listbox-picker-value","c123")
        box.press("Escape")
        expect(trigger).to_be_focused()
        page.locator("[data-artifacts-band] > summary").click()
        window = page.locator(".artifact-catalog-window").first
        expect(window.locator(":scope > [data-artifact-folder]")).to_have_count(1)
        window.locator("[data-artifact-folder]").click()
        expect(page.locator('.artifact-catalog-children [data-artifact-chip]')).to_have_count(3)
        window.locator("[data-artifact-folder]").click()
        window.evaluate("element => { element.scrollTop=130*36; element.dispatchEvent(new Event('scroll')); }")
        expect(window.locator('[data-artifact-chip="file134.md"]')).to_have_count(1)
        assert window.locator('[data-artifact-chip]').count() <= 60
        assert ("history",120) in requests and ("artifacts",120) in requests
        print("PASS catalog browser boundary: bounded history End/Home/typeahead and artifact folder/scroll windows", flush=True)
    finally:
        page.close()


def _preview_lifecycle(browser, url):
    import base64
    import time

    from playwright.sync_api import expect

    started = time.monotonic()
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    reads = []
    page.on("pageerror",lambda error: print("PREVIEW ERROR",str(error),flush=True))
    page.on("console",lambda message: print("PREVIEW CONSOLE",message.text,flush=True) if message.type=="error" else None)
    def preview(route):
        reads.append(route.request.url)
        route.fulfill(content_type="text/html", body="<html><body>Automatic visible preview</body></html>")
    try:
        page.route("**/files/tickets/t_preview/*.html", preview)
        page.route("**/files/tickets/t_preview/*.png",lambda route: route.fulfill(content_type="image/png",body=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=")))
        page.goto(url+"?previews=1", wait_until="domcontentloaded")
        page.wait_for_function("document.querySelectorAll('[data-file-preview-html]').length > 0")
        assert 0 < len(reads) < 8, reads
        assert page.locator("iframe").count() < 8
        page.wait_for_function("document.querySelectorAll('[data-preview-fixture] img').length > 0")
        assert page.locator("[data-preview-fixture] img").count() < 8
        page.locator("[data-preview-fixture]").evaluate("element=>{element.scrollTop=element.scrollHeight}")
        page.wait_for_function("Array.from(document.querySelectorAll('[data-file-preview-html]')).some(frame=>frame.title.includes('preview23'))")
        assert page.locator("iframe").count() < 8
        page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'hidden'});document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator("iframe")).to_have_count(0)
        expect(page.locator("[data-preview-fixture] img")).to_have_count(0)
        page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'visible'});document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function("document.querySelectorAll('[data-file-preview-html]').length > 0")
        page.evaluate("window.__previewMode('large')")
        page.locator("[data-preview-fixture]").evaluate("element=>{element.scrollTop=0}")
        page.get_by_role("textbox",name="Parser input").fill("typing while parsing")
        try:
            page.get_by_text("Paragraph 0",exact=True).wait_for(timeout=5000)
        except Exception:
            print("PREVIEW STATE",page.locator("[data-preview-fixture]").inner_html()[:2000],flush=True)
            raise
        assert page.locator("[data-markdown-window] h2").count() < 1600
        assert page.locator("[data-markdown-window] h2").count() > 0
        copied = page.locator(".markdown-block").first.evaluate("""element=>{
          const range=document.createRange();range.selectNodeContents(element);
          const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
          let text='';const event=new Event('copy',{bubbles:true,cancelable:true});
          Object.defineProperty(event,'clipboardData',{value:{setData:(kind,value)=>{if(kind==='text/plain')text=value}}});
          element.dispatchEvent(event);selection.removeAllRanges();return text;
        }""")
        assert "Paragraph 0" in copied and "Paragraph 1599" in copied
        page.locator("[data-preview-fixture]").evaluate("element=>{element.scrollTop=element.scrollHeight}")
        page.get_by_text("Paragraph 1599",exact=True).wait_for()
        assert page.locator("[data-markdown-window] h2").count() < 1600
        page.evaluate("window.__previewMode('copy')")
        page.locator("[data-preview-fixture]").evaluate("element=>{element.scrollTop=0}")
        page.get_by_text("FIRST Paragraph 0",exact=True).wait_for()
        page.get_by_text("SECOND Paragraph 0",exact=True).wait_for()
        assert page.locator("[data-copy-first] [data-markdown-window]:empty").count() > 0
        assert page.locator("[data-copy-second] [data-markdown-window]:empty").count() > 0
        copied = page.locator("[data-copy-selection]").evaluate("""element=>{
          const copy=(partial)=>{
            const range=document.createRange();
            if(partial){
              const start=element.querySelector('[data-copy-before]').firstChild;
              const end=element.querySelector('[data-copy-after]').firstChild;
              range.setStart(start,start.textContent.indexOf('KEEP START'));
              range.setEnd(end,'KEEP END'.length);
            }else range.selectNodeContents(element);
            const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
            const writes=[];const event=new Event('copy',{bubbles:true,cancelable:true});
            Object.defineProperty(event,'clipboardData',{value:{setData:(kind,value)=>{if(kind==='text/plain')writes.push(value)}}});
            document.dispatchEvent(event);selection.removeAllRanges();return writes;
          };
          return {full:copy(false),partial:copy(true)};
        }""")
        assert len(copied["full"]) == 1 and len(copied["partial"]) == 1
        for value in [copied["full"][0],copied["partial"][0]]:
            markers=["FIRST Paragraph 0","FIRST Paragraph 1599","OUTSIDE BETWEEN messages","SECOND Paragraph 0","SECOND Paragraph 1599"]
            positions=[value.index(marker) for marker in markers]
            assert positions == sorted(positions)
            assert all(value.count(marker)==1 for marker in ["FIRST Paragraph 1599","SECOND Paragraph 1599","OUTSIDE BETWEEN messages"])
        assert "OUTSIDE BEFORE prefix KEEP START" in copied["full"][0]
        assert "KEEP END suffix OUTSIDE AFTER" in copied["full"][0]
        assert copied["partial"][0].startswith("KEEP START")
        assert copied["partial"][0].rstrip().endswith("KEEP END")
        assert "OUTSIDE BEFORE" not in copied["partial"][0] and "OUTSIDE AFTER" not in copied["partial"][0]
        page.evaluate("window.__previewMode('none')")
        expect(page.locator("[data-markdown-window]")).to_have_count(0)
        page.evaluate("window.__containerLongTasks=[];window.__containerObserver=new PerformanceObserver(list=>window.__containerLongTasks.push(...list.getEntries().map(entry=>entry.duration)));window.__containerObserver.observe({type:'longtask',buffered:false})")
        input_box=page.get_by_role("textbox",name="Parser input")
        input_box.focus()
        latencies=[]
        for _ in range(20):
            page.evaluate("setTimeout(()=>window.__containerRevision(),0)")
            before=time.perf_counter()
            page.keyboard.press("x")
            page.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
            latencies.append((time.perf_counter()-before)*1000)
        expect(page.locator("[data-preview-fixture] table tr")).to_have_count(50)
        expect(page.locator("[data-preview-fixture] ol li")).to_have_count(54)
        long_tasks=page.evaluate("window.__containerObserver.disconnect();window.__containerLongTasks")
        latencies.sort()
        print("MARKDOWN_CONTAINER_METRICS",{"table_rows":50,"ordered_items":54,"samples":20,"queued_input_roundtrip_p50_ms":round(latencies[9],2),"p95_ms":round(latencies[18],2),"max_ms":round(latencies[-1],2),"long_tasks":long_tasks},flush=True)
        print(f"PASS preview lifecycle: visible automatic frames/images, offscreen/hidden disposal, worker Markdown windows and full-text copy ({time.monotonic()-started:.2f}s)",flush=True)
    finally:
        page.close()


def _run(browser, urls):
    _catalogs(browser, urls[0])
    _preview_lifecycle(browser, urls[0])

    import os
    import traceback

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

    def settled(page):
        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )

    def newest_reading(page):
        return page.locator(THREAD).evaluate("""thread => {
          const row = [...thread.querySelectorAll('[data-conversation-row]')].at(-1);
          const bounds = thread.getBoundingClientRect();
          return {bottom: row.getBoundingClientRect().bottom - bounds.top,
            height: thread.clientHeight, scrollTop: thread.scrollTop,
            latest: !!document.querySelector('button[aria-label="Jump to latest message"]')};
        }""")

    def assert_newest(page):
        settled(page)
        reading = newest_reading(page)
        assert -3 <= reading["bottom"] <= reading["height"] + 3, reading
        assert not reading["latest"], reading
        return reading

    def visible_paragraph(page):
        return page.locator(THREAD).evaluate("""thread => {
          const top = thread.getBoundingClientRect().top;
          const paragraph = [...thread.querySelectorAll('[data-conversation-row] p')]
            .find(node => node.getBoundingClientRect().bottom > top + 1 && node.getBoundingClientRect().top < top + thread.clientHeight);
          if (!paragraph) throw Error('fixture needs a visible paragraph');
          window.__heldParagraph = paragraph;
          return {text: paragraph.textContent, top: paragraph.getBoundingClientRect().top - top};
        }""")

    def assert_paragraph(page, held):
        settled(page)
        actual = page.evaluate("""() => ({connected: window.__heldParagraph.isConnected,
          text: window.__heldParagraph.textContent,
          top: window.__heldParagraph.getBoundingClientRect().top - document.querySelector('[data-conversation-thread]').getBoundingClientRect().top})""")
        assert actual["connected"] and actual["text"] == held["text"], (held, actual)
        assert abs(actual["top"] - held["top"]) <= 3, (held, actual)
        assert page.locator('button[aria-label="Jump to latest message"]').is_visible()
        return actual

    def prepare_scroll_case(page, url, kind, mode):
        page.goto(url, wait_until="domcontentloaded")
        page.evaluate("({kind,mode}) => window.__setupScrollRegression(kind,mode)", {"kind":kind,"mode":mode})
        state(page, "peeked")
        page.locator(INPUT).fill("\n".join("Draft line " + str(n) for n in range(6)))
        settled(page)
        if mode == "scrollback":
            thread = page.locator(THREAD)
            if kind != "plain":
                # Read INSIDE a message taller than the viewport. Anchoring its outer
                # article would leave this paragraph displaced when its preview grows.
                distance = thread.evaluate("""thread => {
                  const paragraph = [...thread.querySelectorAll('p')].find(node => node.textContent.startsWith('Tall paragraph 14:'));
                  return paragraph.getBoundingClientRect().top - thread.getBoundingClientRect().top + 9;
                }""")
                thread.hover()
                page.mouse.wheel(0, distance)
            else:
                thread.hover()
                page.mouse.wheel(0, -550)
            page.evaluate("window.__lastRegressionWheel = performance.now()")
            page.wait_for_timeout(80)
            settled(page)
            page.locator('button[aria-label="Jump to latest message"]').wait_for()
            return visible_paragraph(page)
        assert_newest(page)
        return None

    def watch_draft(page):
        page.evaluate("""() => {
          const thread = document.querySelector('[data-conversation-thread]');
          const input = document.querySelector('[data-conversation-input]');
          const newest = [...thread.querySelectorAll('[data-conversation-row]')].at(-1);
          window.__draftSamples = [];
          let active = true;
          const sample = () => window.__draftSamples.push({scroll:thread.scrollTop,
            newest:newest.getBoundingClientRect().bottom - thread.getBoundingClientRect().top,
            height:input.getBoundingClientRect().height});
          const observer = new ResizeObserver(sample);
          observer.observe(thread);
          thread.addEventListener('scroll', sample);
          const frame = () => {if (active) {sample(); requestAnimationFrame(frame);}};
          frame();
          window.__stopDraftWatch = () => {active=false;sample();observer.disconnect();thread.removeEventListener('scroll',sample);return window.__draftSamples;};
        }""")

    def same_line_edits(page):
        box = page.locator(INPUT)
        # Focus directly, so the browser does not scroll the transcript to a locator.
        box.evaluate(
            "input => {input.focus({preventScroll:true});input.setSelectionRange(input.value.length,input.value.length)}"
        )
        for _ in range(3):
            page.keyboard.type("x")
            settled(page)
            page.keyboard.press("Backspace")
            settled(page)

    def draft_measurement_case(page, url):
        for mode in ["following", "scrollback"]:
            held = prepare_scroll_case(page, url, "plain", mode)
            before = page.locator(INPUT).bounding_box()["height"]
            watch_draft(page)
            same_line_edits(page)
            samples = page.evaluate("window.__stopDraftWatch()")
            after = page.locator(INPUT).bounding_box()["height"]
            assert abs(after - before) <= 0.1, (before, after)
            assert (
                max(item["height"] for item in samples) - min(item["height"] for item in samples)
                <= 0.1
            ), samples
            assert (
                max(item["scroll"] for item in samples) - min(item["scroll"] for item in samples)
                <= 3
            ), samples
            assert (
                max(item["newest"] for item in samples) - min(item["newest"] for item in samples)
                <= 3
            ), samples
            assert page.locator(INPUT).evaluate(
                "input => document.activeElement === input && input.selectionStart === input.value.length && input.selectionEnd === input.value.length"
            )
            if held:
                assert_paragraph(page, held)
            else:
                assert_newest(page)

    def prose_delta_case(page, url):
        for mode in ["following", "scrollback"]:
            held = prepare_scroll_case(page, url, "plain", mode)
            for index in range(3):
                same_line_edits(page)
                page.evaluate("(index) => window.__proseDelta(index)", index)
                settled(page)
                if held:
                    assert_paragraph(page, held)
                else:
                    assert_newest(page)

    def missing_anchors_case(page, url):
        prepare_scroll_case(page, url, "plain", "scrollback")
        before = newest_reading(page)["scrollTop"]
        page.evaluate("window.__replaceAllScrollRows()")
        settled(page)
        after = newest_reading(page)
        assert abs(after["scrollTop"] - before) <= 3, (before, after)
        assert after["latest"], after
        held = visible_paragraph(page)
        page.evaluate("window.__proseDelta(0)")
        assert_paragraph(page, held)
        # All original candidates disappear again, and the saved position now exceeds
        # the shortened transcript's range. Clamp there, then retain scrollback intent.
        before = newest_reading(page)["scrollTop"]
        page.evaluate("window.__replaceAllScrollRows(20)")
        settled(page)
        limit = page.locator(THREAD).evaluate("thread => thread.scrollHeight-thread.clientHeight")
        actual = newest_reading(page)["scrollTop"]
        assert before > limit and abs(actual - limit) <= 3, (before, limit, actual)
        held = visible_paragraph(page)
        page.evaluate(
            "window.__appendConversationLine();window.__appendConversationLine();window.__appendConversationLine()"
        )
        assert_paragraph(page, held)

    def layer_send_case(page, url):
        held = prepare_scroll_case(page, url, "plain", "scrollback")
        page.evaluate("window.__sendScrollRegression(true)")
        state(page, "opened")
        assert_paragraph(page, held)
        # A later viewport resize cannot replay the send that lost placement to layer.
        page.locator(INPUT).fill("one\ntwo\nthree\nfour\nfive")
        assert_paragraph(page, held)
        page.evaluate("window.__answerScrollRegression(1)")
        assert_paragraph(page, held)

    def answer_room_reading(page):
        return page.locator(THREAD).evaluate("""thread => {
          const prompt = thread.querySelector('[data-conversation-outgoing], [data-conversation-row="prompt"]');
          const room = thread.querySelector('[data-conversation-reserved-space]');
          return {room:room?.getBoundingClientRect().height ?? 0,
            promptTop:prompt.getBoundingClientRect().top-thread.getBoundingClientRect().top,
            viewport:thread.clientHeight,scroll:thread.scrollTop};
        }""")

    def answer_room_case(page, url):
        page.goto(url, wait_until="domcontentloaded")
        page.locator(INPUT).click()
        page.locator("[data-conversation-expand]").click()
        state(page, "opened")
        settled(page)
        page.evaluate("window.__sendScrollRegression(false)")
        settled(page)
        sent = answer_room_reading(page)
        assert 0 <= sent["promptTop"] <= 32, sent
        assert sent["room"] > 200, sent
        page.evaluate("window.__answerScrollRegression(1)")
        settled(page)
        partial = answer_room_reading(page)
        assert 0 < partial["room"] < sent["room"] - 30, (sent, partial)
        page.locator(INPUT).fill("\n".join("Draft line " + str(n) for n in range(6)))
        settled(page)
        smaller = answer_room_reading(page)
        assert smaller["viewport"] < partial["viewport"] - 50, (partial, smaller)
        assert smaller["room"] <= partial["room"] - 50, (partial, smaller)
        assert abs(smaller["promptTop"] - partial["promptTop"]) <= 3, (partial, smaller)
        page.evaluate("window.__answerScrollRegression(20)")
        settled(page)
        exhausted = answer_room_reading(page)
        assert exhausted["room"] == 0, exhausted
        page.locator(INPUT).fill("")
        settled(page)
        grown = answer_room_reading(page)
        assert grown["room"] == 0, (exhausted, grown)
        assert_newest(page)

    def install_controlled_image(page):
        # A capture listener gives the browser genuine input in the same dispatch as
        # raw content growth, before ResizeObserver can publish its pending notice.
        page.locator(THREAD).evaluate("""thread => {
          const image = document.createElement('img');
          image.alt = 'Controlled geometry race';
          image.style.cssText = 'display:block;width:1px;height:0px';
          thread.querySelector('[data-conversation-row]').prepend(image);
          window.__raceImage = image;
        }""")
        settled(page)

    def native_before_observer_case(page, url):
        prepare_scroll_case(page, url, "plain", "scrollback")
        install_controlled_image(page)
        before = newest_reading(page)["scrollTop"]
        page.locator(THREAD).evaluate("""thread => thread.addEventListener('wheel', () => {
          window.__raceImage.style.height = '180px';
          window.__nativeRaceBefore = thread.scrollTop;
        }, {capture:true, once:true})""")
        page.locator(THREAD).hover()
        page.mouse.wheel(0, 120)
        page.wait_for_timeout(100)
        settled(page)
        after = newest_reading(page)
        assert abs(page.evaluate("window.__nativeRaceBefore") - before) <= 3
        assert abs(after["scrollTop"] - before - 120) <= 3, (before, after)
        held = visible_paragraph(page)
        page.evaluate("window.__raceImage.style.height = '220px'")
        assert_paragraph(page, held)

    def short_message_growth_case(page, url):
        prepare_scroll_case(page, url, "plain", "scrollback")
        page.locator(THREAD).focus()
        page.keyboard.press("Home")
        page.wait_for_function(
            "document.querySelector('[data-conversation-thread]').scrollTop <= 1"
        )
        settled(page)
        install_controlled_image(page)
        row_height = page.locator("[data-conversation-row]").first.bounding_box()["height"]
        assert row_height < page.locator(THREAD).evaluate("thread => thread.clientHeight"), (
            row_height
        )
        held = visible_paragraph(page)
        page.evaluate("window.__raceImage.style.height = '180px'")
        assert_paragraph(page, held)

    def descendant_keyboard_case(page, url):
        for key in ["PageDown", "Home", "End", "Control+Home", "Control+End"]:
            prepare_scroll_case(page, url, "plain", "scrollback")
            install_controlled_image(page)
            page.evaluate("""() => {
              const link = document.createElement('a');
              link.href = '#keyboard-proof';link.textContent = ' Read';
              window.__heldParagraph.append(link);
              link.focus({preventScroll:true});
            }""")
            settled(page)
            before = newest_reading(page)["scrollTop"]
            page.keyboard.press(key)
            # Chromium animates native key scrolling. Wait for its actual endpoint
            # before the independent geometry change, so the held paragraph is settled.
            if key.endswith("Home"):
                page.wait_for_function(
                    "document.querySelector('[data-conversation-thread]').scrollTop <= 1"
                )
            elif key.endswith("End"):
                page.wait_for_function("""() => {
                  const thread=document.querySelector('[data-conversation-thread]');
                  return thread.scrollHeight-thread.scrollTop-thread.clientHeight <= 1;
                }""")
            else:
                page.wait_for_timeout(250)
            settled(page)
            after = newest_reading(page)
            if key == "PageDown":
                assert after["scrollTop"] > before + 50, (key, before, after)
            if key.endswith("Home"):
                assert after["scrollTop"] <= 1, (key, before, after)
            if key.endswith("End"):
                assert_newest(page)
            held = visible_paragraph(page)
            if key.endswith("Home"):
                # Growth below the reader must retain the native Home baseline.
                page.evaluate("window.__proseDelta(0)")
            else:
                page.evaluate("window.__raceImage.style.height = '180px'")
            if key.endswith("End"):
                assert_newest(page)
            else:
                assert_paragraph(page, held)
        # Plain buttons still allow native arrow scrolling, while Space activates.
        prepare_scroll_case(page, url, "plain", "scrollback")
        install_controlled_image(page)
        page.evaluate("""() => {
          const button=document.createElement('button');button.textContent='Read';
          button.addEventListener('click',()=>window.__buttonClicks=(window.__buttonClicks??0)+1);
          window.__heldParagraph.append(button);button.focus({preventScroll:true});
        }""")
        settled(page)
        for key, amount in [("ArrowDown", 180), ("ArrowUp", 220)]:
            before = newest_reading(page)["scrollTop"]
            page.keyboard.press(key)
            page.wait_for_timeout(250)
            settled(page)
            after = newest_reading(page)["scrollTop"]
            assert after > before + 10 if key == "ArrowDown" else after < before - 10, (
                key,
                before,
                after,
            )
            held = visible_paragraph(page)
            page.evaluate('(amount) => window.__raceImage.style.height = amount+"px"', amount)
            assert_paragraph(page, held)
        before = newest_reading(page)["scrollTop"]
        page.keyboard.press("Space")
        settled(page)
        assert page.evaluate("window.__buttonClicks") == 1
        assert abs(newest_reading(page)["scrollTop"] - before) <= 3
        # An editable nested scroller owns its navigation and scroll position.
        prepare_scroll_case(page, url, "plain", "scrollback")
        page.evaluate("""() => {
          const box = document.createElement('textarea');
          const bounds=document.querySelector('[data-conversation-thread]').getBoundingClientRect();
          box.rows=1;box.style.cssText='position:fixed;left:'+bounds.left+'px;top:'+(bounds.top+20)+'px;width:120px;height:24px';
          box.value=('nested text\\n').repeat(20);
          window.__heldParagraph.append(box);box.focus({preventScroll:true});
          box.setSelectionRange(0,0);window.__nestedEditor=box;
        }""")
        settled(page)
        before = newest_reading(page)["scrollTop"]
        page.keyboard.press("Control+End")
        settled(page)
        assert page.evaluate(
            "window.__nestedEditor.selectionStart === window.__nestedEditor.value.length && window.__nestedEditor.scrollTop > 0"
        ), page.evaluate(
            "({start:window.__nestedEditor.selectionStart,length:window.__nestedEditor.value.length,scroll:window.__nestedEditor.scrollTop})"
        )
        assert abs(newest_reading(page)["scrollTop"] - before) <= 3, (before, newest_reading(page))

    def delayed_case(page, url, kind, mode):
        pending = []
        pattern = (
            "**/delayed.png"
            if kind == "image"
            else "**/files/tickets/t_scrollregression/delayed.md"
        )
        page.route(pattern, lambda route: pending.append(route))
        held = prepare_scroll_case(page, url, kind, mode)
        page.wait_for_function("() => document.querySelector('[data-conversation-row]') !== null")
        # The preview now starts only near the viewport. Wait for its observer,
        # then keep the original delayed native-layout/anchor assertion.
        for _ in range(50):
            if pending:
                break
            page.wait_for_timeout(20)
        if len(pending)!=1:
            print("DELAYED_VISIBILITY",kind,mode,page.evaluate("""()=>({thread:document.querySelector('[data-conversation-thread]').getBoundingClientRect().toJSON(),slots:Array.from(document.querySelectorAll('[data-conversation-image-slot]')).map(node=>({rect:node.getBoundingClientRect().toJSON(),html:node.outerHTML}))})"""),flush=True)
        assert len(pending) == 1, (kind, len(pending))
        row_count = page.locator("[data-conversation-row]").count()
        page.locator('[data-conversation-row="agent_message"]').nth(27 if mode=="following" else 12).evaluate(
            "row => window.__delayedRow = row"
        )
        before = newest_reading(page)
        if mode == "scrollback":
            assert page.evaluate("performance.now() - window.__lastRegressionWheel") < 400
        # This is response release, with no row assignment, replaced message, or app event.
        if kind == "image":
            import struct
            import zlib

            def chunk(name, payload):
                return (
                    struct.pack("!I", len(payload))
                    + name
                    + payload
                    + struct.pack("!I", zlib.crc32(name + payload) & 0xFFFFFFFF)
                )

            image = (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack("!IIBBBBB", 500, 310, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x80\xa0\xb0" * 500) * 310))
                + chunk(b"IEND", b"")
            )
            pending[0].fulfill(status=200, content_type="image/png", body=image)
            page.wait_for_function(
                "() => {const image=document.querySelector('[data-conversation-piece=image]');return image?.complete && image.naturalHeight === 310}"
            )
        else:
            pending[0].fulfill(
                status=200,
                content_type="text/markdown",
                body="\n\n".join(
                    "Delayed preview paragraph "
                    + str(n)
                    + ": "
                    + ("Loaded Markdown changes real content geometry. " * 3)
                    for n in range(6)
                ),
            )
            page.get_by_text("Delayed preview paragraph 5:", exact=False).wait_for()
        settled(page)
        assert page.evaluate("window.__delayedRow.isConnected")
        assert page.locator("[data-conversation-row]").count() == row_count
        if held:
            assert held["text"].startswith("Tall paragraph"), held
            actual = assert_paragraph(page, held)
            assert newest_reading(page)["scrollTop"] > before["scrollTop"] + 100, (
                before,
                newest_reading(page),
            )
        else:
            actual = assert_newest(page)
            assert actual["scrollTop"] > before["scrollTop"] + 100, (before, actual)
        print(
            "DELAYED_GEOMETRY",
            kind,
            mode,
            {"before": before, "held": held, "after": actual},
            flush=True,
        )
        evidence = os.environ.get("SCROLL_REGRESSION_EVIDENCE_DIR")
        if evidence:
            os.makedirs(evidence, exist_ok=True)
            page.screenshot(path=os.path.join(evidence, "delayed-" + kind + "-" + mode + ".png"))

    # Named cases share this host and run independently. Selection permits every
    # regression to demonstrate its own failure against the unfixed source.
    cases = {
        "draft-measurement": draft_measurement_case,
        "prose-deltas": prose_delta_case,
        "missing-anchors": missing_anchors_case,
        "layer-send": layer_send_case,
        "answer-room": answer_room_case,
        "native-before-observer": native_before_observer_case,
        "descendant-keyboard": descendant_keyboard_case,
        "short-message-growth": short_message_growth_case,
        **{
            f"delayed-{kind}-{mode}": (
                lambda page, url, kind=kind, mode=mode: delayed_case(page, url, kind, mode)
            )
            for kind in ["image", "markdown"]
            for mode in ["following", "scrollback"]
        },
    }
    selected = os.environ.get("CONVERSATION_SCROLL_CASE")
    if selected and selected not in cases:
        raise AssertionError("Unknown CONVERSATION_SCROLL_CASE: " + selected)
    failures = []
    for name, run_case in cases.items():
        if selected and selected != name:
            continue
        regression_page = browser.new_page(viewport={"width": 1000, "height": 760})
        regression_page.set_default_timeout(5_000)
        try:
            run_case(regression_page, urls[0])
            print("SCROLL_CASE PASS " + name, flush=True)
        except Exception as error:
            failures.append(name + ": " + str(error))
            print("SCROLL_CASE FAIL " + name + ": " + str(error), flush=True)
            print(traceback.format_exc(), flush=True)
            evidence = os.environ.get("SCROLL_REGRESSION_EVIDENCE_DIR")
            if evidence:
                os.makedirs(evidence, exist_ok=True)
                regression_page.screenshot(path=os.path.join(evidence, name + ".png"))
        finally:
            regression_page.close()
    assert not failures, "\n".join(failures)
    if selected:
        print("conversation-pane-browser.test.mjs: all assertions passed")
        return

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
    assert page.locator(THREAD).count() == 0
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


def _bounded_reader(browser, url):
    page = browser.new_page(viewport={"width": 1000, "height": 900})
    page.goto(url + "?reader=1")
    page.wait_for_function("() => document.querySelectorAll('[data-conversation-view-key]').length > 0")
    page.wait_for_function("() => document.querySelector('[data-conversation-thread]')?.textContent.includes('Owner answer 2000.')")
    assert page.locator('[data-conversation-row="agent_message"]').count() <= 150
    assert page.evaluate("() => window.__viewCalls.every(url => !url.includes('/events?') && (!url.includes('/tail?') || url.includes('/view/tail?')))")
    before = page.evaluate("() => window.__viewCalls.filter(url => url.includes('/view/pages')).length")
    page.locator('[data-conversation-thread]').evaluate("node => {node.scrollTop=0;node.dispatchEvent(new Event('scroll'));}")
    page.wait_for_function("before => window.__viewCalls.filter(url => url.includes('/view/pages')).length > before", arg=before)
    assert page.locator('[data-conversation-view-key]').count() <= 300
    assert page.locator('[data-conversation-row="agent_message"]').count() <= 150
    page.evaluate("() => window.__readerState('rest')")
    page.wait_for_function("() => !document.querySelector('[data-conversation-thread]')")
    assert page.locator('[data-conversation-view-key]').count() == 0
    page.evaluate("() => window.__readerState('opened')")
    page.wait_for_selector('[data-conversation-thread]')
    page.wait_for_function("() => document.querySelectorAll('[data-conversation-row=agent_message]').length > 0")
    assert page.locator('[data-conversation-view-key]').count() <= 300
    # Persisted projected prompts must retain the same bubble layout as outgoing
    # prompts. Class-only assertions miss a virtualization wrapper breaking flex.
    for width in (1000, 420):
        page.set_viewport_size({"width": width, "height": 900})
        for key in ("e1998", "e1999"):
            bubble = page.locator(f'[data-conversation-view-key="{key}"] .chat-u')
            bubble.scroll_into_view_if_needed()
            geometry = bubble.evaluate("""element => {
                const row = element.getBoundingClientRect();
                const wrapper = element.closest('[data-conversation-view-key]').getBoundingClientRect();
                return {right: row.right, width: row.width, parentRight: wrapper.right,
                    parentWidth: wrapper.width, maxWidth: parseFloat(getComputedStyle(element).maxWidth)};
            }""")
            assert abs(geometry["right"] - geometry["parentRight"]) < 1, geometry
            assert geometry["width"] <= geometry["parentWidth"] * geometry["maxWidth"] / 100 + 1, geometry
            if key == "e1998":
                assert geometry["width"] < geometry["parentWidth"] * 0.65, geometry
        other = page.locator('[data-conversation-view-key="e1997"] .chat-system')
        other.scroll_into_view_if_needed()
        assert "Chief" in other.inner_text()
    page.set_viewport_size({"width": 1000, "height": 900})

    page.close()


def run(browser, urls):
    from tests.e2e.harness import browser_failure_evidence

    with browser_failure_evidence(browser, "conversation-pane-browser"):
        _bounded_reader(browser, urls[0])
        _run(browser, urls)
