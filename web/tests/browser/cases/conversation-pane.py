# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def _catalogs(browser, url):
    from urllib.parse import parse_qs, urlsplit

    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1100, "height": 800})
    requests = []
    pending_artifacts = []
    hold_artifacts = False
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
        response = {"revision":"one","directory":directory,"offset":offset,"total":count,"entries":rows}
        if hold_artifacts and not directory:
            pending_artifacts.append((route, response))
        else:
            route.fulfill(json=response)
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
        hold_artifacts = True
        page.locator("[data-artifacts-band] > summary").click()
        window = page.locator(".artifact-catalog-window").first
        expect(window.locator(':scope > .skeleton-region[aria-busy="true"]')).to_have_count(1)
        expect(window.locator(':scope > .skeleton-region .skeleton[data-entering]')).to_have_count(1)
        request, response = pending_artifacts.pop(0)
        request.fulfill(json=response)
        expect(window.locator(":scope > .skeleton-region > .skeleton-content > [data-artifact-page] > [data-artifact-folder]")).to_have_count(1)
        # Ready chips replace the cold placeholder without a second layout block.
        assert window.locator(":scope > .skeleton-region").count() == 1
        expect(window.locator(':scope > .skeleton-region .skeleton')).to_have_count(0)
        hold_artifacts = False
        window.locator("[data-artifact-folder]").click()
        expect(page.locator('.artifact-catalog-children [data-artifact-chip]')).to_have_count(3)
        window.locator("[data-artifact-folder]").click()
        # The requested page does not own the geometry until its response arrives.
        # Hold the browser read to prove that entries and both spacers stay together.
        before_spacer = window.locator(":scope > [data-artifact-before]")
        after_spacer = window.locator(":scope > [data-artifact-after]")
        before_height = before_spacer.evaluate("element => element.offsetHeight")
        after_height = after_spacer.evaluate("element => element.offsetHeight")
        hold_artifacts = True
        window.evaluate("element => { element.scrollTop=100*36; element.dispatchEvent(new Event('scroll')); }")
        expect(page.locator('.artifact-pending-page [aria-busy="true"]')).to_have_count(1)
        expect(window.locator('[data-artifact-chip="file059.md"]')).to_have_count(1)
        assert before_spacer.evaluate("element => element.offsetHeight") == before_height
        assert after_spacer.evaluate("element => element.offsetHeight") == after_height
        assert pending_artifacts
        pending_artifacts.pop(0)[0].fulfill(status=503, json={"error":{"code":"offline","message":"offline"}})
        expect(window.get_by_role("button", name="Retry", exact=True)).to_be_visible()
        expect(window.locator('[data-artifact-chip="file059.md"]')).to_have_count(1)
        assert before_spacer.evaluate("element => element.offsetHeight") == before_height
        assert after_spacer.evaluate("element => element.offsetHeight") == after_height
        window.get_by_role("button", name="Retry", exact=True).evaluate("element => element.click()")
        expect(page.locator('.artifact-pending-page [aria-busy="true"]')).to_have_count(1)
        expect(page.locator('.artifact-pending-page .skeleton[data-entering]')).to_have_count(1)
        request, response = pending_artifacts.pop(0)
        overlay_box = page.locator('.artifact-pending-page').first.bounding_box()
        assert overlay_box and overlay_box['height'] > 0
        request.fulfill(json=response)
        expect(window.locator('[data-artifact-chip="file134.md"]')).to_have_count(1)
        expect(page.locator('.artifact-pending-page .skeleton')).to_have_count(0)
        assert window.locator('[data-artifact-page]').evaluate("element => getComputedStyle(element).opacity === '1' && element.getAnimations({subtree:true}).length === 0")
        assert window.locator('[data-artifact-chip]').count() <= 60
        assert ("history",120) in requests and ("artifacts",80) in requests
        # Upward paging draws only above the retained page, never over its chips.
        prior_before = before_spacer.evaluate("element => element.offsetHeight")
        prior_after = after_spacer.evaluate("element => element.offsetHeight")
        window.evaluate("element => { element.scrollTop=element.querySelector('[data-artifact-before]').offsetHeight-150; element.dispatchEvent(new Event('scroll')); }")
        expect(page.locator('.artifact-pending-page [aria-busy="true"]')).to_have_count(1)
        expect(window.locator('[data-artifact-chip="file134.md"]')).to_have_count(1)
        assert before_spacer.evaluate("element => element.offsetHeight") == prior_before
        assert after_spacer.evaluate("element => element.offsetHeight") == prior_after
        overlay_box = page.locator('.artifact-pending-page').first.bounding_box()
        chip_box = window.locator('[data-artifact-chip="file080.md"]').bounding_box()
        assert overlay_box and chip_box and overlay_box["height"] > 0
        assert overlay_box["y"] + overlay_box["height"] <= chip_box["y"] + 1
        expect(page.locator('.artifact-pending-page .skeleton[data-entering]')).to_have_count(1)
        request, response = pending_artifacts.pop(0)
        assert response["offset"] == 40
        request.fulfill(json=response)
        expect(window.locator('[data-artifact-chip="file040.md"]')).to_have_count(1)
        expect(page.locator('.artifact-pending-page .skeleton')).to_have_count(0)
        assert window.locator('[data-artifact-page]').evaluate("element => getComputedStyle(element).opacity === '1' && element.getAnimations({subtree:true}).length === 0")
        # A different owner must never inherit the held page, even if its read fails.
        page.evaluate("window.__catalogOwner('t_other')")
        expect(window.locator('[data-artifact-chip]')).to_have_count(0)
        expect(window.locator('[aria-busy="true"]')).to_have_count(1)
        page.wait_for_timeout(50)
        assert pending_artifacts
        pending_artifacts.pop(0)[0].fulfill(status=503, json={"error":{"code":"offline","message":"offline"}})
        expect(window.get_by_role("button", name="Retry", exact=True)).to_be_visible()
        expect(window.locator('[data-artifact-chip]')).to_have_count(0)
        assert before_spacer.evaluate("element => element.offsetHeight") == 0
        assert after_spacer.evaluate("element => element.offsetHeight") == 0
        print("PASS catalog browser boundary: bounded history, artifact windows, retained pending/error geometry and owner isolation", flush=True)
    finally:
        page.close()


def _preview_lifecycle(browser, url):
    import base64
    import time

    from playwright.sync_api import expect

    started = time.monotonic()
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    reads = []
    held_reads = []
    hold_previews = False
    page.add_init_script("""window.__workerStarts=0;
      window.__retainedRoots=()=>[...document.querySelectorAll('[data-visible-content-state=retained]')]
        .filter(node=>!node.parentElement?.closest('[data-visible-content-state=retained]'));
      const NativeWorker=window.Worker;
      window.__heldWorkerMessages=[];
      window.__releaseWorkerMessages=()=>{
        window.__holdMarkdownWorker=false;
        window.__heldWorkerMessages.splice(0).forEach(send=>send());
      };
      window.Worker=class extends NativeWorker {
        constructor(...args){super(...args);window.__workerStarts++;}
        postMessage(...args){
          if(window.__holdMarkdownWorker){window.__heldWorkerMessages.push(()=>super.postMessage(...args));return;}
          super.postMessage(...args);
        }
      };""")
    page.on("pageerror",lambda error: print("PREVIEW ERROR",str(error),flush=True))
    page.on("console",lambda message: print("PREVIEW CONSOLE",message.text,flush=True) if message.type=="error" else None)
    def preview(route):
        reads.append(route.request.url)
        if hold_previews:
            held_reads.append(route)
            return
        route.fulfill(content_type="text/html", body="""<html><body data-count="0">
          Automatic visible preview <button onclick="document.body.dataset.count=Number(document.body.dataset.count)+1">Increment</button>
        </body></html>""")
    def markdown_preview(route):
        reads.append(route.request.url)
        if hold_previews:
            held_reads.append(route)
            return
        body = ('Small painted source' if 'refresh.md' in route.request.url else
                '# Nested retained source\n\n' + 'Nested source words. ' * 10000)
        route.fulfill(content_type='text/markdown', body=body)
    try:
        page.route("**/files/tickets/t_preview/*.html", preview)
        page.route("**/files/tickets/t_preview/*.md", markdown_preview)
        page.route("**/files/tickets/t_preview/*.png",lambda route: route.fulfill(content_type="image/png",body=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=")))
        page.goto(url+"?previews=1", wait_until="domcontentloaded")
        page.wait_for_function("document.querySelectorAll('[data-file-preview-html]').length > 0")
        assert 0 < len(reads) < 8, reads
        page.wait_for_function("document.querySelectorAll('[data-preview-fixture] img').length > 0")
        limit = page.evaluate("window.__inactiveLimit")
        # Traverse enough distinct previews to exceed the inactive budget. Lazy admission
        # and a fixed inactive budget bound native resources even after a complete scroll.
        page.evaluate("window.__firstPreview=document.querySelector('[data-file-preview-html]')")
        for index in (4, 8, 12, 16, 23):
            page.locator(f'[data-preview-index="{index}"]').evaluate(
                "element=>element.closest('[data-preview-fixture]').scrollTop=element.offsetTop"
            )
            page.wait_for_function(
                "index=>Array.from(document.querySelectorAll('[data-file-preview-html]')).some(frame=>frame.title.includes('preview'+index+'.html'))",
                arg=index,
            )
            page.wait_for_function("window.__retainedRoots().length <= window.__inactiveLimit")
            resource_counts = page.evaluate("""() => ({
              active:document.querySelectorAll('[data-visible-content-state=active]').length,
              retained:window.__retainedRoots().length,
              native:document.querySelectorAll('[data-preview-fixture] iframe,[data-preview-fixture] img').length})""")
            assert resource_counts['retained'] <= limit, resource_counts
            assert resource_counts['native'] <= resource_counts['active'] + limit, resource_counts
        page.wait_for_function("!window.__firstPreview.isConnected")
        # An evicted preview must read again; its measured slot holds both height and
        # the reader's scroll position while that unavoidable read is held.
        hold_previews = True
        page.locator('[data-preview-fixture]').evaluate("element=>element.scrollTop=0")
        page.wait_for_function("document.querySelector('[data-preview-index=\"0\"] [data-visible-content-restoring]')")
        restored_slot = page.locator('[data-preview-index="0"] [data-file-preview]').first
        held_geometry = restored_slot.evaluate("""element=>({height:element.getBoundingClientRect().height,
          scroll:element.closest('[data-preview-fixture]').scrollTop})""")
        assert held_geometry['height'] > 160, held_geometry
        page.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        assert restored_slot.evaluate("""element=>({height:element.getBoundingClientRect().height,
          scroll:element.closest('[data-preview-fixture]').scrollTop})""") == held_geometry
        assert held_reads, reads
        # A changed width invalidates the old width's measured reservation.
        page.set_viewport_size({'width': 800, 'height': 800})
        page.wait_for_function("document.querySelector('[data-preview-index=\"0\"] [data-file-preview]').style.minHeight === ''")
        hold_previews = False
        for route in held_reads:
            if 'preview0.html' in route.request.url:
                route.fulfill(status=503, body='Temporary restore failure')
            else:
                route.fulfill(content_type="text/html", body="<html><body>Fresh evicted response</body></html>")
        held_reads.clear()
        page.wait_for_function("!document.querySelector('[data-preview-index=\"0\"] [data-visible-content-restoring]')")
        assert not page.evaluate("window.__firstPreview.isConnected")
        assert sum('preview0.html' in href for href in reads) == 2, reads
        expect(restored_slot.locator('.quiet-line')).to_have_text('file fetch failed: 503')
        # Failure completes restoration and is retryable when this preview returns.
        page.locator('[data-preview-fixture]').evaluate("element=>element.scrollTop=element.scrollHeight")
        page.wait_for_function("document.querySelector('[data-preview-index=\"0\"] [data-visible-content-state]')?.dataset.visibleContentState !== 'active'")
        page.locator('[data-preview-fixture]').evaluate("element=>element.scrollTop=0")
        restored_slot.frame_locator('iframe').get_by_role('button', name='Increment').wait_for()
        assert sum('preview0.html' in href for href in reads) == 3, reads
        page.set_viewport_size({'width': 1100, 'height': 800})
        # A normal offscreen return remains inside the budget. Probe native identity,
        # iframe script state, reads and parser starts independently of opacity.
        for kind, target, selector in (
            ('preview', '/files/tickets/t_preview/retained.html', '[data-file-preview-html]'),
            ('document', '/files/tickets/t_preview/retained.html', '[data-file-preview-html]'),
            ('image', '/files/tickets/t_preview/image-retained.png', 'img'),
            ('markdown', '/files/tickets/t_preview/retained.html', '.markdown-block'),
            ('nested', '/files/tickets/t_preview/nested.md', '.markdown-block'),
        ):
            page.evaluate("([kind,target])=>window.__retained(kind,target)", [kind, target])
            page.locator('[data-preview-fixture]').evaluate("element=>element.scrollTop=0")
            content = page.locator('[data-retention-content]')
            native = content.locator(selector).first
            native.wait_for()
            if kind in ('preview', 'document'):
                content.frame_locator('iframe').get_by_role('button', name='Increment').click()
                assert content.frame_locator('iframe').locator('body').get_attribute('data-count') == '1'
            elif kind == 'image':
                page.wait_for_function("document.querySelector('[data-retention-content] img')?.complete")
            elif kind == 'nested':
                content.get_by_role('heading', name='Nested retained source').wait_for()
            else:
                page.get_by_text('Paragraph 0', exact=True).wait_for()
            native.evaluate("element=>{window.__retainedNode=element;window.__retainedWindow=element.contentWindow}")
            before_reads = len(reads)
            before_workers = page.evaluate('window.__workerStarts')
            page.locator('[data-preview-fixture]').evaluate("element=>element.scrollTop=element.scrollHeight")
            page.wait_for_function("document.querySelector('[data-retention-content] [data-visible-content-state=retained]')")
            page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'hidden'});document.dispatchEvent(new Event('visibilitychange'))")
            assert page.evaluate("window.__retainedNode.isConnected"), kind
            assert page.evaluate("window.__retainedRoots().length") <= limit, kind
            if kind == 'nested':
                assert page.evaluate("window.__retainedRoots().filter(node=>node.closest('[data-retention-content]')).length") == 1
                assert content.locator('[data-visible-content-state=released]').count() == 0
            page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'visible'});document.dispatchEvent(new Event('visibilitychange'))")
            page.locator('[data-preview-fixture]').evaluate("element=>element.scrollTop=0")
            page.wait_for_function("document.querySelector('[data-retention-content] [data-visible-content-state=active]')")
            assert native.evaluate("element=>element===window.__retainedNode && element.contentWindow===window.__retainedWindow"), kind
            assert len(reads) == before_reads, (kind, reads)
            assert page.evaluate('window.__workerStarts') == before_workers, kind
            if kind in ('preview', 'document'):
                assert content.frame_locator('iframe').locator('body').get_attribute('data-count') == '1'
            page.evaluate("window.__previewMode('none')")
            page.wait_for_function("!window.__retainedNode.isConnected")
            assert page.locator('[data-visible-content-state=retained]').count() == 0
        # Explicit refresh rereads a mounted document. Changing its target while the
        # refresh is pending cancels that generation; a late old body cannot win.
        page.evaluate("window.__retained('document','/files/tickets/t_preview/reload.html')")
        page.locator('[data-preview-fixture]').evaluate("element=>element.scrollTop=0")
        document_content = page.locator('[data-retention-content]')
        document_content.frame_locator('iframe').get_by_role('button', name='Increment').wait_for()
        document_content.locator('iframe').evaluate("element=>window.__refreshFrame=element")
        refresh_before = sum('reload.html' in href for href in reads)
        hold_previews = True
        with page.expect_request('**/files/tickets/t_preview/reload.html'):
            page.evaluate("window.__retained('document','/files/tickets/t_preview/reload.html',1)")
        assert document_content.locator('iframe').evaluate("element=>element===window.__refreshFrame")
        expect(document_content.locator('.skeleton')).to_have_count(0)
        assert sum('reload.html' in href for href in reads) == refresh_before + 1
        with page.expect_request('**/files/tickets/t_preview/replacement.html'):
            page.evaluate("window.__retained('document','/files/tickets/t_preview/replacement.html')")
        expect(document_content.locator('.skeleton')).to_have_count(1)
        hold_previews = False
        for route in held_reads:
            route.fulfill(content_type='text/html', body='<html><body>' + (
                'Current target' if 'replacement.html' in route.request.url else 'Obsolete refresh'
            ) + '</body></html>')
        held_reads.clear()
        document_content.frame_locator('iframe').get_by_text('Current target', exact=True).wait_for()
        assert document_content.frame_locator('iframe').get_by_text('Obsolete refresh').count() == 0
        page.evaluate("window.__previewMode('none')")
        expect(page.locator('[data-visible-content-state]')).to_have_count(0)
        # A small painted Markdown file must also survive crossing the worker
        # threshold on refresh. Hold both the HTTP read and actual parse separately.
        page.evaluate("window.__retained('document','/files/tickets/t_preview/refresh.md')")
        page.locator('[data-preview-fixture]').evaluate("element=>element.scrollTop=0")
        small_source = page.get_by_text('Small painted source', exact=True)
        small_source.wait_for()
        small_source.evaluate("element=>window.__smallMarkdown=element")
        hold_previews = True
        with page.expect_request('**/files/tickets/t_preview/refresh.md'):
            page.evaluate("window.__retained('document','/files/tickets/t_preview/refresh.md',1)")
        assert page.evaluate('window.__smallMarkdown.isConnected')
        assert page.locator('[data-retention-content] .skeleton').count() == 0
        page.evaluate('window.__holdMarkdownWorker=true')
        hold_previews = False
        assert len(held_reads) == 1
        held_reads.pop().fulfill(content_type='text/markdown',
            body='## Large refreshed source\n\n' + 'Large refreshed words. ' * 10000)
        page.wait_for_function('window.__heldWorkerMessages.length > 0')
        page.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        assert page.evaluate('window.__smallMarkdown.isConnected')
        assert small_source.is_visible()
        assert page.locator('[data-retention-content] .skeleton').count() == 0
        page.evaluate('window.__releaseWorkerMessages()')
        page.get_by_role('heading', name='Large refreshed source').wait_for()
        assert not page.evaluate('window.__smallMarkdown.isConnected')
        page.evaluate("window.__previewMode('none')")
        expect(page.locator('[data-visible-content-state]')).to_have_count(0)
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
        print(f"PASS preview lifecycle: bounded retained frames/images, stable evicted restore, worker Markdown windows and full-text copy ({time.monotonic()-started:.2f}s)",flush=True)
    finally:
        page.close()


