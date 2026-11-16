"""Browser-only proof of immediate ready content, wait cancellation and layout ownership."""
import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import expect


def checklist_header_and_presentations(page):
    # One existing component host isolates header hits and recursive presentation.
    # Opposite edge midpoints test whitespace inside the rounded header target.
    checklist = page.locator('[data-checklist-fixture] [data-block="checklist"]')
    page.emulate_media(reduced_motion="no-preference")
    for variant in ("empty", "partial", "complete"):
        page.evaluate(
            "window.__checklist(false);window.__checklistVariant("
            + json.dumps(variant)
            + ");window.__checklist(true)"
        )
        toggle = checklist.locator('[data-checklist-toggle]')
        expect(toggle).to_have_count(1)
        expect(toggle).to_have_accessible_name('Hide checklist')
        expect(toggle).to_have_attribute('aria-expanded', 'true')
        expect(checklist.get_by_role('progressbar')).to_have_count(1)
        assert (
            toggle.locator('button,input,[contenteditable],h1,h2,h3,[role=progressbar]').count()
            == 0
        )
        target = toggle.get_attribute('aria-controls')
        assert target and checklist.locator('[id="' + target + '"]').count() == 1
        box = toggle.bounding_box()
        assert box and box['width'] > 200 and box['height'] >= 40, box
        toggle.click(position={'x': 2, 'y': box['height'] / 2})
        expect(toggle).to_have_accessible_name('Show checklist')
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        expect(checklist.locator('[data-checklist-body]')).to_have_attribute('aria-hidden', 'true')
        expect(checklist.get_by_role('checkbox')).to_have_count(0)
        toggle.click(position={'x': box['width'] - 2, 'y': box['height'] / 2})
        expect(toggle).to_have_attribute('aria-expanded', 'true')
        toggle.focus()
        toggle.press('Space')
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        expect(toggle).to_be_focused()
        toggle.press('Enter')
        expect(toggle).to_have_attribute('aria-expanded', 'true')
        if variant == 'complete':
            checklist.get_by_role('button', name='2 completed', exact=True).click()
            expect(
                checklist.get_by_role('list', name='Completed items').get_by_role('checkbox')
            ).to_have_count(2)
    page.evaluate("window.__checklist(false);window.__checklistVariant('partial');window.__checklistOptions(true);window.__checklist(true)")
    expect(checklist).to_have_attribute('data-presentation', 'normal')
    expect(checklist.get_by_role('checkbox', name='Publish', exact=True)).to_be_visible()
    page.evaluate("window.__checklistOptions(true,false,'small')")
    expect(checklist).to_have_attribute('data-presentation', 'small')
    expect(checklist.get_by_role('checkbox', name='Publish', exact=True)).to_be_visible()
    page.evaluate("window.__checklist(false);window.__checklistOptions(false,true);window.__checklist(true)")
    expect(checklist).to_have_attribute('data-presentation', 'small')
    publish = checklist.get_by_role('checkbox', name='Publish', exact=True)
    publish.focus()
    # A responsive collapse must not strand focus in an inert body.
    page.evaluate("window.__checklistOptions(true,true)")
    expect(checklist).to_have_attribute('data-presentation', 'smallest')
    expect(checklist.locator('[data-checklist-toggle]')).to_be_focused()
    checklist.locator('[data-checklist-toggle]').click()
    expect(checklist).to_have_attribute('data-presentation', 'small')
    expect(publish).to_be_visible()
    page.evaluate("window.__checklist(false);window.__nestedChecklists(true)")
    nested = page.locator('[data-nested-checklists] [data-block="checklist"]')
    expect(nested).to_have_count(4)
    for item in nested.all():
        expect(item).to_have_attribute('data-presentation', 'smallest')
        expect(item.get_by_role('checkbox')).to_have_count(0)
        item.locator('[data-checklist-toggle]').click()
        expect(item).to_have_attribute('data-presentation', 'small')
        expect(item.get_by_role('checkbox')).to_be_visible()
        expect(item.get_by_role('textbox')).to_have_count(0)
    page.evaluate("window.__nestedChecklists(false);window.__checklistOptions(false)")


