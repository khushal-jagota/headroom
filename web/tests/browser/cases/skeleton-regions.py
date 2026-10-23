"""Browser-only proof of immediate ready content, wait cancellation and layout ownership."""
import json
import os
from pathlib import Path

from playwright.sync_api import expect


def run(browser, urls):
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(urls[0], wait_until="networkidle")
    page.wait_for_function("typeof window.__load === 'function'")
    page.evaluate("""() => {
      window.__status = document.querySelector('[data-fixed] [role=status]');
      window.__statusChanges = [];
      new MutationObserver(() => window.__statusChanges.push(window.__status.textContent))
        .observe(window.__status, {subtree:true, childList:true, characterData:true});
    }""")
    assert page.locator('[data-fixed] [role=status]').inner_text() == "Loading document"
    assert page.evaluate("!window.__status.closest('[aria-busy=true]')")
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("window.__load(false)")
    page.wait_for_function("!document.querySelector('.skeleton')")
    page.emulate_media(reduced_motion="no-preference")
    # Control only JS wait timers. Real CSS animations retain browser time, and
    # their progress is inspected/paused through the Web Animations API below.
    page.evaluate("""() => {
      const nativeSet = window.setTimeout, nativeClear = window.clearTimeout;
      let now = 0, next = -1;
      const timers = new Map();
      window.setTimeout = (callback, delay=0, ...args) => {
        const id = next--; timers.set(id,{callback:()=>callback(...args),at:now+delay}); return id;
      };
      window.clearTimeout = id => { if (!timers.delete(id)) nativeClear(id); };
      window.__waitClock = {
        advance(ms) {
          const end=now+ms;
          for (;;) {
            const first=[...timers].filter(([,timer])=>timer.at<=end)
              .sort((a,b)=>a[1].at-b[1].at)[0];
            if (!first) break;
            now=first[1].at; timers.delete(first[0]); first[1].callback();
          }
          now=end; window.__flush();
        },
        restore() { window.setTimeout=nativeSet; window.clearTimeout=nativeClear; },
        size() { return timers.size; }
      };
    }""")
    # Below announcement grace, between announcement and visual grace, and just
    # before visual admission: ready content mounts in the same Svelte flush.
    for completion in [50, 150, 199]:
        sample = page.evaluate("""completion => {
          window.__load(true); window.__waitClock.advance(completion);
          const region=document.querySelector('[data-fixed] [data-shape]');
          const pending={busy:region.getAttribute('aria-busy'),
            skeleton:!!region.querySelector('.skeleton'),
            status:window.__status.textContent,flow:document.querySelector('[data-flow]').getBoundingClientRect().height};
          window.__load(false);
          const content=region.querySelector('.skeleton-content');
          window.__waitClock.advance(500);
          return {pending,content:!!content,opacity:+getComputedStyle(content).opacity,
            animations:content.getAnimations({subtree:true}).length,busy:region.getAttribute('aria-busy'),
            status:window.__status.textContent,skeleton:!!region.querySelector('.skeleton'),timers:window.__waitClock.size()};
        }""", completion)
        assert sample["pending"] == {"busy": "true", "skeleton": False,
                                     "status": "Loading document" if completion >= 100 else "",
                                     "flow": 112}, sample
        assert sample["content"] and sample["opacity"] == 1, sample
        assert sample["animations"] == 0, sample
        assert sample["busy"] == "false" and sample["status"] == "", sample
        assert not sample["skeleton"] and sample["timers"] == 0, sample
    # Inherited seconds override controls both admission and status; restart and
    # unmount discard old waits. No new host or backend is needed for these races.
    page.evaluate("""() => {
      window.__show(false); window.__show(true);
      window.__status=document.querySelector('[data-fixed] [role=status]');
      document.querySelector('[data-fixed]').style.setProperty('--skeleton-delay','.4s');
      window.__load(false); window.__load(true); window.__waitClock.advance(199);
    }""")
    assert page.locator('[data-fixed] [role=status]').inner_text() == ''
    page.evaluate("window.__waitClock.advance(1)")
    page.wait_for_function("window.__status.textContent === 'Loading document'")
    assert page.locator('[data-fixed] .skeleton').count() == 0
    page.evaluate("window.__waitClock.advance(199)")
    assert page.locator('[data-fixed] .skeleton').count() == 0
    page.evaluate("window.__waitClock.advance(1)")
    page.wait_for_function("document.querySelector('[data-fixed] .skeleton')")
    page.evaluate("""() => {
      window.__load(false); window.__load(true); window.__waitClock.advance(150);
      window.__load(false); window.__load(true); window.__waitClock.advance(50);
    }""")
    assert page.locator('[data-fixed] .skeleton').count() == 0
    page.evaluate("window.__show(false);window.__waitClock.advance(1000)")
    assert page.locator('.skeleton').count() == 0
    assert page.evaluate('window.__waitClock.size()') == 0
    page.evaluate("""() => {
      window.__waitClock.restore();
      document.querySelector('[data-fixed]').style.removeProperty('--skeleton-delay');
      window.__show(true);
      window.__status=document.querySelector('[data-fixed] [role=status]');
      window.__statusChanges=[];
      new MutationObserver(()=>window.__statusChanges.push(window.__status.textContent))
        .observe(window.__status,{subtree:true,childList:true,characterData:true});
    }""")
    page.wait_for_function("document.querySelector('[data-entering]')")
    page.evaluate("window.__load(false)")
    assert page.locator('.skeleton').count() == 0
    page.evaluate("window.__statusChanges=[]")
    page.evaluate("window.__load(true)")
    page.wait_for_function("document.querySelector('[data-entering]')")
    assert page.evaluate("window.__status === document.querySelector('[data-fixed] [role=status]')")
    assert page.evaluate("window.__statusChanges") == ["Loading document"]
    assert page.evaluate("!window.__status.closest('[aria-busy=true]')")
    assert page.locator('[data-fixed] [aria-busy=true]').count() == 1
    # Completing even a partially entered placeholder removes it in the same flush.
    completed = page.evaluate("""() => {
      const layer=document.querySelector('[data-fixed] .skeleton');
      const entrance=layer.getAnimations()[0]; entrance.pause(); entrance.currentTime=10;
      const before=+getComputedStyle(layer).opacity;
      window.__load(false);
      return {before, placeholders:document.querySelectorAll('.skeleton').length,
        ready:[...document.querySelectorAll('.skeleton-content')].map(node=>({
          opacity:+getComputedStyle(node).opacity,animations:node.getAnimations({subtree:true}).length}))};
    }""")
    assert 0 < completed['before'] < 1, completed
    assert completed['placeholders'] == 0, completed
    assert all(item == {'opacity': 1, 'animations': 0} for item in completed['ready']), completed
    # Cached fill, flow and inline content never needs a loading pass or entrance.
    cached = page.evaluate("""() => {
      window.__idle(true);
      return [...document.querySelectorAll('[data-idle] .skeleton-content')].map(content=>({
        opacity:+getComputedStyle(content).opacity,animations:content.getAnimations({subtree:true}).length}));
    }""")
    assert len(cached) == 2, cached
    assert all(item == {'opacity': 1, 'animations': 0} for item in cached), cached
    page.evaluate("window.__idle(false);window.__flat(true)")
    page.wait_for_function("document.querySelector('[data-flat] .markdown-host p')")
    flat = page.evaluate("""() => {
      const fixture=document.querySelector('[data-flat]');
      const targets=[fixture.querySelector('[data-flat-first]'),
        fixture.querySelector('[data-flat-second]'),fixture.querySelector('.markdown-host').firstElementChild,
        fixture.querySelector('[data-flat-after]')];
      const transform=fixture.querySelector('[data-flat-second]').getAnimations()
        .find(animation=>animation instanceof CSSAnimation);
      transform.pause(); transform.currentTime=100;
      const existing=fixture.querySelector('[data-flat-existing]').getAnimations();
      const geometry=()=>targets.map(target=>{
        const box=target.getBoundingClientRect();
        return {x:box.x,y:box.y,width:box.width,height:box.height};
      });
      const before=geometry();
      const painted=targets.slice(0,3).map(target=>+getComputedStyle(target).opacity);
      const opacity=fixture.getAnimations({subtree:true}).filter(animation=>
        animation.effect.getKeyframes().some(frame=>'opacity' in frame));
      return {before,after:geometry(),painted,
        opacity:opacity.map(animation=>animation.animationName),
        transform:transform.playState,existing:existing.map(animation=>({name:animation.animationName,
          duration:animation.effect.getTiming().duration}))};
    }""")
    assert flat['before'] == flat['after'], flat
    assert flat['painted'] == [1, 1, 1], flat
    assert flat['opacity'] == ['review-arrive'], flat
    assert flat['transform'] == 'paused', flat
    assert flat['existing'] == [{'name': 'review-arrive', 'duration': 440}], flat
    page.evaluate("window.__flat(false)")
    # Repeated waits and unmount cannot leave a stale placeholder behind.
    page.evaluate("window.__load(true)")
    page.wait_for_function("document.querySelector('[data-entering]')")
    page.evaluate("window.__load(false);window.__load(true)")
    assert page.locator('.skeleton').count() == 0
    page.wait_for_function("document.querySelector('[data-entering]')")
    page.evaluate("window.__show(false)")
    assert page.locator('.skeleton').count() == 0
    page.evaluate("window.__show(true)")
    page.wait_for_function("document.querySelector('.skeleton')")
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("window.__load(false)")
    assert page.locator('.skeleton').count() == 0
    assert page.locator('.skeleton-content').evaluate_all(
        "nodes=>nodes.every(node=>getComputedStyle(node).opacity==='1' "
        "&& node.getAnimations({subtree:true}).length===0)"
    )
    page.emulate_media(reduced_motion="no-preference")

    measurements = []
    for width in [1100, 390]:
        page.set_viewport_size({"width": width, "height": 900})
        for long in [False, True]:
            page.evaluate("window.__load(true)")
            waiting = page.evaluate("""() => ({
              control:document.querySelector('[data-control]').getBoundingClientRect().y,
              flow:document.querySelector('[data-flow]').getBoundingClientRect().height,
              footer:document.querySelector('footer').getBoundingClientRect().y})""")
            page.wait_for_function("document.querySelector('[data-entering]')")
            before = page.evaluate("""() => ({
              control:document.querySelector('[data-control]').getBoundingClientRect().y,
              flow:document.querySelector('[data-flow]').getBoundingClientRect().height,
              footer:document.querySelector('footer').getBoundingClientRect().y})""")
            assert before["flow"] == 112, before
            assert before == waiting, (waiting, before)
            page.evaluate("value => {window.__shifts=[];window.__load(false,value)}", long)
            assert page.locator('.skeleton').count() == 0
            after = page.evaluate("""() => ({
              control:document.querySelector('[data-control]').getBoundingClientRect().y,
              flow:document.querySelector('[data-flow]').getBoundingClientRect().height,
              footer:document.querySelector('footer').getBoundingClientRect().y,shifts:window.__shifts})""")
            assert after["control"] == before["control"], (before, after)
            assert after["flow"] == (420 if long else 40), after
            measurements.append({"width": width, "long": long, "waiting": waiting,
                                 "before": before, "after": after,
                                 "intrinsic_growth": after["footer"] - before["footer"]})
    output = os.environ.get("PANELS_SKELETON_ARTIFACT_DIR")
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "geometry.json").write_text(json.dumps(measurements, indent=2))
        page.evaluate("window.__load(true)")
        page.wait_for_timeout(200)
        page.screenshot(path=str(directory / "skeleton-narrow.png"), full_page=True)
    # This existing host also exercises the imperative Markdown/Svelte boundary.
    # A controlled Worker exposes waits that a real parser can finish between frames.
    page.evaluate("window.__show(false);window.__mockMarkdownWorker();window.__markdown('fast')")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] [aria-busy=true]')")
    page.evaluate("window.__workers.at(-1).reply()")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] h2')")
    fast = page.evaluate("""() => {
      const fixture=document.querySelector('[data-markdown-fixture]');
      const content=fixture.querySelector('.skeleton-content');
      let target=content.firstElementChild;
      while (getComputedStyle(target).display === 'contents') target=target.firstElementChild;
      return {skeleton:!!fixture.querySelector('.skeleton'),
        status:fixture.querySelector('[role=status]').textContent,
        opacity:+getComputedStyle(target).opacity,animations:content.getAnimations({subtree:true}).length};
    }""")
    assert not fast['skeleton'] and fast['status'] == '', fast
    assert fast['opacity'] == 1 and fast['animations'] == 0, fast
    # Painted source remains available throughout a replacement parse.
    page.evaluate("""() => {
      window.__paintedMarkdown=document.querySelector('[data-markdown-fixture] h2');
      window.__previousWorkers=window.__workers.length;
      window.__markdown('refresh');
    }""")
    page.wait_for_function("window.__workers.length > window.__previousWorkers")
    assert page.evaluate(
        "window.__paintedMarkdown === document.querySelector('[data-markdown-fixture] h2')"
    )
    assert page.locator('[data-markdown-fixture] .skeleton').count() == 0
    assert page.locator('[data-markdown-fixture] [aria-busy=true]').count() == 0
    page.evaluate("window.__workers.at(-1).reply()")
    page.wait_for_function("!window.__paintedMarkdown.isConnected")
    # A fresh surface still has the delayed cold placeholder and accessible status.
    page.evaluate("window.__markdown('destroy');window.__markdown('large')")
    fixture = page.locator('[data-markdown-fixture]')
    fixture.scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('[data-markdown-fixture] .skeleton')")
    assert fixture.locator('[data-shape=document][aria-busy=true]').count() == 1
    assert fixture.locator('[role=status]').inner_text() == "Loading text..."
    assert fixture.locator('.quiet-line').count() == 0
    assert fixture.locator('[role=status]').evaluate("node=>!node.closest('[aria-busy=true]')")
    if output:
        page.wait_for_function(
            "parseFloat(getComputedStyle("
            "document.querySelector('[data-markdown-fixture] .skeleton')).opacity) > .99"
        )
        fixture.screenshot(path=str(Path(output) / "markdown-worker-skeleton.png"))
    page.evaluate("window.__workers.at(-1).reply()")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] h2')")
    assert fixture.locator('.skeleton').count() == 0
    assert fixture.locator('.skeleton-content').evaluate_all(
        "nodes=>nodes.every(node=>getComputedStyle(node).opacity==='1' "
        "&& node.getAnimations({subtree:true}).length===0)"
    )
    page.wait_for_function("document.querySelector('[data-markdown-window] h2')")
    assert fixture.locator('h2').inner_text() == 'Worker document'
    page.wait_for_function("!document.querySelector('[data-markdown-fixture] .skeleton')")
    assert fixture.locator('[role=status]').inner_text() == ''

    page.evaluate("""() => {
      window.__paintedMarkdown=document.querySelector('[data-markdown-fixture] h2');
      window.__previousWorkers=window.__workers.length;
      window.__markdown('failure');
    }""")
    page.wait_for_function("window.__workers.length > window.__previousWorkers")
    assert fixture.locator('.skeleton').count() == 0
    assert page.evaluate('window.__paintedMarkdown.isConnected')
    page.evaluate("window.__workers.at(-1).reply(true)")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] .quiet-line')")
    assert fixture.locator('.quiet-line').inner_text() == 'Parse failed'
    assert page.evaluate(
        "window.__paintedMarkdown === document.querySelector('[data-markdown-fixture] h2')"
    )
    assert fixture.locator('.skeleton').count() == 0
    assert fixture.locator('[aria-busy=true]').count() == 0

    page.evaluate("window.__previousWorkers=window.__workers.length;window.__markdown('obsolete')")
    page.wait_for_function("window.__workers.length > window.__previousWorkers")
    assert page.evaluate('window.__paintedMarkdown.isConnected')
    page.evaluate("window.__oldWorker=window.__workers.at(-1);window.__markdown('small');window.__oldWorker.reply()")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] h1')")
    assert fixture.locator('h1').inner_text() == 'Replacement document'
    assert page.evaluate('window.__oldWorker.terminated')
    assert fixture.locator('.skeleton-region, [data-markdown-window]').count() == 0

    page.evaluate("window.__previousWorkers=window.__workers.length;window.__markdown('destroyed')")
    page.wait_for_function("window.__workers.length > window.__previousWorkers")
    assert fixture.locator('h1').inner_text() == 'Replacement document'
    assert fixture.locator('.skeleton').count() == 0
    page.evaluate("window.__markdownAnimations=document.querySelector('[data-markdown-fixture]').getAnimations({subtree:true});window.__markdown('destroy');window.__workers.at(-1).reply()")
    page.wait_for_function("!document.querySelector('[data-markdown-fixture] .skeleton-region')")
    assert page.evaluate(
        "window.__markdownAnimations.every(animation=>animation.playState==='idle')"
    )
    assert page.evaluate('window.__workers.at(-1).terminated')
    assert fixture.locator('[data-markdown-window]').count() == 0
    # Reuse this browser host for frame identity, visibility and failure retention.
    # These risks need a real WindowProxy and sandbox, but no backend connection.
    page.set_viewport_size({"width": 1100, "height": 900})
    page.evaluate("""() => {
      window.__htmlFetches = 0;
      window.__htmlNavigationMessages = 0;
      window.addEventListener('message', event => {
        if (event.data?.type === 'panels-html-navigation') window.__htmlNavigationMessages++;
      });
      const originalFetch = window.fetch;
      window.fetch = (...args) => {
        if (String(args[0]).includes('/files/tickets/t_owner/')) window.__htmlFetches++;
        if (String(args[0]).endsWith('/slow.html')) return new Promise(resolve => {
          window.__resolveSlowHtml = () => resolve(new Response('<h1>Obsolete response</h1>'));
        });
        return originalFetch(...args);
      };
    }""")
    reads = []
    def serve_html(route):
        path = route.request.url.split("?", 1)[0].split("/files/", 1)[1]
        reads.append(path)
        if path.endswith("missing.html"):
            route.fulfill(status=404, body="not found")
            return
        title = "Child" if path.endswith("child.html") else "Index"
        route.fulfill(status=200, content_type="text/html", body=f"""<!doctype html><html><head>
          <base href="https://evil.example/"></head><body><h1>{title}</h1>
          <script>window.controlCounts=[0,0,0];window.controlPrevented=[];</script>
          <a id="empty-control" href=""
            onclick="window.controlCounts[0]++;window.controlPrevented.push(event.defaultPrevented);
            event.preventDefault()">Empty control</a>
          <a id="hash-control" href="#"
            onclick="window.controlCounts[1]++;window.controlPrevented.push(event.defaultPrevented);
            event.preventDefault()">Hash control</a>
          <a id="no-href-control"
            onclick="window.controlCounts[2]++;window.controlPrevented.push(event.defaultPrevented);
            event.preventDefault()">No href control</a>
          <a id="child" href="nested/child.html">Child</a>
          <a id="missing" href="missing.html">Missing</a>
          <a id="escape" href="../outside.html">Outside</a>
          <a id="fragment" href="#detail">Detail</a>
          <div style="height:1400px"></div><p id="detail">Detail content</p>
          </body></html>""")
    page.route("**/files/tickets/t_owner/**", serve_html)
    original_url = page.url
    for mode in ["full", "embedded"]:
        page.evaluate("mode => window.__html(mode)", mode)
        html = page.locator('[data-html-fixture]')
        html.scroll_into_view_if_needed()
        frame = html.frame_locator('[data-file-preview-html]')
        expect(frame.locator('h1')).to_have_text('Index')
        initial_src = html.locator('iframe').get_attribute('src')
        before_controls = page.evaluate("[window.__htmlFetches,window.__htmlNavigationMessages]")
        for control in ['empty-control', 'hash-control', 'no-href-control']:
            frame.locator('#' + control).click()
        assert frame.locator('body').evaluate("() => window.controlCounts") == [1, 1, 1]
        assert frame.locator('body').evaluate("() => window.controlPrevented") == [False] * 3
        assert page.evaluate(
            "[window.__htmlFetches,window.__htmlNavigationMessages]"
        ) == before_controls
        assert html.locator('iframe').get_attribute('src') == initial_src
        expect(html.locator('[data-html-back]')).to_have_count(0)
        page.evaluate("""() => {
          const frame=document.querySelector('[data-html-fixture] iframe');
          window.__oldHtmlWindow=frame.contentWindow;
        }""")
        frame.locator('#child').click()
        expect(frame.locator('h1')).to_have_text('Child')
        assert page.url == original_url
        page.evaluate("""() => window.dispatchEvent(new MessageEvent('message', {
          source:window.__oldHtmlWindow,
          data:{type:'panels-html-navigation',generation:'stale',href:'missing.html'}
        }))""")
        # Current source with an obsolete generation also has no authority.
        page.evaluate("""() => window.dispatchEvent(new MessageEvent('message', {
          source:document.querySelector('[data-html-fixture] iframe').contentWindow,
          data:{type:'panels-html-navigation',generation:'obsolete',href:'missing.html'}
        }))""")
        frame.locator('#fragment').click()
        expect(frame.locator('h1')).to_have_text('Child')
        frame.locator('body').evaluate(
            "() => new Promise(resolve => requestAnimationFrame(resolve))"
        )
        assert frame.locator('body').evaluate("() => window.scrollY") > 1000
        assert frame.locator('#detail').evaluate("node => node.getBoundingClientRect().top") < 500
        assert html.locator('[data-html-navigation-notice]').count() == 0
        html.locator('[data-html-back]').click()  # Fragment history.
        successful_src = html.locator('iframe').get_attribute('src')
        frame.locator('#missing').click()
        expect(html.locator('[data-html-navigation-notice]')).to_have_text(
            'file fetch failed: 404'
        )
        expect(frame.locator('h1')).to_have_text('Child')
        assert html.locator('iframe').get_attribute('src') == successful_src
        expect(html.locator('[data-visible-content-failed]')).to_have_count(0)
        frame.locator('#escape').click()  # Resolves inside the original site directory.
        expect(frame.locator('h1')).to_have_text('Index')
        html.locator('[data-html-back]').click()
        expect(frame.locator('h1')).to_have_text('Child')
        html.locator('[data-html-back]').click()
        expect(frame.locator('h1')).to_have_text('Index')
        frame.locator('#escape').click()
        expect(html.locator('[data-html-navigation-notice]')).to_have_text(
            'This link cannot be opened in the artifact viewer.'
        )
        assert not any(path.endswith('outside.html') and '/site/' not in path for path in reads)
        generation = frame.locator('head script').first.evaluate(
            "node => JSON.parse(node.textContent.match(/const generation = ([^;]+);/)[1])"
        )
        before = page.evaluate("window.__htmlFetches")
        page.evaluate("""generation => {
          const other=document.createElement('iframe');
          document.body.append(other);
          window.dispatchEvent(new MessageEvent('message', {source:other.contentWindow,
            data:{type:'panels-html-navigation',generation,href:'missing.html'}}));
          other.remove();
        }""", generation)
        page.evaluate("""generation => {
          const host=document.querySelector('[data-html-fixture]');
          host.style.visibility='hidden';
          const frame=host.querySelector('iframe');
          window.dispatchEvent(new MessageEvent('message', {source:frame.contentWindow,
            data:{type:'panels-html-navigation',generation,href:'missing.html'}}));
          host.style.visibility='';
        }""", generation)
        assert page.evaluate("window.__htmlFetches") == before
        clipped = page.evaluate("""generation => {
          const host=document.querySelector('[data-html-fixture]');
          const wrapper=document.createElement('div');
          wrapper.style.cssText='height:20px;overflow:auto;position:relative';
          host.replaceWith(wrapper); wrapper.append(host);
          host.style.transform='translateY(40px)';
          const rect=host.getBoundingClientRect();
          window.dispatchEvent(new MessageEvent('message', {
            source:host.querySelector('iframe').contentWindow,
            data:{type:'panels-html-navigation',generation,href:'missing.html'}}));
          host.style.transform=''; wrapper.replaceWith(host);
          return {top:rect.top,bottom:rect.bottom,reads:window.__htmlFetches};
        }""", generation)
        assert 0 <= clipped['top'] < 900, clipped
        assert clipped['reads'] == before, clipped
        # A request starts while visible, then its frame becomes clipped before success.
        page.evaluate("""generation => {
          const host=document.querySelector('[data-html-fixture]');
          window.dispatchEvent(new MessageEvent('message', {
            source:host.querySelector('iframe').contentWindow,
            data:{type:'panels-html-navigation',generation,href:'slow.html'}}));
          const wrapper=document.createElement('div');
          wrapper.style.cssText='height:20px;overflow:auto;position:relative';
          host.replaceWith(wrapper); wrapper.append(host);
          host.style.transform='translateY(40px)';
          window.__restoreHtml = () => {host.style.transform='';wrapper.replaceWith(host);};
          window.__resolveSlowHtml();
        }""", generation)
        page.evaluate("""() => new Promise(resolve =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
        expect(frame.locator('h1')).to_have_text('Index')
        page.evaluate("window.__restoreHtml()")
        page.evaluate("""generation => window.dispatchEvent(new MessageEvent('message', {
          source:document.querySelector('[data-html-fixture] iframe').contentWindow,
          data:{type:'panels-html-navigation',generation,href:'slow.html'}
        }))""", generation)
        frame.locator('#child').click()
        expect(frame.locator('h1')).to_have_text('Child')
        page.evaluate("window.__resolveSlowHtml()")
        page.evaluate("""() => new Promise(resolve =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
        expect(frame.locator('h1')).to_have_text('Child')
        html.locator('[data-html-back]').click()
        expect(frame.locator('h1')).to_have_text('Index')
        frame.locator('#missing').click()
        expect(html.locator('[data-html-navigation-notice]')).to_have_text('file fetch failed: 404')
        page.evaluate("window.__html('')")
    assert not errors, errors
    page.close()