def _settle_conversation_layout(page):
    # Endpoint assertions follow the actual coordinator, not a fixed frame count.
    # Two idle, unchanged frames also let ResizeObserver and reconciliation settle.
    page.evaluate("""() => new Promise((resolve, reject) => {
      const started = performance.now();
      let previous = '', quiet = 0;
      const sample = () => {
        const pane = document.querySelector('[data-conversation-pane]');
        const thread = pane?.querySelector('[data-conversation-thread]');
        const box = pane?.getBoundingClientRect();
        const geometry = JSON.stringify([box?.top, box?.height, thread?.clientHeight,
          thread?.scrollTop, thread?.scrollHeight]);
        const active = pane?.hasAttribute('data-conversation-motion')
          || pane?.getAnimations({subtree:true}).some(animation =>
            animation.playState === 'running' && Number.isFinite(animation.effect?.getComputedTiming().endTime));
        quiet = !active && geometry === previous ? quiet + 1 : 0;
        previous = geometry;
        if (quiet >= 2) return resolve();
        if (performance.now() - started > 10000) return reject(Error('Conversation did not reach an idle layout: ' + geometry));
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    })""")


def _run(browser, urls):
    controls_only = __import__('os').environ.get('CONVERSATION_SETTLED_CASE') == 'controls'
    if not controls_only:
        _catalogs(browser, urls[0])
        _preview_lifecycle(browser, urls[0])

    import json
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
        _settle_conversation_layout(page)

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

    def assert_native_growth(page, held, growth):
        # Native anchoring can hold an existing node or let growth above it move it.
        # It must not replace that node or return a scrollback reader to the end.
        settled(page)
        actual = page.evaluate("""() => ({connected: window.__heldParagraph.isConnected,
          text: window.__heldParagraph.textContent,
          top: window.__heldParagraph.getBoundingClientRect().top - document.querySelector('[data-conversation-thread]').getBoundingClientRect().top})""")
        assert actual["connected"] and actual["text"] == held["text"], (held, actual)
        displacement = actual["top"] - held["top"]
        assert -3 <= displacement <= growth + 3, (held, actual, growth)
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
        # the shortened transcript's range. The native clamp reaches the newest line.
        before = newest_reading(page)["scrollTop"]
        page.evaluate("window.__replaceAllScrollRows(20)")
        settled(page)
        limit = page.locator(THREAD).evaluate("thread => thread.scrollHeight-thread.clientHeight")
        actual = newest_reading(page)["scrollTop"]
        assert before > limit and abs(actual - limit) <= 3, (before, limit, actual)
        assert_newest(page)
        page.evaluate(
            "window.__appendConversationLine();window.__appendConversationLine();window.__appendConversationLine()"
        )
        assert_newest(page)

    def layer_send_case(page, url):
        prepare_scroll_case(page, url, "plain", "scrollback")
        page.evaluate("window.__sendScrollRegression(true)")
        state(page, "opened")
        settled(page)
        sent = answer_room_reading(page)
        assert abs(sent["promptTop"]-32)<=2, sent
        # Explicit send placement wins even when the layer changes height.
        page.locator(INPUT).fill("one\ntwo\nthree\nfour\nfive")
        settled(page)
        resized = answer_room_reading(page)
        assert abs(resized["promptTop"] - sent["promptTop"]) <= 3, (sent, resized)
        page.evaluate("window.__answerScrollRegression(1)")
        settled(page)
        answered = answer_room_reading(page)
        assert abs(answered["promptTop"] - sent["promptTop"]) <= 3, (sent, answered)

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
        assert abs(sent["promptTop"]-32)<=2, sent
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
        # Four paragraphs exhaust this small answer room without an unrelated
        # twenty-paragraph backlog of the approved line-at-a-time travel.
        page.evaluate("window.__answerScrollRegression(4)")
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
        # The browser can adjust for growth before native wheel motion begins.
        native_before = page.evaluate("window.__nativeRaceBefore")
        assert abs(after["scrollTop"] - native_before - 120) <= 3, (native_before, after)
        assert after["scrollTop"] > before + 50, (before, after)
        held = visible_paragraph(page)
        page.evaluate("window.__raceImage.style.height = '220px'")
        assert_native_growth(page, held, 40)

    def short_message_growth_case(page, url):
        prepare_scroll_case(page, url, "plain", "scrollback")
        # Production workspace insets leave less transcript room than the old copied
        # host. Keep this case's actual premise: the first message fits in the viewport.
        page.evaluate("window.__shortenFirstConversationLine()")
        settled(page)
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
        assert_native_growth(page, held, 180)
        assert newest_reading(page)["scrollTop"] <= 1
        assert page.evaluate("window.__raceImage.getBoundingClientRect().height") == 180

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
            elif key.endswith("Home"):
                assert_paragraph(page, held)
            else:
                assert_native_growth(page, held, 180)
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
            old_height = page.evaluate("window.__raceImage.getBoundingClientRect().height")
            page.evaluate('(amount) => window.__raceImage.style.height = amount+"px"', amount)
            assert_native_growth(page, held, amount - old_height)
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
        before_row_height = page.evaluate("window.__delayedRow.getBoundingClientRect().height")
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
        growth = page.evaluate("window.__delayedRow.getBoundingClientRect().height") - before_row_height
        assert growth > 0, growth
        if held:
            assert held["text"].startswith("Tall paragraph"), held
            actual = assert_native_growth(page, held, growth)
            actual_scroll = newest_reading(page)["scrollTop"]
        else:
            actual = assert_newest(page)
            actual_scroll = actual["scrollTop"]
        if not held:
            assert abs((actual_scroll - before["scrollTop"]) - growth) <= 2, (growth, before, actual_scroll)
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
        if controls_only:
            break
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

    # The Add menu consumes the first Escape without changing the pane or refocusing Add.
    add = page.locator("[data-conversation-add]")
    add.click()
    page.locator(INPUT).focus()
    page.keyboard.press("Escape")
    assert page.locator(".chat-add-menu").count() == 0
    assert page.locator(INPUT).evaluate("input => document.activeElement === input")
    state(page, "peeked")
    # A disabled composer cannot leave an open menu active.
    add.click()
    page.evaluate("window.__setComposerDisabled(true)")
    page.wait_for_function("!document.querySelector('.chat-add-menu')")
    assert add.is_disabled()
    page.evaluate("window.__setComposerDisabled(false)")
    page.wait_for_function("!document.querySelector('[data-conversation-add]').disabled")
    # The inline model chooser uses the same one-tab-stop shell and focus contract.
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
    _settle_conversation_layout(page)
    page.mouse.move(0, 0)
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
    layer = page.locator(".conversation-layer").bounding_box()
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
    old_height = thread.evaluate("node=>node.clientHeight")
    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    settled(page)
    height_delta = thread.evaluate("node=>node.clientHeight") - old_height
    held = thread.evaluate("""(thread,text)=>{const node=[...thread.querySelectorAll('[data-conversation-row="agent_message"]')].find(node=>node.textContent.trim()===text);return node.getBoundingClientRect().top-thread.getBoundingClientRect().top;}""", anchored['text'])
    assert abs(held-anchored['top']-height_delta)<=3, (anchored,held,height_delta)
    anchored = first_visible_line(page)
    assert anchored is not None

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

    # Latest removes the active control. Both activation paths return the keyboard
    # to the reading region without moving the newest line out of sight.
    latest = page.get_by_role("button", name="Jump to latest message", exact=True)
    for activation in ("pointer", "keyboard", "focus-elsewhere"):
        if activation != "pointer":
            thread.evaluate("node => {node.scrollTop=0;node.dispatchEvent(new Event('scroll'));}")
        latest.wait_for()
        if activation == "pointer":
            latest.click()
        elif activation == "keyboard":
            latest.focus()
            page.keyboard.press("Enter")
        else:
            latest.evaluate("""button => {
              button.focus();button.click();
              document.querySelector('[data-conversation-input]').focus({preventScroll:true});
            }""")
        page.wait_for_function("!document.querySelector('button[aria-label=\"Jump to latest message\"]')")
        page.wait_for_function("""elsewhere => document.activeElement === document.querySelector(
          elsewhere ? '[data-conversation-input]' : '[data-conversation-thread]')""",
          arg=activation == "focus-elsewhere")
        page.wait_for_function("""() => {
          const thread = document.querySelector('[data-conversation-thread]');
          const newest = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')].at(-1);
          return newest.getBoundingClientRect().bottom <= thread.getBoundingClientRect().bottom + 1;
        }""")
        print("LATEST_FOCUS_PROOF", activation, page.evaluate("""() => ({
          tag:document.activeElement.tagName,label:document.activeElement.getAttribute('aria-label'),
          latestPresent:!!document.querySelector('button[aria-label="Jump to latest message"]')})"""), flush=True)
        if activation == "keyboard" and os.environ.get("PANELS_E2E_FAILURE_DIR"):
            from pathlib import Path
            output = Path(os.environ["PANELS_E2E_FAILURE_DIR"]).parent
            page.screenshot(path=str(output / "latest-focus.png"))
    # Preserve the existing following proof after the explicit jump.
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
    settled(page)
    page.locator('[data-conversation-held-row="held-1"]').wait_for()
    assert page.locator('[data-conversation-held-promote="steer"]').count() == 0
    page.evaluate("window.__showHeldPrompt(true)")
    steer = page.locator('[data-conversation-held-row="held-1"] [data-conversation-held-promote="steer"]')
    steer.wait_for()
    assert steer.inner_text() == "Steer"
    assert "Hermes" not in page.locator("[data-conversation-held-stack]").inner_text()

    # An unanswered send retains its bubble and carries its two existing actions there.
    page.evaluate("window.__showUnansweredSend()")
    settled(page)
    bubble = page.locator('[data-conversation-outgoing="sent-into-the-dark"]')
    bubble.wait_for()
    assert "no answer came" in bubble.inner_text()
    assert page.locator('[data-conversation-held-stack]').count() == 0
    assert page.locator('[data-conversation-held-promote="send_now"]').count() == 0
    assert page.locator('[data-conversation-held-promote="steer"]').count() == 0
    send_again = bubble.get_by_role('button', name='Send again', exact=True)
    send_again.wait_for()
    if screenshot_dir:
        bubble.screenshot(
            path=os.path.join(screenshot_dir, "unanswered-send-in-place.png")
        )
    send_again.click()
    page.wait_for_function("window.__sentAgain === 'sent-into-the-dark'")
    bubble.get_by_role('button', name='Stop showing this message', exact=True).click()
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

    # Inline voice leads Stop and Send; an empty draft offers no Send.
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
    page.wait_for_function("() => !document.querySelector('[data-conversation-send]')")
    assert submit_order(page) == ["delivery", "mic"]

    page.locator(INPUT).fill("something to send")
    page.wait_for_function("() => document.querySelector('[data-conversation-send]') !== null")
    assert submit_order(page) == ["delivery", "mic", "send"]

    page.evaluate("window.__showWorkRunning()")
    page.wait_for_function("() => document.querySelector('[data-conversation-stop]') !== null")
    assert submit_order(page) == ["delivery", "mic", "stop", "send"]
    page.locator(INPUT).fill("")
    page.wait_for_function("() => !document.querySelector('[data-conversation-send]')")
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

    # Settled Focus retains every owner answer and its head, without an empty disclosure.
    page.evaluate("window.__showSettledTurn()")
    state(page, "opened")
    assert page.get_by_text("hidden commentary", exact=True).count() == 0
    assert page.locator('[data-conversation-row="tool_call"]').count() == 0
    for text in ("Owner answer one.", "Owner answer two.", "final answer"):
        page.get_by_text(text, exact=True).wait_for()
        assert page.get_by_text(text, exact=True).is_visible()
    assert page.locator("[data-conversation-turn-settled-head]").count() == 1
    assert page.locator("[data-conversation-turn-fold], .c2-turn-chevron, [data-conversation-work-fold]").count() == 0
    page.locator('[data-conversation-lens-choice="full"]').click()
    assert page.get_by_text("hidden commentary", exact=True).count() == 1
    assert page.locator('[data-conversation-row="tool_call"]').count() == 4
    assert page.locator("[data-conversation-work-fold]").count() == 0
    assert page.locator("[data-conversation-turn-fold]").count() == 0
    assert page.locator("[data-conversation-turn-count]").count() == 0
    assert page.locator("[data-conversation-turn-settled-head]").count() == 1
    for text in ("Owner answer one.", "Owner answer two.", "final answer", "middle commentary"):
        page.get_by_text(text, exact=True).wait_for()
        assert page.get_by_text(text, exact=True).count() == 1
    legacy_evidence = os.environ.get("PANELS_CONVERSATION_PARITY_EVIDENCE")
    if legacy_evidence:
        from pathlib import Path
        Path(legacy_evidence, "legacy-owner-fold-proof.json").write_text(json.dumps({
            "focusOwnerMessages":3,"focusSettledHeads":1,"focusFoldControls":0,
            "fullOwnerMessages":3,"fullTools":4,"fullFoldControls":0}, indent=2))
        page.screenshot(path=str(Path(legacy_evidence, "legacy-owner-full.png")))

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
          overflow:getComputedStyle(e).overflowY,
          inset:["paddingTop","paddingBottom","borderTopWidth","borderBottomWidth"].reduce((sum,key)=>sum+parseFloat(getComputedStyle(e)[key]),0)})""")

    def settle_frames():
        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve))))"
        )

    settle_frames()
    resting = measure_writing_area()
    assert resting["height"] >= resting["line"] * 3, resting
    assert abs(resting["height"] - resting["inset"] - resting["line"] * 6) <= 1, resting
    assert resting["overflow"] == "auto", resting
    for _ in range(3):
        settle_frames()
        assert measure_writing_area() == resting
    page.locator(INPUT).click()
    state(page, "peeked")
    settle_frames()
    peeked = measure_writing_area()
    assert peeked == resting, peeked
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
        settled(queued_page)
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
    # The card uses the title/text channel, with 24px outside each edge.
    assert abs(phone_bounds["columnWidth"] - min(720, phone_bounds["viewportWidth"] - 48)) <= 1, phone_bounds
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


def _owner_width_frames(browser, url):
    import json
    import os
    import time
    from pathlib import Path

    started = time.perf_counter()
    output = Path(os.environ.get("PANELS_OWNER_WIDTH_ARTIFACT_DIR", os.environ.get("PANELS_E2E_FAILURE_DIR", "/tmp")))
    output.mkdir(parents=True, exist_ok=True)
    proof = []
    # Reuse the real reader/pane host for first-frame visibility and width on open,
    # reload and a narrow pane, plus immediate completion and reduced motion.
    for name, width, mode, motion, open_from_rest, expected_cap in (
        ("open", 1000, "delayed", "no-preference", True, 78),
        ("narrow", 420, "delayed", "no-preference", False, 86),
        ("immediate", 1000, "immediate", "no-preference", False, 78),
        ("reduced", 420, "delayed", "reduce", False, 86),
    ):
        page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion=motion)
        # Install before navigation so even the first immediate-body frame counts.
        page.add_init_script("""
          window.__ownerWidthFrames = [];
          function sampleOwnerWidth() {
            for (const key of ['e1', 'e2']) {
              const element = document.querySelector('[data-conversation-view-key="'+key+'"] .chat-u');
              if (!element) continue;
              const bounds = element.getBoundingClientRect();
              const wrapper = element.closest('[data-conversation-view-key]').getBoundingClientRect();
              const opacity = Number(getComputedStyle(element).opacity);
              const thread = element.closest('[data-conversation-thread]').getBoundingClientRect();
              const ancestors=[];
              for(let node=element;node;node=node.parentElement){
                const style=getComputedStyle(node);
                ancestors.push({opacity:+style.opacity,transform:style.transform,
                  animations:node.getAnimations().filter(a=>a.playState!=='finished').length});
              }
              if (bounds.width && bounds.bottom > thread.top && bounds.top < thread.bottom) window.__ownerWidthFrames.push({key, width:bounds.width,
                right:bounds.right, parentRight:wrapper.right, parentWidth:wrapper.width, opacity,
                maxWidth:parseFloat(getComputedStyle(element).maxWidth),
                ancestors});
            }
            if (!window.__stopOwnerWidth) requestAnimationFrame(sampleOwnerWidth);
          }
          requestAnimationFrame(sampleOwnerWidth);
        """)
        page.goto(url + "?reader=1&owner-width=" + mode + ("&reader-rest=1" if open_from_rest else ""))
        if open_from_rest:
            page.evaluate("window.__readerState('opened')")
        for navigation in ([name, "reload"] if name == "open" else [name]):
            if navigation == "reload":
                page.reload()
                page.evaluate("window.__readerState('opened')")
            if mode == "delayed":
                page.wait_for_selector('[data-conversation-view-key="e1"] .skeleton[data-entering]')
                page.evaluate("window.__releaseOwnerBody()")
            page.wait_for_function("window.__ownerWidthFrames.filter(frame => frame.key === 'e1').length >= 3")
            frames = page.evaluate("window.__ownerWidthFrames")
            (output / ("implementation-owner-width-" + navigation + "-frames.json")).write_text(json.dumps(frames, indent=2))
            if navigation == "open":
                page.screenshot(path=str(output / "implementation-owner-width-settled.png"))
            short = [frame for frame in frames if frame["key"] == "e1"]
            settled = short
            assert short, (navigation, frames)
            assert 0 < settled[-1]["parentWidth"] <= width, (navigation, settled[-1])
            assert settled[-1]["parentRight"] <= width + 1, (navigation, settled[-1])
            final_width = settled[-1]["width"]
            for frame in frames:
                assert frame['opacity'] == 1, (navigation, frame)
                assert all(node['opacity'] == 1 and node['transform'] == 'none' and node['animations'] == 0
                           for node in frame['ancestors']), (navigation, frame)
                assert abs(frame["right"] - frame["parentRight"]) < 1, (navigation, frame)
                assert frame["width"] <= frame["parentWidth"] * expected_cap / 100 + 1, (navigation, frame)
                assert frame["maxWidth"] == expected_cap, (navigation, frame)
                if frame["key"] == "e1":
                    assert abs(frame["width"] - final_width) < 1, (navigation, frame, final_width)
                    assert frame["width"] < frame["parentWidth"] * .65, (navigation, frame)
            assert page.locator('[data-conversation-view-key="e3"] .chat-system').inner_text().startswith("Chief")
            proof.append({"case": navigation, "paneWidth": settled[-1]["parentWidth"], "bubbleWidth": final_width,
                          "readyFrames": len(settled)})
        page.evaluate("window.__stopOwnerWidth=true")
        page.close()
    proof.append({"seconds": round(time.perf_counter() - started, 3), "navigations": 5, "newHosts": 0})
    (output / "implementation-owner-width-proof.json").write_text(json.dumps(proof, indent=2))
    print("OWNER_WIDTH_PROOF " + json.dumps(proof), flush=True)


def _latest_obsolete_intents(browser, url):
    import json
    import time

    started = time.perf_counter()
    page = browser.new_page(viewport={"width":1000,"height":900})
    proof=[]
    try:
        for case in ("refusal", "superseded", "reader-swap"):
            page.goto(url + "?intent=1")
            thread=page.locator('[data-conversation-thread]')
            thread.wait_for()
            page.wait_for_function("document.querySelector('[data-conversation-thread]').scrollHeight > 1000")
            thread.evaluate("node=>{node.scrollTop=500;node.dispatchEvent(new Event('scroll'));}")
            latest=page.get_by_role("button",name="Jump to latest message",exact=True)
            latest.wait_for()
            latest.click()
            page.wait_for_function("window.__intentProbe.loads.length===1")
            if case == "superseded":
                latest.click()
                page.wait_for_function("window.__intentProbe.loads.length===2")
            # No geometry can settle while hidden. Complete the obsolete request,
            # then restore geometry, exercising deferred placement and focus.
            thread.evaluate("node=>node.style.display='none'")
            if case == "reader-swap":
                # Keep the original button's focus claim alive. Admission succeeds
                # first, but geometry cannot settle until after reader replacement.
                latest.evaluate("node=>node.blur()")
            else:
                page.locator('[data-conversation-input]').focus()
            page.evaluate("admitted=>window.__intentProbe.finish(0,admitted)",case != "refusal")
            page.evaluate("async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}")
            if case == "reader-swap":
                assert page.evaluate("document.activeElement===document.body")
                page.evaluate("window.__intentProbe.swap()")
            thread.evaluate("node=>node.style.display=''")
            page.evaluate("async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}")
            assert thread.evaluate("node=>Math.abs(node.scrollTop-500)<2"), (case,thread.evaluate("node=>node.scrollTop"))
            if case == "reader-swap":
                assert page.evaluate("document.activeElement===document.body"),case
            else:
                assert page.evaluate("document.activeElement===document.querySelector('[data-conversation-input]')"),case
            assert latest.is_visible(),case
            if case == "superseded":
                page.evaluate("window.__intentProbe.finish(1,true)")
                page.wait_for_function("!document.querySelector('button[aria-label=\"Jump to latest message\"]')")
                assert page.evaluate("document.activeElement===document.querySelector('[data-conversation-input]')")
            proof.append({"case":case,"obsoletePlacement":False,"focus":"body preserved after admitted reader swap" if case == "reader-swap" else "composer preserved"})
        # Swap after placement removes Latest, in the mutation callback before
        # the focus tick resumes. The old button's body-focus claim must not pass
        # to a reader that did not own that click.
        page.goto(url + "?intent=1")
        thread=page.locator('[data-conversation-thread]')
        thread.wait_for()
        page.wait_for_function("document.querySelector('[data-conversation-thread]').scrollHeight > 1000")
        thread.evaluate("node=>{node.scrollTop=500;node.dispatchEvent(new Event('scroll'));}")
        latest=page.get_by_role("button",name="Jump to latest message",exact=True)
        latest.click()
        page.wait_for_function("window.__intentProbe.loads.length===1")
        page.evaluate("""() => {
          const thread=document.querySelector('[data-conversation-thread]');
          const focus=thread.focus.bind(thread);
          window.__obsoleteFocusCalls=0;
          thread.focus=(...args)=>{window.__obsoleteFocusCalls++;focus(...args);};
          const observer=new MutationObserver(()=>{
            if(document.querySelector('button[aria-label="Jump to latest message"]'))return;
            observer.disconnect();
            window.__focusTickSwap=true;
            window.__intentProbe.swapSynchronously();
          });
          observer.observe(document.querySelector('[data-conversation-pane]'),{subtree:true,childList:true});
          window.__intentProbe.finish(0,true);
        }""")
        page.wait_for_function("window.__focusTickSwap===true")
        page.evaluate("async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);}")
        assert page.evaluate("window.__obsoleteFocusCalls===0"), "Obsolete focus crossed reader replacement during focus tick"
        assert page.evaluate("document.activeElement===document.body")
        proof.append({"case":"reader-swap-at-focus-tick","obsoleteFocusCalls":0})
        print("LATEST_OBSOLETE_INTENTS_PROOF "+json.dumps({"cases":proof,"seconds":round(time.perf_counter()-started,3),"newHosts":0}),flush=True)
    finally:
        page.close()


def _latest_destination(browser, url):
    import json
    import os
    import time
    from pathlib import Path

    started = time.perf_counter()
    page = browser.new_page(viewport={"width": 1000, "height": 900})
    output = Path(os.environ.get("PANELS_LATEST_ARTIFACT_DIR", os.environ.get("PANELS_E2E_FAILURE_DIR", "/tmp")))
    output.mkdir(parents=True, exist_ok=True)
    try:
        page.goto(url + "?reader=1")
        page.wait_for_selector('[data-conversation-view-key="e2000"]')
        page.evaluate("window.__latestProbe.blockForward = true")
        # Three historical pages exceed the real 300-row retention bound.
        for _ in range(3):
            before = page.evaluate("Math.min(...[...document.querySelectorAll('[data-conversation-view-key]')].map(n=>+n.dataset.conversationViewKey.slice(1)))")
            page.locator('[data-conversation-thread]').evaluate("node => {node.scrollTop=0;node.dispatchEvent(new Event('scroll'));}")
            page.wait_for_function("before => Math.min(...[...document.querySelectorAll('[data-conversation-view-key]')].map(n=>+n.dataset.conversationViewKey.slice(1))) < before", arg=before)
        assert page.locator('[data-conversation-view-key="e2000"]').count() == 0
        assert page.locator('[data-conversation-view-key]').count() <= 300
        page.evaluate("window.__latestProbe.holdHistory = true")
        page.locator('[data-conversation-thread]').evaluate("node => {node.scrollTop=0;node.dispatchEvent(new Event('scroll'));}")
        page.wait_for_function("window.__latestProbe.historyWaiting > 0")
        page.evaluate("""() => {
          window.__latestFrames=[];
          const sample = () => {
            const thread=document.querySelector('[data-conversation-thread]');
            const edge=thread.getBoundingClientRect();
            const row=document.querySelector('[data-conversation-view-key="e2000"]');
            const bounds=row?.getBoundingClientRect();
            const visible=!!bounds && bounds.bottom>edge.top && bounds.top<edge.bottom;
            window.__latestFrames.push({visibleNewest:visible,
              latest:!!document.querySelector('button[aria-label="Jump to latest message"]'),
              latestResponses:window.__latestProbe.latestResponses,
              scrollTop:thread.scrollTop, scrollHeight:thread.scrollHeight});
            if(!window.__stopLatestFrames)requestAnimationFrame(sample);
          };
          requestAnimationFrame(sample);
        }""")
        page.get_by_role("button", name="Jump to latest message", exact=True).click()
        page.evaluate("window.__latestProbe.holdHistory=false;window.__latestProbe.releaseHistory()")
        # On the defect, Latest disappears at the retained historical endpoint.
        # Do not wait for a fetch hook: destination and control trajectory decide.
        try:
            page.wait_for_function("window.__latestFrames.some(f => !f.latest)", timeout=10000)
        finally:
            frames=page.evaluate("window.__latestFrames")
            (output / "latest-destination-frames.json").write_text(json.dumps(frames, indent=2))
        first_hidden=next(frame for frame in frames if not frame["latest"])
        assert first_hidden["visibleNewest"], {"failure":"Latest disappeared before the true newest destination", "frame":first_hidden}
        assert first_hidden["latestResponses"] > 0, first_hidden
        assert page.evaluate("window.__latestProbe.blockForward"), "Recovery gate opened before destination assertion"
        # All frames that presented Latest as complete must already show e2000.
        assert all(frame["visibleNewest"] for frame in frames if not frame["latest"]), frames
        page.screenshot(path=str(output / "latest-destination.png"))
        proof={"destination":"e2000 visible before Latest disappears", "recovery":"blocked through destination assertion", "seconds":round(time.perf_counter()-started,3), "newHosts":0}
        (output / "latest-destination-proof.json").write_text(json.dumps(proof,indent=2))
        print("LATEST_DESTINATION_PROOF " + json.dumps(proof), flush=True)
        page.evaluate("window.__latestProbe.blockForward=false;window.__latestProbe.holdHistory=false;window.__latestProbe.releaseForward();window.__latestProbe.releaseHistory();window.__stopLatestFrames=true")
    finally:
        page.close()


def _bounded_reader(browser, url):
    import os
    from pathlib import Path

    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1000, "height": 900})
    interruption_requests = []
    page.on("request", lambda request: interruption_requests.append(request.url)
            if "deployment-interruptions" in request.url else None)
    page.goto(url + "?reader=1")
    expect(page.locator("[data-conversation-interruption]")).to_have_count(0)
    page.wait_for_function("() => document.querySelectorAll('[data-conversation-view-key]').length > 0")
    page.wait_for_function("() => document.querySelector('[data-conversation-thread]')?.textContent.includes('Owner answer 2000.')")
    assert interruption_requests == [], interruption_requests
    evidence = Path(os.environ["PANELS_SIDEBAR_ARTIFACT_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(animations="disabled", path=str(evidence / "conversation-without-interruption-banner.png"))
    # What the reader retains is what is drawn. One bound, not two: a row that is
    # held but not drawn has to claim a height it has not measured, and that is what
    # made the thread change size under a gesture.
    assert page.locator('[data-conversation-row="agent_message"]').count() <= 300
    assert page.evaluate("() => window.__viewCalls.every(url => !url.includes('/events?') && (!url.includes('/tail?') || url.includes('/view/tail?')))")
    # Hidden candidate rows exist before cold-entry placement. Wait for the visible
    # destination before the reader starts a history gesture.
    page.wait_for_function("document.querySelector('[data-conversation-entry]')?.dataset.conversationEntry === 'complete'")
    before = page.evaluate("() => window.__viewCalls.filter(url => url.includes('/view/pages')).length")
    # Use an owner navigation gesture so active follow motion yields before scroll.
    page.locator('[data-conversation-thread]').focus()
    page.locator('[data-conversation-thread]').press("Home")
    page.wait_for_function("before => window.__viewCalls.filter(url => url.includes('/view/pages')).length > before", arg=before)
    # A transport call records request start, before the reader admits its page.
    # Wait for both edges of the older page and its layout before requesting Latest.
    page.wait_for_function("() => document.querySelector('[data-conversation-view-key=\"e1801\"]') && document.querySelector('[data-conversation-view-key=\"e1900\"]')")
    # Leave both automatic history-edge demand zones before testing an explicit read.
    page.locator('[data-conversation-thread]').evaluate("node => {node.scrollTop=(node.scrollHeight-node.clientHeight)/2;node.dispatchEvent(new Event('scroll'));}")
    page.wait_for_function("""async () => {
      const reads = () => window.__viewCalls.filter(url => url.includes('/view/pages')).length;
      const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
      const before = reads();
      await frame();
      if (reads() !== before) return false;
      await frame();
      const thread = document.querySelector('[data-conversation-thread]');
      return reads() === before && thread.scrollTop > thread.clientHeight
        && thread.scrollTop + 2 * thread.clientHeight < thread.scrollHeight;
    }""")
    assert page.locator('[data-conversation-view-key]').count() <= 300
    assert page.locator('[data-conversation-row="agent_message"]').count() <= 300
    # A delayed latest-page read must respect focus moved during the request.
    import time
    focus_started = time.perf_counter()
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const path = String(args[0]);
        const url = new URL(path, location.origin);
        if (url.pathname.endsWith('/view/pages') && !url.searchParams.has('cursor')) {
          window.fetch = original;
          await new Promise(resolve => {window.__releaseLatestPage = resolve;});
        }
        return original(...args);
      };
    }""")
    latest = page.get_by_role("button", name="Jump to latest message", exact=True)
    latest.click()
    page.wait_for_function("typeof window.__releaseLatestPage === 'function'")
    page.locator('[data-conversation-input]').focus()
    page.evaluate("window.__releaseLatestPage()")
    page.wait_for_function("!document.querySelector('button[aria-label=\"Jump to latest message\"]')")
    page.wait_for_function("document.activeElement === document.querySelector('[data-conversation-input]')")
    print(f"LATEST_DELAYED_FOCUS_PROOF composer focus preserved after controlled admission ({time.perf_counter()-focus_started:.3f}s)", flush=True)
    page.evaluate("() => window.__readerState('rest')")
    page.wait_for_function("() => !document.querySelector('[data-conversation-thread]')")
    expect(page.locator("[data-conversation-interruption]")).to_have_count(0)
    assert page.locator('[data-conversation-view-key]').count() == 0
    page.evaluate("() => window.__readerState('opened')")
    page.wait_for_selector('[data-conversation-thread]')
    expect(page.locator("[data-conversation-interruption]")).to_have_count(0)
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

    # Canonical replacement destroys component caches even at unchanged coverage.
    import time
    from pathlib import Path
    started = time.perf_counter()
    page.goto(url + "?reader=1&expiry=1")
    page.locator('[data-conversation-lens-choice="full"]').click()
    page.locator('[data-conversation-tool="expiry-tool"]').click()
    page.get_by_text("Cached whole diagnostic", exact=True).wait_for()
    page.evaluate("() => window.__expireDiagnostics()")
    page.get_by_text("Some diagnostic history expired.", exact=True).wait_for()
    page.get_by_text("This diagnostic detail expired.", exact=True).wait_for()
    assert page.get_by_text("Cached whole diagnostic", exact=True).count() == 0
    page.evaluate("() => window.__completeDiagnosticBody()")
    page.wait_for_timeout(100)
    assert page.locator('[data-conversation-view-key="e3"]').count() == 0
    assert page.get_by_text("Late diagnostic body must disappear.", exact=True).count() == 0
    assert page.get_by_text("Cached whole diagnostic", exact=True).count() == 0
    page.evaluate("() => window.__expireDeferred()")
    page.locator('[data-conversation-view-key="e3"]').get_by_text("This diagnostic detail expired.", exact=True).wait_for()
    evidence = Path(os.environ.get("PANELS_E2E_FAILURE_DIR", "data/verification/browser-failure")) / "diagnostic-expiry.png"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence))
    print(f"Diagnostic expiry browser scenario: {time.perf_counter() - started:.3f}s")

    page.close()