def checklist_saved_mount_and_motion(page):
    # The existing native host catches transient outro DOM, which settled-state
    # component tests cannot see. Both first mount and remount use accepted data.
    for reduced in ("no-preference", "reduce"):
        page.emulate_media(reduced_motion=reduced)
        for complete in (False, True):
            page.evaluate("window.__checklistSaved(" + str(complete).lower() + ")")
            for _ in range(2):
                frames = page.evaluate(
                    """async complete => {
                  const frames=[];
                  const sample=()=>frames.push([...document.querySelectorAll(
                    '[data-checklist-fixture] [aria-label="Open items"] [data-step]')]
                    .map(node=>node.dataset.step));
                  window.__checklist(true); sample();
                  for(let n=0;n<5;n++){await new Promise(requestAnimationFrame);sample();}
                  return frames;
                }""",
                    complete,
                )
                expected = [] if complete else ["publish"]
                assert all(frame == expected for frame in frames), (reduced, complete, frames)
                page.evaluate("window.__checklist(false)")

    page.emulate_media(reduced_motion="no-preference")
    page.evaluate("window.__checklistSaved(false);window.__checklist(true)")
    checklist = page.locator("[data-checklist-fixture]")
    publish = checklist.locator('[data-step="publish"]')
    publish.get_by_role("checkbox").click()
    page.wait_for_function("typeof window.__checklistReply === 'function'")
    page.evaluate("window.__checklistReply(true)")
    # A real edit retains the struck row before the normal delayed disclosure.
    expect(publish).to_have_class(re.compile(".*struck.*"))
    expect(publish).to_be_visible()
    assert (
        publish.locator(".title-text").evaluate("node => getComputedStyle(node).transitionDuration")
        != "0s"
    )
    expect(checklist.get_by_role("button", name="2 completed", exact=True)).to_be_visible()
    expect(publish).to_have_count(0)
    checklist.get_by_role("button", name="2 completed", exact=True).click()
    checklist.get_by_role("list", name="Completed items").get_by_role(
        "checkbox", name="Publish", exact=True
    ).click()
    page.wait_for_function("typeof window.__checklistReply === 'function'")
    page.evaluate("window.__checklistReply(true)")
    expect(publish.get_by_role("checkbox")).to_have_attribute("aria-checked", "false")
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_function("document.querySelector('[data-checklist-fixture] .reduced')")
    assert (
        publish.locator(".title-text").evaluate("node => getComputedStyle(node).transitionDuration")
        == "0s"
    )
    publish.get_by_role("checkbox").click()
    page.wait_for_function("typeof window.__checklistReply === 'function'")
    page.evaluate("window.__checklistReply(true)")
    expect(publish).to_have_count(0)
    page.evaluate("window.__checklist(false);window.__checklistSaved(false)")


