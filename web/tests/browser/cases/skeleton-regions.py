"""Browser-only proof of opacity interruption, cancellation and layout ownership."""
import json
import os
from pathlib import Path


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
          const animation=content.getAnimations()[0]; animation.pause(); animation.currentTime=150;
          window.__waitClock.advance(500);
          return {pending,content:!!content,opacity:+getComputedStyle(content).opacity,
            duration:animation.effect.getTiming().duration,busy:region.getAttribute('aria-busy'),
            status:window.__status.textContent,skeleton:!!region.querySelector('.skeleton'),timers:window.__waitClock.size()};
        }""", completion)
        assert sample["pending"] == {"busy": "true", "skeleton": False,
                                     "status": "Loading document" if completion >= 100 else "",
                                     "flow": 112}, sample
        assert sample["content"] and 0 < sample["opacity"] < 1, sample
        assert sample["duration"] == 300, sample
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
    page.wait_for_function("!document.querySelector('[data-leaving]')")
    page.evaluate("window.__statusChanges=[]")
    page.evaluate("window.__load(true)")
    page.wait_for_function("document.querySelector('[data-entering]')")
    assert page.evaluate("window.__status === document.querySelector('[data-fixed] [role=status]')")
    assert page.evaluate("window.__statusChanges") == ["Loading document"]
    assert page.evaluate("!window.__status.closest('[aria-busy=true]')")
    assert page.locator('[data-fixed] [aria-busy=true]').count() == 1
    # Control animation time rather than depend on host scheduling speed.
    interrupted = page.evaluate("""async () => {
      const layer = document.querySelector('[data-fixed] .skeleton');
      const entrance = layer.getAnimations()[0];
      entrance.pause(); entrance.currentTime = 10;
      const inlineLayer=document.querySelector('[data-inline] .skeleton');
      const inlineEntrance=inlineLayer.getAnimations()[0];
      inlineEntrance.pause(); inlineEntrance.currentTime=10;
      const inlineBefore=+getComputedStyle(inlineLayer).opacity;
      const before = +getComputedStyle(layer).opacity;
      window.__load(false);
      await new Promise(requestAnimationFrame);
      const inline=document.querySelector('[data-inline] .skeleton-content');
      const fade=inline.getAnimations()[0]; fade.pause(); fade.currentTime=150;
      return {before, from:+layer.style.getPropertyValue('--skeleton-from'),
        inline:+getComputedStyle(inline).opacity,inlineBefore,inlineFrom:+inlineLayer.style.getPropertyValue('--skeleton-from')};
    }""")
    assert 0 < interrupted["before"] < 1 and 0 < interrupted["inline"] < 1, interrupted
    assert abs(interrupted["before"] - interrupted["from"]) < 0.001, interrupted
    assert abs(interrupted["inlineBefore"] - interrupted["inlineFrom"]) < 0.001, interrupted
    page.wait_for_function("!document.querySelector('[data-leaving]')")
    # Initial idle mounts, including inline values, fade without a loading pass.
    cached = page.evaluate("""() => {
      window.__idle(true);
      return [...document.querySelectorAll('[data-idle] .skeleton-content')].map(content=>{
        const target=content.matches('.skeleton-content--inline')
          ? content : content.firstElementChild;
        const animation=target.getAnimations()[0]; animation.pause(); animation.currentTime=150;
        const opacity=+getComputedStyle(target).opacity;
        content.getAnimations({subtree:true}).forEach(animation=>animation.finish());
        return {opacity,duration:animation.effect.getTiming().duration};
      });
    }""")
    assert len(cached) == 2 and all(
        0 < item['opacity'] < 1 and item['duration'] == 300 for item in cached
    ), cached
    page.wait_for_function("!document.querySelector('[data-idle] .resource-arriving')")
    page.evaluate("window.__idle(false);window.__flat(true)")
    page.wait_for_function("document.querySelector('[data-flat] .markdown-host p')")
    flat = page.evaluate("""async () => {
      const fixture=document.querySelector('[data-flat]');
      const targets=[fixture.querySelector('[data-flat-first]'),
        fixture.querySelector('[data-flat-second]'),fixture.querySelector('.markdown-host').firstElementChild,
        fixture.querySelector('[data-flat-after]')];
      const transform=fixture.querySelector('[data-flat-second]').getAnimations()
        .find(animation=>animation instanceof CSSAnimation);
      transform.pause(); transform.currentTime=100;
      const existing=fixture.querySelector('[data-flat-existing]').getAnimations();
      const opacity=fixture.getAnimations({subtree:true}).filter(animation=>
        animation.effect.getKeyframes().some(frame=>'opacity' in frame));
      opacity.forEach(animation=>{animation.pause();animation.currentTime=1;});
      const geometry=()=>targets.map(target=>{
        const box=target.getBoundingClientRect();
        return {x:box.x,y:box.y,width:box.width,height:box.height};
      });
      const before=geometry();
      opacity.forEach(animation=>animation.currentTime=150);
      const during=geometry();
      const painted=targets.slice(0,3).map(target=>+getComputedStyle(target).opacity);
      const easing=targets.slice(0,3).map(target=>target.getAnimations().find(animation=>
        animation.effect.getKeyframes().some(frame=>'opacity' in frame)).effect.getTiming().easing);
      opacity.forEach(animation=>animation.finish());
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
      return {before,during,after:geometry(),painted,easing,
        transform:transform.playState,
        existing:existing.map(animation=>({name:animation.animationName,
          duration:animation.effect.getTiming().duration}))};
    }""")
    assert flat['before'] == flat['during'] == flat['after'], flat
    assert all(0 < opacity < 1 for opacity in flat['painted']), flat
    assert flat['easing'] == ['ease-in-out'] * 3, flat
    assert flat['transform'] == 'paused', flat
    assert flat['existing'] == [{'name': 'review-arrive', 'duration': 440}], flat
    page.evaluate("window.__flat(false)")
    # A second load cancels the outgoing animation; its old completion cannot hide it.
    page.evaluate("window.__load(true)")
    page.wait_for_function("document.querySelector('[data-entering]')")
    page.evaluate("window.__load(false)")
    page.wait_for_function("document.querySelector('[data-leaving]')")
    page.evaluate("window.__load(true)")
    page.wait_for_timeout(450)
    assert page.locator('[data-fixed] .skeleton').count() == 1
    assert page.locator('[data-leaving]').count() == 0
    page.evaluate("window.__show(false)")
    page.wait_for_timeout(450)
    assert page.locator('.skeleton').count() == 0
    assert not errors, errors
    page.evaluate("window.__show(true)")
    page.wait_for_function("document.querySelector('.skeleton')")
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("window.__load(false)")
    page.wait_for_function("!document.querySelector('.skeleton')")
    assert page.locator('.resource-arriving').count() == 0
    page.emulate_media(reduced_motion="no-preference")

    page.evaluate("window.__load(true)")
    page.wait_for_function("document.querySelector('.skeleton')")
    page.evaluate("window.__load(false)")
    page.wait_for_function("document.querySelector('[data-leaving]')")
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_function("!document.querySelector('.skeleton, .resource-arriving')")
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
            page.wait_for_function("document.querySelector('[data-leaving]')")
            during = page.locator('[data-flow]').bounding_box()["height"]
            assert during >= before["flow"]
            page.wait_for_function("!document.querySelector('.skeleton')")
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
    page.wait_for_function("document.querySelector('[data-markdown-fixture] .resource-arriving')")
    fast = page.evaluate("""() => {
      const fixture=document.querySelector('[data-markdown-fixture]');
      const content=fixture.querySelector('.skeleton-content');
      let target=content.firstElementChild;
      while (getComputedStyle(target).display === 'contents') target=target.firstElementChild;
      const animation=target.getAnimations()[0];
      animation.pause(); animation.currentTime=150;
      return {skeleton:!!fixture.querySelector('.skeleton'),
        status:fixture.querySelector('[role=status]').textContent,
        opacity:+getComputedStyle(target).opacity};
    }""")
    assert not fast['skeleton'] and fast['status'] == '' and 0 < fast['opacity'] < 1, fast
    page.evaluate("window.__markdown('large')")
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
    page.wait_for_function("document.querySelector('[data-markdown-fixture] .resource-arriving')")
    assert fixture.locator('.skeleton[data-leaving]').count() == 1
    page.wait_for_function("document.querySelector('[data-markdown-window] h2')")
    assert fixture.locator('h2').inner_text() == 'Worker document'
    page.wait_for_function("!document.querySelector('[data-markdown-fixture] .skeleton')")
    assert fixture.locator('[role=status]').inner_text() == ''

    page.evaluate("window.__markdown('failure')")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] .skeleton')")
    page.evaluate("window.__workers.at(-1).reply(true)")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] .quiet-line')")
    assert fixture.locator('.quiet-line').inner_text() == 'Parse failed'
    page.wait_for_function("!document.querySelector('[data-markdown-fixture] .skeleton')")
    assert fixture.locator('[aria-busy=true]').count() == 0

    page.evaluate("window.__markdown('obsolete')")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] .skeleton')")
    page.evaluate("window.__oldWorker=window.__workers.at(-1);window.__markdown('small');window.__oldWorker.reply()")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] h1')")
    assert fixture.locator('h1').inner_text() == 'Replacement document'
    assert page.evaluate('window.__oldWorker.terminated')
    assert fixture.locator('.skeleton-region, [data-markdown-window]').count() == 0

    page.evaluate("window.__markdown('destroyed')")
    page.wait_for_function("document.querySelector('[data-markdown-fixture] .skeleton')")
    page.evaluate("window.__markdownAnimations=document.querySelector('[data-markdown-fixture]').getAnimations({subtree:true});window.__markdown('destroy');window.__workers.at(-1).reply()")
    page.wait_for_function("!document.querySelector('[data-markdown-fixture] .skeleton-region')")
    assert page.evaluate(
        "window.__markdownAnimations.every(animation=>animation.playState==='idle')"
    )
    assert page.evaluate('window.__workers.at(-1).terminated')
    assert fixture.locator('[data-markdown-window]').count() == 0
    assert not errors, errors
    page.close()