def _projected_visual_relationships(browser, url):
    import json
    import os
    from pathlib import Path

    page = browser.new_page(viewport={"width": 1000, "height": 1100})
    page.goto(url + "?reader=1&visual=1")
    second = page.locator('[data-conversation-view-key="e4"] .chat-a')
    second.wait_for()
    second.scroll_into_view_if_needed()
    focus = second.evaluate("""element => {
        const wrapper = element.closest('[data-conversation-view-key]');
        const decoration = node => ({separator: getComputedStyle(node, '::before').content,
            margin: parseFloat(getComputedStyle(node).marginBlockStart)});
        const decorations = [decoration(element), decoration(wrapper)];
        const first = document.querySelector('[data-conversation-view-key="e2"]');
        return {decorations, first: [decoration(first), decoration(first.querySelector('.chat-a'))]};
    }""")
    page.locator('[data-conversation-lens-choice="full"]').click()
    page.locator('[data-conversation-tool="tool-5"]').wait_for()
    stream = page.locator('[data-conversation-row="streaming"]')
    stream.wait_for()
    stream.scroll_into_view_if_needed()
    page.wait_for_function("""() => !document.querySelector('[data-conversation-view-key="e13"]') &&
        window.__viewCalls.some(raw => {
            const url = new URL(raw, location.origin);
            const reference = url.searchParams.get('reference');
            return reference && JSON.parse(reference).body_id === 'completed:13';
        })""")
    full = page.evaluate("""() => {
        const rect = element => element.getBoundingClientRect();
        const first = document.querySelector('[data-conversation-tool="tool-5"]');
        const second = document.querySelector('[data-conversation-tool="tool-6"]');
        const thread = document.querySelector('.chat-thread-content');
        const a = document.querySelector('[data-conversation-view-key="e2"] .chat-a');
        const b = document.querySelector('[data-conversation-view-key="e4"] .chat-a');
        const streaming = document.querySelector('[data-conversation-row="streaming"]');
        const prompt = document.querySelector('[data-conversation-view-key="e1"] .chat-u');
        const ending = document.querySelector('[data-conversation-view-key="e8"]');
        const last = document.querySelector('[data-conversation-view-key="e11"] .chat-a');
        const probe = document.createElement('span');
        probe.style.color = 'var(--text-muted)';
        streaming.append(probe);
        const muted = getComputedStyle(probe).color;
        probe.remove();
        let rule = false;
        for(let node = first.parentElement; node && node !== thread; node=node.parentElement)
            rule ||= parseFloat(getComputedStyle(node).borderInlineStartWidth) > 0;
        return {
            toolIndent: rect(first).left - rect(thread).left,
            toolGap: rect(second).top - rect(first).bottom,
            toolRule: rule,
            firstTop: rect(prompt).top - rect(thread).top,
            groupBeforeGap: rect(first).top - rect(b).bottom,
            groupAfterGap: rect(ending).top - rect(second).bottom,
            streamingGap: rect(streaming).top - rect(last).bottom,
            proseGap: rect(b).top - rect(a).bottom,
            threadGap: parseFloat(getComputedStyle(thread).rowGap),
            emptyHeights: ['e0', 'e3', 'e7', 'e12', 'e13', 'work:5'].map(key => {
                const node = document.querySelector(`[data-conversation-view-key="${key}"]`);
                return node ? rect(node).height : 0;
            }),
            streamingColor: getComputedStyle(streaming).color, muted,
            visibleEndings: document.querySelectorAll('[data-conversation-row="turn_ended"]').length
        };
    }""")
    proof = {"focus": focus, "full": full}
    print("PROJECTED_VISUAL_PROOF " + json.dumps(proof), flush=True)
    evidence = os.environ.get("PANELS_CONVERSATION_PARITY_EVIDENCE")
    if evidence:
        output = Path(evidence)
        output.mkdir(parents=True, exist_ok=True)
        (output / "visual-geometry.json").write_text(json.dumps(proof, indent=2))
        page.screenshot(path=str(output / "projected-full.png"))
    failures = []
    if not any(row["separator"] not in ("none", "normal") and row["margin"] > 0 for row in focus["decorations"]):
        failures.append("Focus messages lost their separator")
    if any(row["separator"] not in ("none", "normal") for row in focus["first"]):
        failures.append("A separator appeared across the owner prompt")
    if not full["toolRule"] or full["toolIndent"] <= 0 or abs(full["toolGap"] - 4) > 1:
        failures.append("Full tools lost their run layout")
    if any(full["emptyHeights"]) or abs(full["proseGap"] - full["threadGap"]) > 1:
        failures.append("Invisible bookkeeping rows consume transcript space")
    if abs(full["firstTop"]) > 1 or any(abs(full[key] - full["threadGap"]) > 1 for key in ("groupBeforeGap", "groupAfterGap", "streamingGap")):
        failures.append("Leading, trailing, or group markers alter transcript spacing")
    if full["streamingColor"] != full["muted"]:
        failures.append("Streaming prose lost its muted color")
    if full["visibleEndings"] != 3:
        failures.append("Failed/interrupted/compaction-warning endings disappeared")
    page.close()
    assert not failures, {"failures": failures, "proof": proof}