def checklist_save_feedback(page):
    # Reuse this component host and browser. No backend is needed to reject a save.
    page.evaluate("window.__checklist(true)")
    checklist = page.locator('[data-checklist-fixture]')
    progress = checklist.get_by_role('progressbar')
    publish = (
        checklist.get_by_role('list', name='Open items').get_by_role('checkbox', name='Publish')
    )
    expect(progress).to_have_attribute('aria-valuenow', '1')
    publish.click()
    expect(checklist.get_by_text('Saving checklist…')).to_have_count(0)
    expect(progress).to_have_attribute('aria-valuenow', '1')
    expect(checklist.locator('[data-block=checklist]')).not_to_have_attribute(
        'data-complete', 'true'
    )
    assert 'All 2 items done.' not in checklist.inner_text()
    page.wait_for_function("typeof window.__checklistReply === 'function'")
    page.evaluate("window.__checklistReply(false)")
    expect(publish).to_have_attribute('aria-checked', 'false')
    expect(progress).to_have_attribute('aria-valuenow', '1')
    expect(checklist.get_by_role('alert')).to_contain_text('Save rejected')
    expect(checklist.get_by_role('heading', name='Delivery', exact=True)).to_be_visible()
    assert 'All 2 items done.' not in checklist.inner_text()
    checklist.get_by_role('button', name='Retry save').click()
    expect(checklist.get_by_role('alert')).to_have_count(0)
    expect(checklist.get_by_text('Saving checklist…')).to_have_count(0)
    expect(progress).to_have_attribute('aria-valuenow', '1')
    page.wait_for_function("typeof window.__checklistReply === 'function'")
    page.evaluate("window.__checklistReply(true)")
    expect(progress).to_have_attribute('aria-valuenow', '2')
    expect(checklist.locator('[data-block=checklist]')).to_have_attribute('data-complete', 'true')
    expect(checklist.get_by_role('heading', name='Delivery complete', exact=True)).to_be_visible()
    expect(checklist.get_by_text('Saving checklist…')).to_have_count(0)
    expect(checklist.get_by_role('status')).to_have_text('All 2 items done.')
    # An accepted uncheck reopens the checklist through the same writer queue.
    checklist.get_by_role('button', name='2 completed', exact=True).click()
    (
        checklist.get_by_role('list', name='Completed items')
        .get_by_role('checkbox', name='Publish').click()
    )
    expect(progress).to_have_attribute('aria-valuenow', '2')
    page.wait_for_function("typeof window.__checklistReply === 'function'")
    page.evaluate("window.__checklistReply(true)")
    expect(progress).to_have_attribute('aria-valuenow', '1')
    expect(publish).to_have_attribute('aria-checked', 'false')
    # Equal first/last intents must not let an old rejection clear the last click.
    publish.click()
    publish.click()
    publish.click()
    for _ in range(2):
        page.wait_for_function("typeof window.__checklistReply === 'function'")
        page.evaluate("window.__checklistReply(false)")
        expect(checklist.get_by_role('alert')).to_have_count(0)
        expect(publish).to_have_attribute('aria-checked', 'true')
    page.wait_for_function("typeof window.__checklistReply === 'function'")
    page.evaluate("window.__checklistReply(true)")
    expect(progress).to_have_attribute('aria-valuenow', '2')
    expect(checklist.get_by_text('Saving checklist…')).to_have_count(0)
    page.evaluate("window.__checklist(false)")


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
    checklist_started = time.monotonic()
    checklist_header_and_presentations(page)
    checklist_saved_mount_and_motion(page)
    page.emulate_media(reduced_motion="reduce")
    checklist_save_feedback(page)
    print(
        f"checklist save feedback: passed ({time.monotonic() - checklist_started:.2f}s)",
        flush=True,
    )
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
      const controlledSet = window.setTimeout, controlledClear = window.clearTimeout;
      window.__waitClock = {
        install() { window.setTimeout=controlledSet; window.clearTimeout=controlledClear; },
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
        # The placeholders have appeared after their delay and finished entering.
        page.wait_for_function("""() => {
          const layers = [...document.querySelectorAll('.skeleton')];
          return layers.length > 0 && layers.every(layer =>
            layer.getAnimations().every(animation => animation.playState === 'finished'));
        }""")
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
    # A real frame checks the browser navigation boundary. Authored links and scripts
    # run directly from the managed address without the former document rewrite.
    page.set_viewport_size({"width": 1100, "height": 900})
    def serve_html(route):
        child = route.request.url.split("?", 1)[0].endswith("child.html")
        route.fulfill(status=200, content_type="text/html", body=(
            "<!doctype html><h1>Child</h1>" if child else
            "<!doctype html><h1>Index</h1><a href='child.html'>Child</a>"
        ))
    page.route("**/files/tickets/t_owner/**", serve_html)
    page.evaluate("window.__html('link')")
    html = page.locator('[data-html-fixture]')
    expect(html.locator('a.file-preview-artifact-link')).to_have_text('index.html')
    assert html.locator('iframe').count() == 0
    for mode in ["full", "embedded"]:
        page.evaluate("mode => window.__html(mode)", mode)
        html.scroll_into_view_if_needed()
        iframe = html.locator('[data-file-preview-html]')
        frame = html.frame_locator('[data-file-preview-html]')
        expect(frame.locator('h1')).to_have_text('Index')
        assert iframe.get_attribute('sandbox') is None
        frame.locator('a').click()
        expect(frame.locator('h1')).to_have_text('Child')
        page.evaluate("window.__html('')")
    _preview_cards(page)
    assert not errors, errors
    page.close()


def _preview_cards(page):
    """Existing host proves real layout clipping, keyboard focus and media engine wiring.

    No connected server is needed: route/range playback remains in its existing E2E.
    """
    import io
    import wave

    page.route(
        "**/api/conversation/conversations/proof/files/proof.txt",
        lambda route: route.fulfill(content_type="text/plain", body="Small attachment"),
    )
    page.route(
        "**/files/tickets/t_card/proof.png",
        lambda route: route.fulfill(
            content_type="image/svg+xml",
            body=(
                '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="640">'
                '<rect width="640" height="640"/></svg>'
            ),
        ),
    )
    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000 * 8)
    clip_file = Path(__file__).resolve().parents[4] / "tests/e2e/fixtures/playable_clip.mp4"
    page.route(
        "**/files/tickets/t_card/proof.mp4",
        lambda route: route.fulfill(content_type="video/mp4", body=clip_file.read_bytes()),
    )
    proof_directory = os.environ.get("PANELS_UI_PROOF_DIR")

    def screenshot(name):
        if proof_directory:
            directory = Path(proof_directory)
            directory.mkdir(parents=True, exist_ok=True)
            page.locator("[data-card-fixture]").screenshot(path=str(directory / name))

    audio_requests = []

    def serve_audio(route):
        audio_requests.append(route.request.url)
        body = audio.getvalue()
        range_header = route.request.headers.get("range")
        if range_header:
            start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
            start = int(start_text or 0)
            end = min(int(end_text) if end_text else len(body) - 1, len(body) - 1)
            route.fulfill(
                status=206,
                content_type="audio/wav",
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Range": f"bytes {start}-{end}/{len(body)}",
                },
                body=body[start : end + 1],
            )
        else:
            route.fulfill(content_type="audio/wav", headers={"Accept-Ranges": "bytes"}, body=body)

    page.route("**/files/tickets/t_card/proof.wav", serve_audio)
    page.route(
        "**/files/tickets/t_card/invalid.wav",
        lambda route: route.fulfill(content_type="audio/wav", body=b"not an audio resource"),
    )
    page.set_viewport_size({"width": 1100, "height": 900})
    fixture = page.locator("[data-card-fixture]")
    # Embedded, a Markdown artifact is a link to its full preview, with nothing fetched.
    page.evaluate("window.__preview('proof.md')")
    link = fixture.locator("a.file-preview-artifact-link")
    expect(link).to_have_text("proof.md")
    expect(fixture.locator(".file-preview-clip")).to_have_count(0)
    expect(fixture.locator(".markdown-block")).to_have_count(0)
    link.focus()
    expect(link).to_be_focused()
    screenshot("preview-markdown-link-desktop.png")
    page.evaluate("window.__preview('proof.txt',false,true)")
    expect(fixture.locator(".pv-name")).to_have_text("proof.txt")
    expect(fixture.locator(".file-preview-kind")).to_contain_text("1.0 KiB")
    assert (
        fixture.locator(".file-preview-card").evaluate(
            "node => getComputedStyle(node).borderTopWidth"
        )
        == "1px"
    )
    fixture.locator(".pv-name").focus()
    page.keyboard.press("Tab")
    expect(fixture.locator(".pv-act")).to_be_focused()
    assert fixture.locator(".pv-act").evaluate("node => !node.closest('a a')")
    page.set_viewport_size({"width": 390, "height": 900})
    expect(fixture.locator(".file-preview-clip")).to_be_visible()
    screenshot("preview-attachment-phone.png")
    page.evaluate("window.__preview('proof.png')")
    expect(fixture.locator("[data-media-open] img")).to_be_visible()
    assert fixture.locator("img").evaluate("node => node.getBoundingClientRect().height") <= 280
    page.evaluate("window.__preview('missing.md')")
    expect(fixture.locator("a.file-preview-artifact-link")).to_have_text("missing.md")
    expect(fixture.get_by_role("link", name="Download", exact=True)).to_be_visible()
    page.evaluate("window.__preview('proof.mp4')")
    play = fixture.locator('[data-media-open="video"]')
    expect(play).to_be_visible()
    screenshot("preview-video-phone.png")
    page.set_viewport_size({"width": 1100, "height": 900})
    screenshot("preview-video-desktop.png")
    page.set_viewport_size({"width": 390, "height": 900})
    poster = fixture.locator("video.file-preview-video-poster")
    expect(poster).to_have_attribute("preload", "metadata")
    assert poster.evaluate("node => node.paused")
    expect(fixture.locator("video[controls]")).to_have_count(0)
    expect(play.locator(".file-preview-media-duration")).to_be_visible()
    # Keyboard activation opens the expanded view, where the video plays natively.
    play.focus()
    page.keyboard.press("Enter")
    viewer = page.locator("[data-media-viewer]")
    expect(viewer.locator("video[controls]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    expect(play).to_be_focused()
    page.evaluate("window.__preview('proof.mp4',true)")
    expect(fixture.locator("video[controls]")).to_be_visible()
    assert fixture.locator("[data-media-open]").count() == 0
    page.evaluate("window.__preview('proof.wav')")
    expect(fixture.locator("[data-media-play]")).to_be_visible()
    assert fixture.locator("audio").count() == 0 and audio_requests == []
    slider = fixture.locator("[data-audio-seek]")
    expect(slider).to_have_attribute("aria-disabled", "true")
    # Keep the original native promise: immediate pause must cancel a real start.
    page.evaluate("""() => {
      const nativePlay = HTMLMediaElement.prototype.play;
      window.__audioCancellation = {nativePlay};
      HTMLMediaElement.prototype.play = function(...args) {
        const proof = window.__audioCancellation;
        proof.player = this;
        proof.slider = document.querySelector('[data-card-fixture] [data-audio-seek]');
        const pending = nativePlay.apply(this, args);
        this.pause();
        pending.catch(error => {
          proof.rejection = {isDOMException: error instanceof DOMException, name: error.name};
        });
        return pending;
      };
    }""")
    try:
        fixture.locator("[data-media-play]").click()
        page.wait_for_function("window.__audioCancellation.rejection")
        assert page.evaluate("window.__audioCancellation.rejection") == {
            "isDOMException": True,
            "name": "AbortError",
        }
        assert page.evaluate("""() => {
          const proof = window.__audioCancellation;
          return proof.player === document.querySelector('[data-card-fixture] audio')
            && proof.slider === document.querySelector('[data-card-fixture] [data-audio-seek]');
        }""")
        expect(slider).to_have_attribute("role", "slider")
        expect(fixture.locator("[data-visible-content-failed]")).to_have_count(0)
        expect(fixture.locator(".quiet-line")).to_have_count(0)
        screenshot("audio-after-cancellation.png")
    finally:
        page.evaluate("""() => {
          HTMLMediaElement.prototype.play = window.__audioCancellation.nativePlay;
        }""")
    fixture.get_by_role("button", name="Play proof.wav", exact=True).click()
    page.wait_for_function("document.querySelector('[data-card-fixture] audio')?.currentTime > .1")
    assert page.evaluate(
        "window.__audioCancellation.player === document.querySelector('[data-card-fixture] audio')"
    )
    page.evaluate("delete window.__audioCancellation")
    page.wait_for_function("document.querySelector('[data-card-fixture] audio')?.duration >= 8")
    expect(slider).to_have_attribute("aria-disabled", "false")
    screenshot("preview-audio-phone.png")
    fixture.get_by_role("button", name="Pause proof.wav").click()
    slider.focus()
    slider.press("Home")
    page.wait_for_function("document.querySelector('[data-card-fixture] audio').currentTime < .1")
    slider.press("ArrowRight")
    page.wait_for_function("document.querySelector('[data-card-fixture] audio').currentTime >= 4.9")
    expect(slider).to_have_attribute("aria-valuenow", "5")
    assert "0:05" in slider.get_attribute("aria-valuetext")
    slider.press("End")
    page.wait_for_function("document.querySelector('[data-card-fixture] audio').currentTime >= 7.9")
    box = slider.bounding_box()
    slider.click(position={"x": box["width"] / 2, "y": box["height"] / 2})
    page.wait_for_function(
        "Math.abs(document.querySelector('[data-card-fixture] audio').currentTime - 4) < .3"
    )
    assert audio_requests
    page.set_viewport_size({"width": 1100, "height": 900})
    screenshot("preview-audio-desktop.png")
    page.evaluate("window.__preview('replacement.wav')")
    expect(fixture.locator("audio")).to_have_count(0)
    expect(fixture.locator("[data-audio-seek]")).to_have_attribute("aria-disabled", "true")
    expect(fixture.locator("[data-audio-seek]")).to_have_attribute("aria-valuenow", "0")
    # Policy rejection must still reach FilePreview's failure state.
    page.evaluate("""() => {
      window.__audioNativePlay = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function() {
        return Promise.reject(new DOMException('Playback denied', 'NotAllowedError'));
      };
    }""")
    try:
        page.evaluate("window.__preview('proof.wav')")
        fixture.get_by_role("button", name="Play proof.wav", exact=True).click()
        expect(fixture.locator("[data-visible-content-failed]")).to_have_count(1)
        expect(fixture.locator(".quiet-line")).to_contain_text("proof.wav — file fetch failed")
        expect(fixture.locator("[data-audio-seek]")).to_have_count(0)
        screenshot("preview-audio-policy-failure.png")
    finally:
        page.evaluate("""() => {
          HTMLMediaElement.prototype.play = window.__audioNativePlay;
          delete window.__audioNativePlay;
        }""")
    # An invalid resource must emit a native media error and retain the failure UI.
    page.evaluate("""() => {
      window.__audioResourceError = null;
      window.__observeAudioError = event => {
        if (event.target instanceof HTMLAudioElement) {
          window.__audioResourceError = {trusted: event.isTrusted, code: event.target.error?.code};
        }
      };
      document.addEventListener('error', window.__observeAudioError, true);
      window.__preview('invalid.wav');
    }""")
    try:
        fixture.get_by_role("button", name="Play invalid.wav", exact=True).click()
        page.wait_for_function("window.__audioResourceError")
        assert page.evaluate("window.__audioResourceError") == {"trusted": True, "code": 4}
        expect(fixture.locator("[data-visible-content-failed]")).to_have_count(1)
        expect(fixture.locator(".quiet-line")).to_contain_text("invalid.wav — file fetch failed")
        expect(fixture.locator("[data-audio-seek]")).to_have_count(0)
        screenshot("preview-audio-resource-failure.png")
    finally:
        page.evaluate("""() => {
          document.removeEventListener('error', window.__observeAudioError, true);
          delete window.__observeAudioError;
          delete window.__audioResourceError;
        }""")
    page.evaluate("window.__preview('')")