def _conversation_repairs(browser, url):
    """Native pointer origins and textarea caret layout need a real browser, not a backend."""
    import json
    import os
    from pathlib import Path
    from urllib.parse import unquote

    evidence = os.environ.get("PANELS_CONVERSATION_PARITY_EVIDENCE")
    output = Path(evidence) if evidence else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    proof = {"gestures": [], "caret": [], "cap": [], "projected": {}}
    pane_selector = "[data-conversation-pane]"
    input_selector = "[data-conversation-input]"
    def frames(page):
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve))))")
    def state(page, value):
        page.wait_for_selector(f'{pane_selector}[data-conversation-state="{value}"]')
        frames(page)
        # The settled design gives card state travel 150ms. Measure pointer origins
        # after that travel, not at the third intermediate frame.
        page.wait_for_function("[...document.querySelectorAll('.conversation-layer')].every(node=>!node.style.height)")
        frames(page)
    for route in ("manager", "ticket"):
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors = []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        page.goto(url + ("?ticket=1" if route == "ticket" else ""), wait_until="domcontentloaded")
        state(page, "rest")
        page.locator(input_selector).fill("selection survives dismissal")
        for initial in ("peeked", "opened"):
            for outside in ("document", "layer"):
                page.evaluate("value => window.__setConversationState(value)", initial)
                state(page, initial)
                thread = page.locator("[data-conversation-thread]")
                paragraph = thread.locator('[data-conversation-row="agent_message"] p').last
                paragraph.scroll_into_view_if_needed()
                box = paragraph.bounding_box()
                layer = page.locator(".conversation-layer").bounding_box()
                outside_box = page.locator("[data-ticket-behind]").bounding_box()
                assert box and layer and outside_box
                end = {"x": outside_box["x"] + 10, "y": outside_box["y"] + 10} if outside == "document" else {"x": layer["x"] + 2, "y": box["y"] + 8}
                page.mouse.move(box["x"] + 40, box["y"] + 8)
                page.mouse.down()
                page.mouse.move(end["x"], end["y"], steps=12)
                page.mouse.up()
                state(page, initial)
                selection = page.evaluate("() => getSelection()?.toString() ?? ''")
                assert len(selection) > 0, (route, initial, outside, "gesture did not select text")
                assert page.locator(input_selector).input_value() == "selection survives dismissal"
                # The secondary button cannot acquire primary dismissal behavior.
                page.mouse.click(end["x"], end["y"], button="right")
                state(page, initial)
                page.mouse.click(end["x"], end["y"])
                state(page, "rest")
                proof["gestures"].append({"route": route, "state": initial, "outside": outside, "selectionLength": len(selection), "secondaryPreserved": True, "primaryDismissed": True})
        page.close()
        assert not errors, errors

    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    page.goto(url, wait_until="domcontentloaded")
    box = page.locator(input_selector)
    six_lines = "\n".join("abcdefghij line " + str(index) for index in range(6))
    box.fill(six_lines)
    for initial in ("rest", "peeked", "opened"):
        for line in (0, 2, 5):
            box.evaluate("e => {e.blur(); e.setSelectionRange(e.value.length,e.value.length); e.scrollTop=0;}")
            page.evaluate("value => window.__setConversationState(value)", initial)
            state(page, initial)
            before = box.evaluate("""(e, line) => {
              const r=e.getBoundingClientRect(), s=getComputedStyle(e);
              const canvas=document.createElement('canvas'), c=canvas.getContext('2d'); c.font=s.font;
              const x=r.left+parseFloat(s.borderLeftWidth)+parseFloat(s.paddingLeft)+c.measureText('abcd').width;
              const y=r.top+parseFloat(s.borderTopWidth)+parseFloat(s.paddingTop)+(line+.5)*parseFloat(s.lineHeight)-e.scrollTop;
              return {x,y,top:r.top,height:r.height,lineHeight:parseFloat(s.lineHeight)};
            }""", line)
            page.mouse.click(before["x"], before["y"])
            frames(page)
            after = box.evaluate("""e => {
              const r=e.getBoundingClientRect(),s=getComputedStyle(e),prefix=e.value.slice(0,e.selectionStart);
              const lines=prefix.split('\\n'), line=lines.length-1;
              return {start:e.selectionStart,end:e.selectionEnd,top:r.top,height:r.height,
                caretY:r.top+parseFloat(s.borderTopWidth)+parseFloat(s.paddingTop)+(line+.5)*parseFloat(s.lineHeight)-e.scrollTop,
                scroll:e.scrollTop,state:e.closest('[data-conversation-pane]').dataset.conversationState};
            }""")
            expected = sum(len(part)+1 for part in six_lines.split("\n")[:line]) + 4
            assert after["start"] == after["end"] == expected, (initial, line, before, after, expected)
            assert abs(after["top"]-before["top"]) <= 1 and abs(after["height"]-before["height"]) <= 1, (before, after)
            assert abs(after["caretY"]-before["y"]) <= 1, (before, after)
            assert after["top"] <= after["caretY"] <= after["top"]+after["height"], after
            proof["caret"].append({"initialState":initial,"clickedLine":line+1,"expectedOffset":expected,"before":before,"after":after})
    long_draft = "\n".join("Long draft line " + str(index) for index in range(30))
    box.fill(long_draft)
    for initial in ("rest", "peeked", "opened"):
        box.evaluate("e => e.blur()")
        page.evaluate("value => window.__setConversationState(value)", initial)
        state(page, initial)
        before = box.evaluate("""e => { const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {height:r.height,lineHeight:parseFloat(s.lineHeight),inset:["paddingTop","paddingBottom","borderTopWidth","borderBottomWidth"].reduce((sum,key)=>sum+parseFloat(s[key]),0),overflow:s.overflowY,scrollHeight:e.scrollHeight,clientHeight:e.clientHeight}; }""")
        assert abs(before["height"]-before["inset"]-6*before["lineHeight"]) <= 1, before
        assert before["overflow"] == "auto" and before["scrollHeight"] > before["clientHeight"], before
        box.click(position={"x":10,"y":10})
        frames(page)
        after_height = box.evaluate("e => e.getBoundingClientRect().height")
        assert abs(after_height-before["height"]) <= 1, (initial, before, after_height)
        assert box.input_value() == long_draft
        proof["cap"].append({"initialState":initial,**before,"afterFocusHeight":after_height})
    if output:
        page.screenshot(path=str(output / "six-line-composer.png"))
    page.close()

    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    page.goto(url+"?reader=1&folds=1", wait_until="domcontentloaded")
    for text in ("Owner answer one.", "Owner answer two.", "Owner answer three.", "Owner answer before failure."):
        answer = page.get_by_text(text, exact=True)
        answer.wait_for()
        answer.scroll_into_view_if_needed()
        assert answer.is_visible()
    assert page.locator("[data-conversation-turn-settled-head]").count() == 2
    assert page.locator("[data-conversation-turn-fold], .c2-turn-chevron, [data-conversation-work-fold]").count() == 0
    assert page.locator('[data-conversation-row="tool_call"]').count() == 0
    page.get_by_text("Visible fold failure", exact=True).wait_for()
    page.get_by_text("turn stopped without an ending", exact=True).wait_for()
    proof["projected"]["focus"]={"ownerMessages":4,"settledHeads":2,"foldControls":0,"failureAndStop":True}
    if output:
        page.screenshot(path=str(output / "owner-messages-focus.png"))
    page.locator('[data-conversation-lens-choice="full"]').click()
    for text in ("Ordinary commentary one.", "Ordinary commentary two.", "Ordinary commentary three."):
        page.get_by_text(text, exact=True).wait_for()
    for text in ("Owner answer one.", "Owner answer two.", "Owner answer three.", "Owner answer before failure."):
        assert page.get_by_text(text, exact=True).count() == 1
    for key in ("fold-tool-6", "fold-tool-7"):
        page.locator(f'[data-conversation-tool="{key}"]').wait_for()
    assert page.locator("[data-conversation-turn-fold], [data-conversation-work-fold]").count() == 0
    calls=page.evaluate("window.__viewCalls")
    assert any('owner:3' in unquote(call) for call in calls), calls
    proof["projected"]["full"]={"ordinaryMessages":3,"tools":2,"foldControls":0,"deferredOwnerRead":True}
    if output:
        page.screenshot(path=str(output / "owner-messages-full.png"))
        (output / "conversation-repair-proof.json").write_text(json.dumps(proof,indent=2))
    print("CONVERSATION_REPAIR_PROOF " + json.dumps(proof), flush=True)
    page.close()


def _manager_recap_header(browser, url):
    """The real header must remeasure its recap after its typography changes."""
    import time

    from playwright.sync_api import expect

    started = time.perf_counter()
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    try:
        page.goto(url + "?manager-layout=1", wait_until="domcontentloaded")
        recap = page.get_by_role("textbox", name="Manager recap", exact=True)
        toggle = page.locator("[data-manager-recap-toggle]")
        expect(recap).to_have_text("The recap belongs below the title.")
        assert page.evaluate("window.__layoutHeaderMatches()")
        page.evaluate("window.__layoutUpdate('A long recap paragraph with enough text to span several measured lines. '.repeat(30))")
        expect(toggle).to_be_visible()
        toggle.click()
        expect(toggle).to_have_text("Show less")
        page.evaluate("window.__layoutUpdate('')")
        expect(recap).to_be_empty()
        expect(recap).to_have_attribute("data-ph", "Write a concise recap for this manager…")
        expect(toggle).to_have_count(0)
        assert page.evaluate("window.__layoutHeaderMatches()")
    finally:
        page.close()
        print(f"Manager recap header browser scenario: {time.perf_counter() - started:.3f}s", flush=True)


def _overlay_menu_bounds(browser, url):
    """Overlay scroll bounds must leave every existing menu action operable."""
    import json
    import os
    import re
    import time
    from pathlib import Path

    from playwright.sync_api import expect

    started = time.perf_counter()
    output = Path(os.environ.get("PANELS_MENU_ARTIFACT_DIR", os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR", "/tmp/panels-menu-proof")))
    output.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    page.add_init_script("""(() => {
      const viewport = new EventTarget();
      Object.assign(viewport, {offsetTop:0,offsetLeft:0,width:1100,height:900});
      Object.defineProperty(window,'visualViewport',{value:viewport,configurable:true});
      window.__keyboardBounds=(top,height)=>{Object.assign(viewport,{offsetTop:top,height});viewport.dispatchEvent(new Event('resize'));viewport.dispatchEvent(new Event('scroll'));};
    })()""")
    proof = {"nativeKeyboard":"Not tested: visualViewport reduction and displacement are simulated.", "states":[], "actions":[]}
    def settle():
        _settle_conversation_layout(page)
    def hit(item):
        item.scroll_into_view_if_needed()
        settle()
        bounds = item.bounding_box()
        assert bounds, item
        x,y=bounds["x"]+bounds["width"]/2,bounds["y"]+bounds["height"]/2
        assert item.evaluate("(el,p)=>el.contains(document.elementFromPoint(p.x,p.y))", {"x":x,"y":y}), (item.get_attribute("class"), bounds)
        viewport = page.evaluate("({top:visualViewport.offsetTop,bottom:visualViewport.offsetTop+visualViewport.height})")
        assert viewport["top"] <= y <= viewport["bottom"], (bounds,viewport)
        return x,y
    def keyboard_rail(item, key):
        picker.get_by_role('listbox').press('ArrowLeft')
        page.keyboard.press('Home')
        count=picker.locator('[data-listbox-picker-action]').count()
        for _ in range(count):
            if item.evaluate('el=>el===document.activeElement'):
                break
            page.keyboard.press('ArrowDown')
        expect(item).to_be_focused()
        item.press(key)
        settle()

    try:
        page.route('**/api/conversation/backends/refresh', lambda route: route.fulfill(status=503,json={'detail':'Fixture refresh boundary'}))
        page.goto(url, wait_until="domcontentloaded")
        page.evaluate("window.__setConversationState('opened')")
        input_box = page.locator('[data-conversation-input]')
        input_box.fill('/')
        settle()
        assert page.locator('[data-conversation-composer-constrained]').count()==0
        assert page.locator('.chat-menu').evaluate("el=>getComputedStyle(el).position")=='absolute'
        proof["ordinaryMenusFloating"]=True
        input_box.fill('')
        for mode,top,height in (("keyboard",120,340),("constrained",200,190)):
            page.evaluate("([top,height])=>window.__keyboardBounds(top,height)",[top,height])
            settle()
            input_box.fill('/')
            settle()
            snapshot=page.locator('[data-conversation-composer]').evaluate("""el=>({height:el.clientHeight,scrollHeight:el.scrollHeight,top:el.getBoundingClientRect().top,popupRoom:parseFloat(getComputedStyle(el).getPropertyValue('--conversation-popup-height')),target:parseFloat(getComputedStyle(el).getPropertyValue('--conversation-target')),gap:parseFloat(getComputedStyle(el).getPropertyValue('--space-6')),constrained:el.hasAttribute('data-conversation-composer-constrained'),viewportTop:visualViewport.offsetTop,viewportHeight:visualViewport.height})""")
            assert snapshot['constrained'] == (mode=='constrained'), snapshot
            assert page.locator('.chat-menu').evaluate('el=>getComputedStyle(el).position') == ('static' if mode=='constrained' else 'absolute')
            snapshot['mode']=mode
            proof['states'].append(snapshot)
            page.screenshot(path=str(output / (mode+'-catalog.png')))
            entries=page.locator('[data-conversation-catalog-entry]').evaluate_all("els=>els.map(el=>el.dataset.conversationCatalogEntry)")
            for method in ('pointer','keyboard'):
                for index,entry in enumerate(entries):
                    input_box.fill('')
                    input_box.fill('/')
                    settle()
                    item=page.locator('[data-conversation-catalog-entry]').nth(index)
                    if method=='pointer':
                        hit(item)
                        item.click()
                    else:
                        active = page.locator('[data-conversation-catalog-active]').get_attribute('data-conversation-catalog-entry')
                        for _ in range((index-entries.index(active)) % len(entries)):
                            input_box.press('ArrowDown')
                        input_box.press('Enter')
                    expect(input_box).to_have_value(entry+' ')
                    proof['actions'].append([mode,'catalog',method,entry])
            input_box.fill('')
            add=page.locator('[data-conversation-add]')
            for method in ('pointer','keyboard'):
                for attribute in ('image','file','camera','slash'):
                    input_box.fill('')
                    settle()
                    add.click()
                    settle()
                    item=page.locator('[data-conversation-'+attribute+']')
                    if method=='pointer':
                        hit(item)
                    else:
                        add.focus()
                        for _ in range(12):
                            if item.evaluate('el=>el===document.activeElement'):
                                break
                            page.keyboard.press('Tab')
                        expect(item).to_be_focused()
                    if attribute=='slash':
                        item.click() if method=='pointer' else item.press('Enter')
                        expect(input_box).to_have_value('/')
                        input_box.fill('')
                    else:
                        with page.expect_file_chooser() as chosen_file:
                            item.click() if method=='pointer' else item.press('Enter')
                        chosen_file.value.set_files([])
                        expect(page.locator('.chat-add-menu')).to_have_count(0)
                    proof['actions'].append([mode,'attachment',method,attribute])
            picker=page.locator('[data-conversation-model-picker]')
            trigger=picker.locator('[data-conversation-picker-trigger]')
            for method in ('pointer','keyboard'):
                for index,model in enumerate(('opus','sonnet')):
                    trigger.click()
                    settle()
                    item=picker.locator('[data-conversation-picker-choice="'+model+'"]')
                    if method=='pointer':
                        hit(item)
                        item.click()
                    else:
                        box=picker.get_by_role('listbox')
                        box.press('Home' if index==0 else 'End')
                        box.press('Enter')
                    if model=='opus':
                        effort=picker.locator('[data-conversation-picker-choice="high"]')
                        if method=='pointer':
                            hit(effort)
                            effort.click()
                        else:
                            picker.get_by_role('listbox').press('Home')
                            picker.get_by_role('listbox').press('Enter')
                        proof['actions'].append([mode,'effort',method,'high'])
                    expect(trigger).to_have_attribute('aria-label', re.compile(model,re.I))
                    proof['actions'].append([mode,'model',method,model])
            for method in ('pointer','keyboard'):
                # Opus offers reasoning; Sonnet deliberately has no effort choices.
                trigger.click()
                picker.locator('[data-conversation-picker-choice="opus"]').click()
                picker.locator('[data-conversation-picker-choice="high"]').click()
                trigger.click()
                for control in ('backend:claude','backend:codex','backend:hermes','reasoning','models','refresh'):
                    settle()
                    selector = '[data-conversation-backend="'+control.split(':')[1]+'"]' if control.startswith('backend:') else '[data-conversation-picker-'+control+']'
                    item=picker.locator(selector)
                    if method=='pointer':
                        x,y=hit(item)
                        page.mouse.click(x,y)
                    else:
                        keyboard_rail(item,'Space')
                    if control.startswith('backend:'):
                        expect(picker.locator('[data-conversation-picker-feedback]')).to_be_visible()
                    elif control=='reasoning':
                        expect(picker.locator('[data-conversation-picker-choice="high"]')).to_be_visible()
                    elif control=='models':
                        expect(picker.locator('[data-conversation-picker-choice="opus"]')).to_be_visible()
                    else:
                        expect(picker.locator('[data-conversation-picker-feedback]')).to_have_text('Fixture refresh boundary')
                    settle()
                    proof['actions'].append([mode,'runRail',method,control])
                page.screenshot(path=str(output / (mode+'-run-picker.png')))
                picker.get_by_role('listbox').press('Escape')
                expect(trigger).to_be_focused()
            # Models removes its own rail button. Enter and Space must both leave
            # the surviving list usable, then dismiss back to the stable trigger.
            for key in ('Enter','Space'):
                trigger.click()
                keyboard_rail(picker.locator('[data-conversation-picker-reasoning]'),key)
                keyboard_rail(picker.locator('[data-conversation-picker-models]'),key)
                box=picker.get_by_role('listbox')
                expect(box).to_be_focused()
                expect(box).to_have_attribute('aria-label','Models')
                box.press('Home')
                box.press('Enter')
                expect(picker.get_by_role('listbox')).to_have_attribute('aria-label','Reasoning efforts')
                picker.get_by_role('listbox').press('Enter')
                expect(trigger).to_have_attribute('aria-label',re.compile('opus high',re.I))
                trigger.click()
                keyboard_rail(picker.locator('[data-conversation-picker-reasoning]'),key)
                keyboard_rail(picker.locator('[data-conversation-picker-models]'),key)
                picker.get_by_role('listbox').press('Escape')
                expect(trigger).to_be_focused()
                proof['actions'].append([mode,'modelsFocus',key,'selection works; Escape restores trigger'])
        proof['elapsedSeconds']=round(time.perf_counter()-started,3)
        (output/'menu-proof.json').write_text(json.dumps(proof,indent=2))
        print('OVERLAY_MENU_PROOF '+json.dumps(proof),flush=True)
    finally:
        (output/'menu-proof-latest.json').write_text(json.dumps(proof,indent=2))
        page.close()


def _settled_motion(browser, url):
    import json
    import os
    import time
    from pathlib import Path

    started = time.perf_counter()
    output = Path(os.environ.get('PANELS_E2E_FAILURE_DIR', '/tmp'))
    output.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={'width':1000,'height':900}, reduced_motion='no-preference')
    try:
        page.goto(url)
        page.evaluate("window.__setupScrollRegression('plain')")
        thread = page.locator('[data-conversation-thread]')
        thread.wait_for()
        page.wait_for_timeout(350)
        before = thread.evaluate('node=>node.clientHeight')
        page.evaluate("""()=>{
          window.__growthFrames=[];window.__growthStop=false;
          const sample=()=>{window.__growthFrames.push(document.querySelector('[data-conversation-thread]').clientHeight);if(!window.__growthStop)requestAnimationFrame(sample);};
          requestAnimationFrame(sample);
        }""")
        page.locator('[data-conversation-input]').fill('One line\nSecond line\nThird line\nFourth line')
        page.wait_for_timeout(350)
        frames=page.evaluate('window.__growthStop=true;window.__growthFrames')
        assert len(frames)>3
        assert max(abs(value-before) for value in frames)<=2, {'before':before,'frames':frames}
        page.locator('[data-conversation-input]').fill('')
        page.wait_for_timeout(300)
        page.evaluate("""()=>{
          window.__sendFrames=[];window.__sendStop=false;
          const sample=()=>{const node=document.querySelector('[data-conversation-thread]');window.__sendFrames.push(node.scrollTop);if(!window.__sendStop)requestAnimationFrame(sample);};
          requestAnimationFrame(sample);window.__sendScrollRegression(false);
        }""")
        page.wait_for_timeout(450)
        send_frames=page.evaluate('window.__sendStop=true;window.__sendFrames')
        moving=[value for i,value in enumerate(send_frames) if i==0 or abs(value-send_frames[i-1])>.5]
        assert len(moving)>3, send_frames
        assert all(b>=a-1 for a,b in zip(send_frames,send_frames[1:],strict=False)), send_frames
        page.evaluate("window.__sentBubble=document.querySelector('[data-conversation-outgoing=\"scroll-send\"]')")
        page.evaluate('window.__answerScrollRegression(1)')
        page.wait_for_timeout(350)
        assert page.evaluate("window.__sentBubble===document.querySelector('[data-conversation-row=\"prompt\"]')"), 'Receipt replaced the visible bubble'
        assert page.locator('.c2-chip').count()==0
        page.screenshot(path=str(output/'settled-motion.png'))
        page.evaluate('window.__showUnansweredSend()')
        bubble=page.locator('[data-conversation-outgoing="sent-into-the-dark"]')
        bubble.wait_for()
        page.wait_for_timeout(300)
        page.screenshot(path=str(output/'settled-unanswered.png'))
        page.evaluate("""()=>{
          const bubble=document.querySelector('[data-conversation-outgoing="sent-into-the-dark"]');
          const row=bubble.closest('[data-conversation-view-key]')??bubble;
          window.__exitFrames=[];
          const sample=()=>{window.__exitFrames.push({opacity:+getComputedStyle(bubble).opacity * +getComputedStyle(row).opacity,height:row.getBoundingClientRect().height});if(bubble.isConnected)requestAnimationFrame(sample);};
          requestAnimationFrame(sample);
        }""")
        bubble.get_by_role('button',name='Stop showing this message',exact=True).click()
        bubble.wait_for(state='detached')
        exit_frames=page.evaluate('window.__exitFrames')
        height=exit_frames[0]['height']
        assert any(row['opacity']<.9 and abs(row['height']-height)<1 for row in exit_frames),exit_frames
        assert any(row['opacity']<.05 and 1<row['height']<height-1 for row in exit_frames),exit_frames
        assert all(b['height']<=a['height']+1 for a,b in zip(exit_frames,exit_frames[1:],strict=False)),exit_frames
        faded=next(i for i,row in enumerate(exit_frames) if row['opacity']<.05)
        assert all(row['opacity']<.05 for row in exit_frames[faded:] if row['height']>0),exit_frames
        receipt={'draftBaseline':before,'draftFrames':frames,'sendFrames':send_frames,'exitFrames':exit_frames,'sameBubble':True,'seconds':round(time.perf_counter()-started,3),'newHosts':0}
        (output/'settled-motion-proof.json').write_text(json.dumps(receipt,indent=2))
        print('SETTLED_MOTION_PROOF '+json.dumps(receipt),flush=True)
    finally:
        page.close()


def _settled_unread_entry(browser, url):
    import json
    import os
    import time
    from pathlib import Path

    started = time.perf_counter()
    output = Path(os.environ.get("PANELS_E2E_FAILURE_DIR", "/tmp"))
    output.mkdir(parents=True, exist_ok=True)
    proof = []
    page = browser.new_page(viewport={"width": 1000, "height": 900}, reduced_motion="reduce")
    try:
        page.goto(url + "?reader=1&unread=1500&entry-hold=1")
        page.wait_for_function("window.__entryProbe.waiting > 0")
        region = page.locator('.conversation-transcript-region')
        assert region.get_attribute('aria-busy') == 'true'
        assert not page.locator('[data-conversation-view-key="e2000"]').is_visible()
        page.evaluate("window.__entryProbe.hold=false;window.__entryProbe.release()")
        page.wait_for_function("document.querySelector('[data-conversation-entry]')?.dataset.conversationEntry==='complete'")
        first = page.locator('[data-conversation-view-key="e1501"]')
        first.wait_for(state='visible')
        gap = first.evaluate("node=>node.getBoundingClientRect().top-document.querySelector('[data-conversation-thread]').getBoundingClientRect().top")
        assert abs(gap-32) < 2, gap
        assert page.locator('[data-conversation-view-key]').count() <= 300
        page.screenshot(path=str(output / 'settled-unread.png'))
        proof.append({'case':'older unread delayed admission','topGap':gap,'newestFlash':False})

        page.goto(url + "?reader=1&unread=1500&entry-fail=1")
        retry = page.get_by_role('button', name='Retry unread messages', exact=True)
        retry.wait_for(state='visible')
        assert page.locator('[data-conversation-entry-status]').is_visible()
        assert region.get_attribute('aria-busy') == 'false'
        page.screenshot(path=str(output / 'settled-unread-error.png'))
        page.evaluate("window.__entryProbe.fail=false")
        retry.click()
        page.wait_for_function("document.querySelector('[data-conversation-entry]')?.dataset.conversationEntry==='complete'")
        page.locator('[data-conversation-view-key="e1501"]').wait_for(state='visible')
        assert not retry.is_visible()
        proof.append({'case':'admission failure','visibleRetry':True,'retryPreservesTarget':True})

        page.goto(url + "?reader=1&unread=1500&entry-hidden=1")
        page.wait_for_function("document.querySelector('[data-conversation-entry]')?.dataset.conversationEntry==='complete'")
        first = page.locator('[data-conversation-view-key="e1601"]')
        first.wait_for(state='visible')
        gap = first.evaluate("node=>node.getBoundingClientRect().top-document.querySelector('[data-conversation-thread]').getBoundingClientRect().top")
        assert abs(gap-32) < 2, gap
        proof.append({'case':'hidden-only first page','firstVisible':'e1601','topGap':gap})
        page.goto(url + "?reader=1&reader-peek=1&unread=1999")
        page.wait_for_function("document.querySelector('[data-conversation-entry]')?.dataset.conversationEntry==='complete'")
        first = page.locator('[data-conversation-view-key="e2000"]')
        gap_before = first.evaluate("node=>node.getBoundingClientRect().top-document.querySelector('[data-conversation-thread]').getBoundingClientRect().top")
        assert abs(gap_before-32)<2, gap_before
        page.evaluate("window.__readerState('opened')")
        page.wait_for_timeout(300)
        gap_after = first.evaluate("node=>node.getBoundingClientRect().top-document.querySelector('[data-conversation-thread]').getBoundingClientRect().top")
        assert abs(gap_after-32)<2, gap_after
        proof.append({'case':'short unread tail expansion','peekTop':gap_before,'openedTop':gap_after})
        receipt={'cases':proof,'seconds':round(time.perf_counter()-started,3),'newHosts':0}
        (output / 'settled-unread-proof.json').write_text(json.dumps(receipt,indent=2))
        print('SETTLED_UNREAD_PROOF '+json.dumps(receipt),flush=True)
    finally:
        page.close()



def _position_commit(browser, url):
    """Probe the render/placement boundary on existing real Svelte hosts."""
    import json
    import os
    import time
    from pathlib import Path

    started=time.perf_counter()
    output=Path(os.environ.get('PANELS_E2E_FAILURE_DIR','/tmp'))
    output.mkdir(parents=True,exist_ok=True)
    page=browser.new_page(viewport={'width':1000,'height':900},reduced_motion='no-preference',record_video_dir=str(output/'position-video'),record_video_size={'width':800,'height':720})
    proof={'entry':[],'contraction':[],'labels':[]}
    page.add_init_script("""window.__positionFrames=[];window.__samplePosition=false;
      const sample=()=>{
        const thread=document.querySelector('[data-conversation-thread]');
        const region=document.querySelector('.conversation-transcript-region');
        if(thread&&window.__samplePosition){
          const last=[...thread.querySelectorAll('[data-conversation-row]')].at(-1);
          const hidden=getComputedStyle(thread).visibility==='hidden'||getComputedStyle(region).visibility==='hidden';
          window.__positionFrames.push({ms:performance.now(),hidden,busy:region?.getAttribute('aria-busy'),
            top:thread.scrollTop,height:thread.clientHeight,scrollHeight:thread.scrollHeight,
            gap:last?thread.getBoundingClientRect().bottom-last.getBoundingClientRect().bottom:null});
        }requestAnimationFrame(sample);
      };requestAnimationFrame(sample);""")
    try:
        # Readiness must survive bodies completing after the initial nonzero layout.
        for attempt in range(2):
            page.goto(url+'?reader=1&position-race=1')
            page.wait_for_function('window.__positionProbe.waiting>0')
            page.evaluate('window.__positionFrames=[];window.__samplePosition=true')
            assert page.locator('.conversation-transcript-region').get_attribute('aria-busy')=='true'
            page.evaluate('window.__positionProbe.release()')
            page.locator('[data-conversation-entry="complete"]').wait_for()
            page.wait_for_timeout(350)
            frames=page.evaluate('window.__samplePosition=false;window.__positionFrames')
            visible=[row for row in frames if not row['hidden']]
            assert visible and all(abs(row['scrollHeight']-row['height']-row['top'])<=1.5 for row in visible), visible
            ready=[row for row in visible if row['busy']!='true']
            assert ready and all(-2<=row['gap']<=18 for row in ready), ready
            proof['entry'].append({'trigger':'fresh open','attempt':attempt,'frames':frames})
            for lens in ('full','focus'):
                page.evaluate('window.__positionFrames=[];window.__samplePosition=true')
                page.locator('[data-conversation-lens-choice="'+lens+'"]').click()
                page.wait_for_timeout(500)
                frames=page.evaluate('window.__samplePosition=false;window.__positionFrames')
                visible=[row for row in frames if not row['hidden']]
                assert visible and all(abs(row['scrollHeight']-row['height']-row['top'])<=1.5 for row in visible), (lens,visible)
                ready=[row for row in visible if row['busy']!='true']
                assert ready and all(-2<=row['gap']<=18 for row in ready), (lens,ready)
                proof['entry'].append({'trigger':'lens '+lens,'attempt':attempt,'frames':frames})
            # Browser-owned image and font metrics change after reveal. Neither causes travel.
            frames=page.evaluate("""()=>new Promise(resolve=>{
              const thread=document.querySelector('[data-conversation-thread]');
              const row=thread.querySelector('[data-conversation-row]');
              const image=document.createElement('img');image.style.cssText='display:block;width:1px;height:220px';
              row.append(image);row.style.fontSize='22px';
              const frames=[];let count=0;const sample=()=>{const last=[...thread.querySelectorAll('[data-conversation-row]')].at(-1);frames.push({gap:thread.getBoundingClientRect().bottom-last.getBoundingClientRect().bottom,top:thread.scrollTop});if(++count<8)requestAnimationFrame(sample);else resolve(frames);};requestAnimationFrame(sample);
            })""")
            assert all(-2<=row['gap']<=18 for row in frames), frames
            proof['entry'].append({'trigger':'late image/font metrics','frames':frames})
        page.screenshot(path=str(output/'position-entry.png'))

        # The label counts unread source messages, while visibility still follows newest.
        page.goto(url)
        assert page.locator('[data-conversation-input]').get_attribute('placeholder')=='Message the agent...'
        page.evaluate("window.__fixturePlaceholder('Write a custom message')")
        page.wait_for_function("document.querySelector('[data-conversation-input]')?.placeholder==='Write a custom message'")
        page.evaluate('window.__fixturePlaceholder(undefined)')
        page.evaluate("window.__setupScrollRegression('plain');window.__appendLabelTools();window.__setReadWatermark(28)")
        _settle_conversation_layout(page)
        thread=page.locator('[data-conversation-thread]')
        thread.hover()
        page.mouse.wheel(0,-600)
        latest=page.get_by_role('button',name='Jump to latest message',exact=True)
        latest.wait_for()
        assert latest.inner_text()=='Latest'
        proof['labels'].append({'watermark':28,'text':latest.inner_text()})
        page.evaluate('window.__setReadWatermark(24)')
        page.wait_for_function("document.querySelector('.chat-jump')?.textContent==='4 new messages'")
        proof['labels'].append({'watermark':24,'text':latest.inner_text()})
        latest.click()
        _settle_conversation_layout(page)

        # Card shrink keeps content fixed relative to its bottom on every shared frame.
        for trigger in ('control','Escape'):
            page.locator('[data-conversation-expand]').click()
            _settle_conversation_layout(page)
            page.evaluate("""()=>{window.__contractFrames=[];window.__contractStop=false;
              const thread=document.querySelector('[data-conversation-thread]');
              const last=[...thread.querySelectorAll('[data-conversation-row]')].at(-1);
              const start=performance.now();const sample=()=>{const box=thread.getBoundingClientRect();window.__contractFrames.push({ms:performance.now()-start,bottom:box.bottom,height:box.height,contentBottom:last.getBoundingClientRect().bottom-box.bottom});if(!window.__contractStop)requestAnimationFrame(sample);};sample();}""")
            if trigger=='control':
                page.locator('[data-conversation-collapse]').click()
            else:
                page.locator('[data-conversation-input]').focus()
                page.keyboard.press('Escape')
            page.wait_for_timeout(380)
            frames=page.evaluate('window.__contractStop=true;window.__contractFrames')
            assert len({round(row['height']) for row in frames})>3,frames
            assert max(row['bottom'] for row in frames)-min(row['bottom'] for row in frames)<=2,frames
            assert max(row['contentBottom'] for row in frames)-min(row['contentBottom'] for row in frames)<=3,frames
            proof['contraction'].append({'trigger':trigger,'frames':frames})
        # The existing send fixture proves the held message remains 32px below the top.
        page.goto(url)
        page.evaluate("window.__setupScrollRegression('plain')")
        _settle_conversation_layout(page)
        page.evaluate('window.__sendScrollRegression(true)')
        _settle_conversation_layout(page)
        def sent_gap():
            return page.locator('[data-conversation-thread]').evaluate("""thread=>{const row=thread.querySelector('[data-conversation-outgoing], [data-conversation-row="prompt"]');return row.getBoundingClientRect().top-thread.getBoundingClientRect().top;}""")
        held_gap=sent_gap()
        assert abs(held_gap-32)<=2,held_gap
        page.evaluate('window.__answerScrollRegression(1)')
        _settle_conversation_layout(page)
        answered_gap=sent_gap()
        assert abs(answered_gap-32)<=2,answered_gap
        proof['sentHold']={'beforeAnswer':held_gap,'afterAnswer':answered_gap}
        # A real live-output body version change retains multiline 120ms line cadence.
        page.goto(url+'?reader=1&live-stream=1')
        page.locator('[data-conversation-entry="complete"]').wait_for()
        page.locator('[data-conversation-lens-choice="full"]').click()
        page.locator('.c2-streaming').wait_for()
        _settle_conversation_layout(page)
        page.evaluate("""()=>{window.__presentationEvents=[];document.addEventListener('conversation-text-presentation',event=>{window.__presentationEvents.push({...event.detail,top:document.querySelector('[data-conversation-thread]')?.scrollTop});});}""")
        frames=page.evaluate(r"""()=>new Promise(resolve=>{
          const thread=document.querySelector('[data-conversation-thread]');
          const before=thread.scrollHeight,start=performance.now(),frames=[];
          window.__streamProbe.append('\n\n'+Array.from({length:5},(_,i)=>'New streamed line '+i+'. The new text alone permits the line cadence to continue.').join('\n\n'));
          const sample=()=>{frames.push({ms:performance.now()-start,top:thread.scrollTop,growth:thread.scrollHeight-before});if(performance.now()-start<1800)requestAnimationFrame(sample);else resolve(frames);};sample();
        })""")
        moving=[b for a,b in zip(frames,frames[1:],strict=False) if b['top']-a['top']>.5]
        # Linear per-line motion exposes its duration as line height / screen speed.
        # This measures the written pixels, rather than a duration constant or call.
        import statistics
        line_height=page.locator('[data-conversation-content]').evaluate('node=>parseFloat(getComputedStyle(node.lastElementChild).lineHeight)')
        speeds=[(b['top']-a['top'])/(b['ms']-a['ms']) for a,b in zip(frames,frames[1:],strict=False)
          if b['top']-a['top']>1 and 8 < b['ms']-a['ms'] < 30]
        observed_line_ms=line_height/statistics.median(speeds)
        assert 100 <= observed_line_ms <= 145, {'lineHeight':line_height,'observedLineMs':observed_line_ms,'frames':frames}
        proof['streaming']={'frames':frames,'events':page.evaluate('window.__presentationEvents')}
        assert len(moving)>3 and moving[-1]['ms']>300,proof['streaming']
        assert frames[-1]['top']-frames[0]['top']<=frames[-1]['growth']+2,frames
        proof['streaming']={'frames':frames,'arrivalHeight':frames[-1]['growth'],'observedLineMs':observed_line_ms,'lineHeight':line_height,'events':page.evaluate('window.__presentationEvents')}
        proof['seconds']=round(time.perf_counter()-started,3)
        proof['newHosts']=0
        (output/'position-commit-proof.json').write_text(json.dumps(proof,indent=2))
        print('POSITION_COMMIT_PROOF '+json.dumps({'seconds':proof['seconds'],'entries':len(proof['entry']),'contractions':2}),flush=True)
    finally:
        proof['seconds']=round(time.perf_counter()-started,3)
        (output/'position-commit-latest.json').write_text(json.dumps(proof,indent=2))
        page.close()

def _tab_return(browser, url):
    """A remembered row and its pixel offset survive hidden backlog admission."""
    import json
    import os
    import time
    from pathlib import Path

    started = time.perf_counter()
    output = Path(os.environ.get('PANELS_E2E_FAILURE_DIR', '/tmp'))
    output.mkdir(parents=True, exist_ok=True)
    proof = []
    page = browser.new_page(viewport={'width': 1000, 'height': 850}, reduced_motion='no-preference')
    reading = """() => {
      const thread=document.querySelector('[data-conversation-thread]'), top=thread.getBoundingClientRect().top;
      const row=[...thread.querySelectorAll('[data-conversation-view-key]')].find(row=>row.getBoundingClientRect().bottom>top+1);
      return {key:row?.dataset.conversationViewKey,offset:row?row.getBoundingClientRect().top-top:null,scroll:thread.scrollTop};
    }"""
    hidden = """() => {
      window.__fixtureVisible=false;
      Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>window.__fixtureVisible?'visible':'hidden'});
      document.dispatchEvent(new Event('visibilitychange'));
    }"""
    sample_return = """held => {
      window.__returnFrames=[];window.__stopReturnFrames=false;
      const sample=()=>{
        const thread=document.querySelector('[data-conversation-thread]');
        const region=document.querySelector('.conversation-transcript-region');
        const row=[...thread.querySelectorAll('[data-conversation-view-key]')].find(row=>row.dataset.conversationViewKey===held.key);
        const visible=getComputedStyle(thread).visibility!=='hidden'&&getComputedStyle(region).visibility!=='hidden';
        window.__returnFrames.push({ms:performance.now(),visible,scroll:thread.scrollTop,
          offset:row?row.getBoundingClientRect().top-thread.getBoundingClientRect().top:null});
        if(!window.__stopReturnFrames)requestAnimationFrame(sample);
      };
      window.__fixtureVisible=true;requestAnimationFrame(sample);document.dispatchEvent(new Event('visibilitychange'));
    }"""

    def assert_frames(held, frames):
        visible = [frame for frame in frames if frame['visible']]
        assert visible, frames
        assert all(frame['offset'] is not None and abs(frame['offset']-held['offset']) <= 1 for frame in visible), (held, visible)

    try:
        for position, backlog, admission in (
            ('scrollback', 0, 'immediate'), ('latest', 0, 'immediate'),
            ('scrollback', 120, 'immediate'), ('latest', 120, 'immediate'),
            ('scrollback', 120, 'delayed'), ('scrollback', 120, 'failed'),
            ('scrollback', 120, 'bodies'),
        ):
            page.goto(url+'?reader=1')
            page.locator('[data-conversation-entry="complete"]').wait_for()
            _settle_conversation_layout(page)
            if position == 'scrollback':
                page.locator('[data-conversation-thread]').hover()
                page.mouse.wheel(0, -750)
                page.wait_for_timeout(300)
            held = page.evaluate(reading)
            assert held['key'] and held['scroll'] > 0, held
            page.evaluate(hidden)
            page.wait_for_timeout(100)
            calls_before = page.evaluate('window.__viewCalls.filter(call=>call.includes("/view/pages")).length')
            page.evaluate('(count)=>window.__backlog(count)', backlog)
            page.evaluate('(mode)=>{window.__returnProbe.hold=mode==="delayed";window.__returnProbe.fail=mode==="failed";window.__returnProbe.bodies=mode==="bodies";window.__returnProbe.bodyHold=mode==="bodies";}', admission)
            page.wait_for_timeout(100)
            assert page.evaluate('window.__viewCalls.filter(call=>call.includes("/view/pages")).length') == calls_before
            page.evaluate(sample_return, held)
            if admission == 'delayed':
                page.wait_for_function('window.__returnProbe.waiting>0')
                page.wait_for_timeout(250)
                waiting = page.evaluate('window.__returnFrames')
                # Any retained content that the user can see must already be correct.
                visible = [frame for frame in waiting if frame['visible']]
                if visible:
                    assert_frames(held, visible)
                page.evaluate('window.__returnProbe.hold=false;window.__returnProbe.release()')
            if admission == 'bodies':
                page.wait_for_function('window.__returnProbe.bodyWaiting>0')
                page.wait_for_timeout(250)
                waiting=page.evaluate('window.__returnFrames')
                visible=[frame for frame in waiting if frame['visible']]
                if visible:
                    assert_frames(held,visible)
                page.evaluate('window.__returnProbe.releaseBodies()')
            if admission == 'failed':
                page.wait_for_timeout(350)
                # Explicit retry retains the same remembered target after a failed read.
                retry = page.get_by_role('button', name='Retry conversation position', exact=True)
                retry.wait_for(state='visible')
                failed = page.evaluate('window.__returnFrames')
                visible = [frame for frame in failed if frame['visible']]
                if visible:
                    assert_frames(held, visible)
                page.evaluate('window.__returnProbe.fail=false')
                retry.click()
            page.wait_for_timeout(1000)
            frames = page.evaluate('window.__stopReturnFrames=true;window.__returnFrames')
            assert_frames(held, frames)
            after = page.evaluate(reading)
            assert after['key'] == held['key'] and abs(after['offset']-held['offset']) <= 1, (held, after)
            assert page.locator('[data-conversation-view-key]').count() <= 300
            page.screenshot(path=str(output / f'tab-return-{position}-{backlog}-{admission}.png'))
            proof.append({'position':position,'backlog':backlog,'admission':admission,'held':held,'after':after,'frames':frames})
        # A hidden transition can interrupt a visible 240ms Latest move between frames.
        page.goto(url+'?reader=1')
        page.locator('[data-conversation-entry="complete"]').wait_for()
        _settle_conversation_layout(page)
        page.locator('[data-conversation-thread]').hover()
        page.mouse.wheel(0,-750)
        page.wait_for_timeout(300)
        before_motion=page.evaluate(reading)
        page.evaluate("""before => {
          window.__midMotionHeld=null;
          const sample=()=>{
            const thread=document.querySelector('[data-conversation-thread]');
            const remaining=thread.scrollHeight-thread.clientHeight-thread.scrollTop;
            if(thread.scrollTop>before.scroll+1&&remaining>2&&thread.closest('[data-conversation-pane]').hasAttribute('data-conversation-motion')){
              const top=thread.getBoundingClientRect().top;
              const row=[...thread.querySelectorAll('[data-conversation-view-key]')].find(row=>row.getBoundingClientRect().bottom>top+1);
              window.__midMotionHeld={key:row.dataset.conversationViewKey,offset:row.getBoundingClientRect().top-top,scroll:thread.scrollTop};
              window.__fixtureVisible=false;
              Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>window.__fixtureVisible?'visible':'hidden'});
              document.dispatchEvent(new Event('visibilitychange'));
              return;
            }
            requestAnimationFrame(sample);
          };requestAnimationFrame(sample);
        }""",before_motion)
        page.get_by_role('button',name='Jump to latest message',exact=True).click()
        page.wait_for_function('window.__midMotionHeld!==null')
        held=page.evaluate('window.__midMotionHeld')
        assert held['scroll']>before_motion['scroll']+1,(before_motion,held)
        page.wait_for_timeout(150)
        page.evaluate(sample_return,held)
        page.wait_for_timeout(1000)
        frames=page.evaluate('window.__stopReturnFrames=true;window.__returnFrames')
        assert_frames(held,frames)
        after=page.evaluate(reading)
        assert after['key']==held['key'] and abs(after['offset']-held['offset'])<=1,(held,after)
        proof.append({'position':'mid-Latest-motion','backlog':0,'admission':'immediate','beforeMotion':before_motion,'held':held,'after':after,'frames':frames})
        receipt={'cases':proof,'seconds':round(time.perf_counter()-started,3),'newHosts':0,
          'visibility':'Synthetic document visibility events exercise the production visibility handler in real Chromium.'}
        (output/'tab-return-proof.json').write_text(json.dumps(receipt,indent=2))
        print('TAB_RETURN_PROOF '+json.dumps({'cases':len(proof),'seconds':receipt['seconds'],'newHosts':0}),flush=True)
    finally:
        (output/'tab-return-latest.json').write_text(json.dumps({'cases':proof,'seconds':round(time.perf_counter()-started,3)},indent=2))
        page.close()


def run(browser, urls):
    from tests.e2e.harness import browser_failure_evidence

    with browser_failure_evidence(browser, "conversation-pane-browser"):
        if __import__('os').environ.get('CONVERSATION_SETTLED_CASE') == 'tab-return':
            _tab_return(browser, urls[0])
            return
        if __import__('os').environ.get('CONVERSATION_SETTLED_CASE') == 'position':
            _position_commit(browser, urls[0])
            return
        if __import__('os').environ.get('CONVERSATION_SETTLED_CASE') == 'overlay':
            _overlay_menu_bounds(browser, urls[0])
            return
        if __import__('os').environ.get('CONVERSATION_SETTLED_CASE') in ('legacy', 'controls'):
            _run(browser, urls)
            return
        if __import__('os').environ.get('CONVERSATION_SETTLED_CASE') == 'bounded':
            _bounded_reader(browser, urls[0])
            return
        if __import__('os').environ.get('CONVERSATION_SETTLED_CASE') == 'motion':
            _settled_motion(browser, urls[0])
            return
        if __import__('os').environ.get('CONVERSATION_SETTLED_CASE') == 'unread':
            _settled_unread_entry(browser, urls[0])
            return
        if __import__('os').environ.get('CONVERSATION_LATEST_CASE') == 'destination':
            _latest_destination(browser, urls[0])
            return
        if __import__('os').environ.get('CONVERSATION_LATEST_CASE') == 'intents':
            _latest_obsolete_intents(browser, urls[0])
            return
        _tab_return(browser, urls[0])
        _position_commit(browser, urls[0])
        _settled_motion(browser, urls[0])
        _settled_unread_entry(browser, urls[0])
        _latest_destination(browser, urls[0])
        _latest_obsolete_intents(browser, urls[0])
        _overlay_menu_bounds(browser, urls[0])
        if __import__('os').environ.get('PANELS_CONVERSATION_MENU_ONLY') == '1':
            print('Focused overlay menu proof only; other conversation scenarios were not run.',flush=True)
            return
        _manager_recap_header(browser, urls[0])
        _owner_width_frames(browser, urls[0])
        _conversation_repairs(browser, urls[0])
        _projected_visual_relationships(browser, urls[0])
        _bounded_reader(browser, urls[0])
        _run(browser, urls)
