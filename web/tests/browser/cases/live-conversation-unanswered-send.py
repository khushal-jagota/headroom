# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.state_barriers import establish_scrollback  # noqa: E402

_ARM_MOTION = r"""({owner, suppressWheel=false})=>{
  window.__nativeMotion?.cleanup();
  const thread=document.querySelector('[data-conversation-thread]'),frames=[],events=[];
  let stopped=false;
  const read=()=>({ms:performance.now(),top:thread.scrollTop,
    owner:thread.dataset.conversationPositionOwner,
    motion:!!thread.closest('[data-conversation-pane]')?.hasAttribute('data-conversation-motion'),
    same:thread===document.querySelector('[data-conversation-thread]')&&thread.isConnected});
  const capture=kind=>{
    const state=read(),last=frames.at(-1),previous=frames.at(-2);
    const recent=!!previous&&state.ms-last.ms<=100&&last.ms-previous.ms<=100;
    const valid=state.same&&state.owner===owner&&state.motion&&recent&&
      previous.same&&last.same&&previous.owner===owner&&last.owner===owner&&
      previous.motion&&last.motion&&Math.abs(last.top-previous.top)>.5;
    const receipt={kind,...state,valid,expectedOwner:owner,frames:frames.slice(-2)};
    return receipt;
  };
  const sample=()=>{if(stopped)return;frames.push(read());if(frames.length>360)frames.shift();requestAnimationFrame(sample);};
  const wheel=event=>{
    if(!thread.contains(event.target))return;
    const receipt={...capture('wheel'),trusted:event.isTrusted,delta:event.deltaY};
    receipt.valid=receipt.valid&&receipt.trusted;
    events.push(receipt);
    if(suppressWheel)event.stopPropagation();
    requestAnimationFrame(()=>events.push({...read(),kind:'after-wheel'}));
  };
  const scroll=event=>{
    if(event.target===thread&&events.some(item=>item.kind==='wheel'))
      events.push({...read(),kind:event.type,trusted:event.isTrusted});
  };
  document.addEventListener('wheel',wheel,true);
  thread.addEventListener('scroll',scroll);thread.addEventListener('scrollend',scroll);
  window.__nativeMotion={frames,events,capture,cleanup(){stopped=true;document.removeEventListener('wheel',wheel,true);thread.removeEventListener('scroll',scroll);thread.removeEventListener('scrollend',scroll);}};
  requestAnimationFrame(sample);
}"""


def _arm_motion(page, owner, *, suppress_wheel=False):
    page.evaluate(_ARM_MOTION, {"owner": owner, "suppressWheel": suppress_wheel})


def _await_motion(page, code):
    try:
        page.wait_for_function("window.__nativeMotion.capture('ready').valid", timeout=5_000)
    except Exception as error:
        raise AssertionError((code, page.evaluate("window.__nativeMotion.capture('missing-phase')"))) from error


def _motion_wheel(page, delta, code, *, late=False, defer_behavior=False):
    _await_motion(page, code)
    if late:
        page.wait_for_function("!window.__nativeMotion.capture('late').motion")
    page.mouse.wheel(0, delta)
    try:
        page.wait_for_function("window.__nativeMotion.events.some(e=>e.kind==='after-wheel')", timeout=5_000)
    except Exception as error:
        raise AssertionError((code, page.evaluate("window.__nativeMotion.events"))) from error
    events = page.evaluate("window.__nativeMotion.events")
    delivered = next(event for event in events if event["kind"] == "wheel")
    assert delivered["valid"], (code, events)
    after = next(event for event in events if event["kind"] == "after-wheel")
    if not defer_behavior:
        assert after["same"] and after["owner"] == "manual", ("BEHAVIOR_WHEEL_CANCELLATION", events)
    # Observe native completion instead of waiting through the automatic travel
    # that a broken cancellation must leave visible to the preservation checks.
    try:
        page.wait_for_function("window.__nativeMotion.events.some(e=>e.kind==='scrollend'&&e.trusted)", timeout=5_000)
    except Exception as error:
        raise AssertionError(('SETUP_NATIVE_SCROLLEND', page.evaluate("window.__nativeMotion.events"))) from error
    return page.evaluate("window.__nativeMotion.events")


def run_retirement(browser, urls, *, control=""):
    """Trusted input, browser anchoring, and view notifications share the live host.

    Distinct gap: delivery checks stop after placement. This phase proves the later
    manual-reader range, including tiny gap transitions and follow admission.
    No new browser host or backend server is required.
    """
    import json
    import os
    import time
    from pathlib import Path

    output = Path(os.environ.get("PANELS_E2E_FAILURE_DIR", "/tmp"))
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    page = browser.new_page(viewport={"width": 1100, "height": 800}, reduced_motion="no-preference", record_video_dir=str(output / "retirement-video"))
    page.set_default_timeout(6_000)
    samples = []
    checks = []
    errors = []
    native_events = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    probe = r"""() => {
      const t=document.querySelector('[data-conversation-thread]'); if(!t)return null;
      const bounds=t.getBoundingClientRect(),content=t.querySelector('[data-conversation-content]');
      const room=t.querySelector('[data-conversation-reserved-space]');
      const last=content.lastElementChild;
      const anchor=window.__retirementAnchor;
      const sent=[...t.querySelectorAll('[data-conversation-outgoing]')].at(-1);
      const css=getComputedStyle(t),height=room?.getBoundingClientRect().height??0;
      const gap=room?parseFloat(css.rowGap)+parseFloat(getComputedStyle(room).marginTop)+parseFloat(getComputedStyle(room).marginBottom):0;
      // Geometry alone remains valid when scroll chaining moves the entire host
      // offscreen. Record actual visibility through every ancestor and a text hit.
      const chain=[];
      const clip={left:Math.max(0,bounds.left),top:Math.max(0,bounds.top+24),right:Math.min(innerWidth,bounds.right),bottom:Math.min(innerHeight,bounds.bottom-12)};
      for(let node=t;node;node=node.parentElement){
        const style=getComputedStyle(node),rect=node.getBoundingClientRect();
        chain.push({tag:node.tagName,id:node.id,classes:node.className,opacity:Number(style.opacity),visibility:style.visibility,display:style.display,contentVisibility:style.contentVisibility,hidden:node.hidden,overflowX:style.overflowX,overflowY:style.overflowY,rect:{left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom}});
        if(['hidden','clip','auto','scroll'].includes(style.overflowX)){clip.left=Math.max(clip.left,rect.left);clip.right=Math.min(clip.right,rect.right);}
        if(['hidden','clip','auto','scroll'].includes(style.overflowY)){clip.top=Math.max(clip.top,rect.top);clip.bottom=Math.min(clip.bottom,rect.bottom);}
      }
      let textHit=null;
      if(clip.right>clip.left&&clip.bottom>clip.top){
        const walker=document.createTreeWalker(content,NodeFilter.SHOW_TEXT);
        let node;
        while((node=walker.nextNode())&&!textHit){
          if(!node.textContent.trim()||!node.parentElement.checkVisibility({checkOpacity:true,checkVisibilityCSS:true,contentVisibilityAuto:true}))continue;
          const range=document.createRange();range.selectNodeContents(node);
          for(const rect of range.getClientRects()){
            const left=Math.max(clip.left,rect.left),right=Math.min(clip.right,rect.right),top=Math.max(clip.top,rect.top),bottom=Math.min(clip.bottom,rect.bottom);
            if(right-left<2||bottom-top<2)continue;
            const x=(left+right)/2,y=(top+bottom)/2,hit=document.elementFromPoint(x,y);
            if(hit&&(hit===node.parentElement||node.parentElement.contains(hit))){textHit={text:node.textContent.trim().slice(0,100),x,y,tag:hit.tagName,rect:{left,right,top,bottom}};break;}
          }
        }
      }
      const paint={chain,clip,textHit,scrollX:window.scrollX,scrollY:window.scrollY,placementReady:t.dataset.conversationPlacementReady==='true',
        insideViewport:bounds.left>=0&&bounds.top>=0&&bounds.right<=innerWidth&&bounds.bottom<=innerHeight,
        stylesVisible:chain.every(item=>item.opacity>=.999&&item.visibility==='visible'&&item.display!=='none'&&item.contentVisibility!=='hidden'&&!item.hidden)};
      paint.ok=paint.placementReady&&paint.insideViewport&&paint.stylesVisible&&textHit!==null;
      return {ms:performance.now(),phase:window.__retirementPhase,top:t.scrollTop,
        scrollHeight:t.scrollHeight,clientHeight:t.clientHeight,viewportHeight:bounds.height,
        contentHeight:content.getBoundingClientRect().height,
        contentBottom:content.getBoundingClientRect().bottom-bounds.top,
        room:height,gap,totalTail:height+gap,paddingTop:parseFloat(css.paddingTop),paddingBottom:parseFloat(css.paddingBottom),
        sentTop:sent?sent.getBoundingClientRect().top-bounds.top:null,
        anchor:anchor?.isConnected?{key:anchor.getAttribute('data-conversation-view-key')??anchor.getAttribute('data-conversation-row'),offset:anchor.getBoundingClientRect().top-bounds.top}:null,
        latest:!!document.querySelector('[aria-label="Jump to latest message"]'),
        placementReady:t.dataset.conversationPlacementReady,owner:t.dataset.conversationPositionOwner??null,paint};
    }"""

    def frames_between(before, after):
        return page.evaluate('(span)=>window.__retirementFrames.filter(f=>f.ms>=span[0]&&f.ms<=span[1])', [before['ms'], after['ms']])

    def fixed_reading_frames(before, after, label):
        frames = frames_between(before, after)
        checks.append((label + ' has no intermediate position snap', all(abs(frame['top'] - before['top']) <= 1 for frame in frames), before, after))
        if before['anchor']:
            checks.append((label + ' preserves the reading line before each paint', all(frame['anchor'] and frame['anchor']['key'] == before['anchor']['key'] and abs(frame['anchor']['offset'] - before['anchor']['offset']) <= 1 for frame in frames), before, after))

    def sample(label):
        page.evaluate("label=>window.__retirementPhase=label", label)
        value = page.evaluate(probe)
        samples.append({"label": label, **value})
        return value

    def remember_anchor():
        page.evaluate("""()=>{const t=document.querySelector('[data-conversation-thread]'),b=t.getBoundingClientRect();window.__retirementAnchor=[...t.querySelectorAll('[data-conversation-view-key], [data-conversation-outgoing]')].find(n=>n.getBoundingClientRect().bottom>b.top+30&&n.getBoundingClientRect().top<b.bottom)??t.querySelector('[data-conversation-outgoing]');}""")

    def wheel(delta, label, wait=220):
        page.evaluate("label=>window.__retirementPhase=label", label)
        page.locator('[data-conversation-thread]').hover()
        page.mouse.wheel(0, delta)
        page.wait_for_timeout(wait)
        return sample(label)

    def pulse(label):
        before = sample(label + "-before")
        page.evaluate("window.__pulse()")
        page.wait_for_timeout(260)
        after = sample(label + "-after")
        fixed_reading_frames(before, after, label)
        checks.append(("notification cannot snap the manual reader", abs(after['top'] - before['top']) <= 1, before, after))
        if before['anchor'] and after['anchor']:
            checks.append(("notification preserves the reading line", abs(after['anchor']['offset'] - before['anchor']['offset']) <= 1, before, after))
        return after

    def accept(text="Please explain the result of the investigation."):
        page.evaluate("window.__delivery='http-first'")
        count = page.evaluate("window.__sends?.length??0")
        page.locator('[data-conversation-input]').fill(text)
        page.locator('[data-conversation-input]').press('Enter')
        page.wait_for_function("n=>window.__sends?.length===n+1 && !!window.__resolveDelivery", arg=count)
        page.evaluate("window.__resolveDelivery()")
        page.locator('[data-conversation-outgoing]').last.wait_for()
        page.wait_for_timeout(380)
        page.evaluate("window.__publishOutcome(window.__sends.at(-1),'prompt')")
        page.wait_for_timeout(200)
        remember_anchor()
        return sample("accepted-send")

    try:
        page.goto(urls[0] + '?lifecycle&running&retirement', wait_until='domcontentloaded')
        page.locator('[data-conversation-entry="complete"]').wait_for()
        page.wait_for_timeout(250)
        page.evaluate(r"""probe=>{
          window.__retirementFrames=[];window.__retirementEvents=[];window.__retirementStop=false;window.__retirementPhase='opening';
          const read=(0,eval)('('+probe+')');
          const sample=()=>{const value=read();if(value)window.__retirementFrames.push(value);if(!window.__retirementStop)requestAnimationFrame(sample);};sample();
          for(const kind of ['wheel','scroll','keydown'])document.addEventListener(kind,event=>{
            if(event.target.closest?.('[data-conversation-thread]'))window.__retirementEvents.push({phase:window.__retirementPhase,ms:performance.now(),type:kind,trusted:event.isTrusted,delta:event.deltaY??null,geometry:read()});
          },true);
        }""", probe)
        initial = accept()
        assert abs(initial['sentTop'] - 32) < 2 and initial['totalTail'] > 100, initial
        assert page.locator('[data-conversation-outgoing]').count() == 1
        # Both excursions run before the discriminating assertions. A base failure
        # therefore retains the repeated behavior instead of a harness-only state.
        for excursion, upward in ((1, -100), (2, -650)):
            before_up = sample(f'excursion-{excursion}-before-up')
            up = wheel(upward, f'excursion-{excursion}-up')
            expected = max(0, before_up['top'] + upward) + up['paddingTop'] - before_up['paddingTop']
            checks.append(('retirement keeps the wheel-selected reading line across inset normalization', abs(up['top'] - expected) <= 1, before_up, up, expected))
            frames = frames_between(before_up, up)
            offsets = [before_up['anchor']['offset'], *[frame['anchor']['offset'] for frame in frames if frame['anchor']], up['anchor']['offset']]
            low, high = before_up['anchor']['offset'], before_up['anchor']['offset'] - upward
            checks.append(('retirement has no transient clamp or second anchor correction', all(low - 1 <= offset <= high + 1 for offset in offsets) and all(b >= a - 1 for a, b in zip(offsets, offsets[1:], strict=False)), before_up, up, offsets))
            checks.append(('retirement preserves the exact wheel-selected reading line', abs(up['anchor']['offset'] - high) <= 1, before_up, up))
            remember_anchor()
            up = sample(f'excursion-{excursion}-up-held')
            down = wheel(1200, f'excursion-{excursion}-down')
            if excursion == 1:
                checks.append(("retired support permits no extra downward empty-space excursion", down['top'] <= up['top'] + 1, up, down))
            pulse(f'excursion-{excursion}-notification')
            page.screenshot(path=str(output / f'retirement-excursion-{excursion}.png'))
        zero = sample('after-two-excursions')
        checks.append(("obsolete tail fully retires after upward navigation", zero['totalTail'] == 0, zero))
        # All primary rAF frames must contain a painted, hittable text line. This
        # rejects an offscreen app host even when its internal geometry is correct.
        primary_frames = [initial, *frames_between(initial, zero), zero]
        checks.append(('both primary excursions stay visibly painted through every ancestor and text hit', all(frame['paint']['ok'] for frame in primary_frames), [{'ms':frame['ms'],'phase':frame['phase'],'paint':frame['paint']} for frame in primary_frames if not frame['paint']['ok']][:2]))
        # Evaluate the reported behavior before any candidate-specific tiny-tail work.
        for check in checks:
            assert check[1], check

        # Compare a leave/return with exactly the same content and real bottom.
        bottom = sample('before-leave')
        page.evaluate('window.__showConversation(false)')
        page.wait_for_timeout(70)
        page.evaluate('window.__showConversation(true)')
        page.locator('[data-conversation-entry="complete"]').wait_for()
        page.wait_for_timeout(300)
        wheel(1200, 'after-return-bottom')
        returned = sample('returned')
        assert returned['totalTail'] == 0, returned
        assert abs(returned['contentHeight'] - bottom['contentHeight']) <= 1, (bottom, returned)
        assert abs(returned['scrollHeight'] - bottom['scrollHeight']) <= 9, (bottom, returned)

        # A fresh send creates legitimate new room. Tiny residuals cross the former
        # external 24px gap; each is actual browser geometry after trusted wheel input.
        accept('Check the small remaining support as well.')
        previous = sample('tiny-start')
        for target in (24, 7, 1, 0):
            current = wheel(-max(1, previous['totalTail'] - target), f'tiny-target-{target}')
            remember_anchor()
            held = sample(f'tiny-held-{target}')
            assert abs(current['totalTail'] - target) <= 1, (target, previous, current)
            if target > 0:
                assert current['totalTail'] > 0, (target, current)
            assert current['totalTail'] <= previous['totalTail'] + 1, (previous, current)
            down = wheel(1200, f'tiny-down-{target}')
            if current['totalTail'] > 0:
                assert down['top'] <= held['top'] + 1, (held, down)
            pulse(f'tiny-notification-{target}')
            previous = sample(f'tiny-settled-{target}')
        assert previous['totalTail'] == 0, previous

        # Stream growth consumes remaining support, then extends below a manual
        # reader. A later notification cannot convert visible newest into authority.
        accept('Watch the reading line while the answer grows.')
        wheel(-100, 'growth-manual-up')
        remember_anchor()
        held = sample('growth-held')
        page.evaluate("window.__publishAnswer('A short answer begins here.')")
        page.wait_for_timeout(250)
        short = sample('growth-before-zero')
        fixed_reading_frames(held, short, 'reply growth before zero')
        assert abs(short['anchor']['offset'] - held['anchor']['offset']) <= 1, (held, short)
        assert 0 < short['totalTail'] < held['totalTail'], (held, short)
        assert abs(short['totalTail'] - round(short['totalTail'])) > .01, short
        fractional = wheel(-int(short['totalTail'] - 7), 'fractional-tiny-up')
        assert 7 <= fractional['totalTail'] < 8.1, fractional
        assert abs(fractional['totalTail'] - round(fractional['totalTail'])) > .01, fractional
        remember_anchor()
        held = sample('fractional-reading-held')
        fractional_down = wheel(1200, 'fractional-tiny-down')
        assert fractional_down['top'] <= held['top'] + 1, (held, fractional_down)
        pulse('fractional-tiny-notification')
        page.evaluate("window.__publishAnswer(Array.from({length:24},(_,i)=>'Answer paragraph '+i+'. The answer extends below the current manual reading line.').join('\\n\\n'))")
        page.wait_for_timeout(300)
        long = sample('growth-to-zero')
        fixed_reading_frames(held, long, 'reply growth to zero')
        assert long['totalTail'] == 0 and abs(long['anchor']['offset'] - held['anchor']['offset']) <= 1, (held, long)
        page.evaluate("window.__publishAnswer(Array.from({length:30},(_,i)=>'Answer paragraph '+i+'. The answer extends below the current manual reading line.').join('\\n\\n'))")
        page.wait_for_timeout(300)
        after = pulse('growth-after-zero')
        fixed_reading_frames(long, after, 'reply growth after zero')
        assert after['totalTail'] == 0 and abs(after['anchor']['offset'] - held['anchor']['offset']) <= 1, (held, after)
        page.screenshot(path=str(output / 'retirement-manual-growth.png'))

        # A genuine wheel arrival at the actual newest range restores follow,
        # independently of the explicit Latest control.
        wheel(100000, 'genuine-return-to-newest')
        arrival = sample('genuine-newest-held')
        assert arrival['totalTail'] == 0 and abs(arrival['top'] - arrival['scrollHeight'] + arrival['clientHeight']) <= 2, arrival
        page.evaluate("window.__publishAnswer(Array.from({length:32},(_,i)=>'Answer paragraph '+i+'. The answer extends below the current manual reading line.').join('\\n\\n'))")
        page.wait_for_timeout(1000)
        arrival_follow = sample('genuine-arrival-follow')
        assert arrival_follow['top'] > arrival['top'] + 20, (arrival, arrival_follow)
        assert abs(arrival_follow['top'] - arrival_follow['scrollHeight'] + arrival_follow['clientHeight']) <= 3, arrival_follow
        wheel(-400, 'manual-before-explicit-latest')
        remember_anchor()

        # Explicit Latest owns one move and restores ordinary arrival follow.
        latest_start = sample('latest-motion')
        page.get_by_role('button', name='Jump to latest message').click()
        page.wait_for_timeout(500)
        latest = sample('latest-arrived')
        assert latest['totalTail'] == 0 and not latest['latest'], latest
        motion = page.evaluate('(ms)=>window.__retirementFrames.filter(f=>f.ms>=ms)', latest_start['ms'])
        movement = [b for a, b in zip(motion, motion[1:], strict=False) if abs(b['top'] - a['top']) > .5]
        assert len(movement) > 3, motion
        assert all(b['top'] >= a['top'] - 1 for a, b in zip(motion, motion[1:], strict=False)), motion
        assert all(frame['top'] <= latest['top'] + 1 for frame in motion), motion
        assert max(abs(b['top'] - a['top']) for a, b in zip(motion, motion[1:], strict=False)) < abs(latest['top'] - latest_start['top']) * .45, motion
        assert abs(latest['top'] - (latest['scrollHeight'] - latest['clientHeight'])) <= 2, latest
        page.evaluate("window.__publishAnswer(Array.from({length:34},(_,i)=>'Answer paragraph '+i+'. The answer extends below the current manual reading line.').join('\\n\\n'))")
        page.wait_for_timeout(1200)
        following = sample('latest-follows-arrival')
        assert following['top'] > latest['top'] + 20, (latest, following)
        assert abs(following['top'] - (following['scrollHeight'] - following['clientHeight'])) <= 3, following

        # Latest also admits real newest history before travel.
        wheel(-180, 'incomplete-manual')
        page.evaluate('window.__setIncomplete()')
        page.wait_for_timeout(100)
        assert page.locator('[data-conversation-view-key="incomplete-newest"]').count() == 0
        reads = page.evaluate('window.__pageReads')
        page.get_by_role('button', name='Jump to latest message').click()
        page.wait_for_function('n=>window.__pageReads>n', arg=reads)
        page.wait_for_timeout(500)
        page.locator('[data-conversation-view-key="incomplete-newest"]').wait_for()
        incomplete = sample('incomplete-latest-arrived')
        assert incomplete['totalTail'] == 0 and not incomplete['latest'], incomplete
        assert abs(incomplete['top'] - (incomplete['scrollHeight'] - incomplete['clientHeight'])) <= 2, incomplete

        # A gesture and then a new send supersede an in-flight Latest operation.
        accept('A later send owns its own answer room.')
        residual = wheel(-80, 'latest-residual-manual')
        assert residual['totalTail'] > 0, residual
        page.get_by_role('button', name='Jump to latest message').click()
        page.wait_for_timeout(500)
        residual_arrival = sample('residual-latest-arrived')
        residual_motion = frames_between(residual, residual_arrival)
        assert residual_arrival['totalTail'] == 0 and not residual_arrival['latest'], residual_arrival
        assert abs(residual_arrival['top'] - residual_arrival['scrollHeight'] + residual_arrival['clientHeight']) <= 2, residual_arrival
        assert all(b['top'] <= a['top'] + 1 for a, b in zip(residual_motion, residual_motion[1:], strict=False)), residual_motion
        assert all(frame['top'] >= residual_arrival['top'] - 1 for frame in residual_motion), residual_motion
        residual_steps = [abs(b['top'] - a['top']) for a, b in zip(residual_motion, residual_motion[1:], strict=False)]
        assert len([step for step in residual_steps if step > .5]) > 3, residual_motion
        assert max(residual_steps) < abs(residual_arrival['top'] - residual['top']) * .45, residual_motion
        accept('Cancel the next Latest motion with a real gesture.')
        page.locator('[data-conversation-thread]').hover()
        # Reserved room retires with this displacement, so the native bottom gap
        # can remain zero. Positive room and Latest establish the required range.
        native_events.append({"kind": "gesture-setup", "barrier": establish_scrollback(page, lambda: page.mouse.wheel(0, -80), min_clearance=0)})
        cancel_setup = sample('latest-residual-before-cancel')
        assert cancel_setup['totalTail'] > 0 and cancel_setup['latest'] and cancel_setup['owner'] == 'manual', ('SETUP_LATEST_GEOMETRY', cancel_setup)
        _arm_motion(page, 'latest', suppress_wheel=control == 'broken-wheel')
        cancel_box = page.locator('[data-conversation-thread]').bounding_box()
        page.get_by_role('button', name='Jump to latest message').click()
        page.mouse.move(cancel_box['x'] + cancel_box['width'] / 2, cancel_box['y'] + cancel_box['height'] / 2)
        page.evaluate("window.__retirementPhase='latest-cancel-gesture'")
        delta = -100000 if control == 'zero-tail' else -1 if control == 'positive-tail' else -120
        wheel_events = _motion_wheel(page, delta, 'SETUP_LATEST_WHEEL', late=control == 'late-wheel', defer_behavior=True)
        native_events.extend(wheel_events)
        predecessor = sample('cancel-predecessor')
        native_events.append({"kind": "predecessor", "totalTail": predecessor['totalTail']})
        if control == 'zero-tail':
            assert predecessor['totalTail'] == 0, ('CONTROL_ZERO_TAIL', predecessor)
        if control == 'positive-tail':
            assert predecessor['totalTail'] > 0, ('CONTROL_POSITIVE_TAIL', predecessor)
        remember_anchor()
        canceled = pulse('latest-canceled-notification')
        page.wait_for_timeout(300)
        after = sample('latest-canceled-settled')
        assert abs(after['top'] - canceled['top']) <= 1, ('BEHAVIOR_WHEEL_CANCELLATION', canceled, after)
        for check in checks:
            assert check[1], ('BEHAVIOR_WHEEL_CANCELLATION', check)
        native_events.append({'kind': 'original-cancellation-checks', 'passed': True})
        wheel_after = next(event for event in wheel_events if event['kind'] == 'after-wheel')
        assert wheel_after['same'] and wheel_after['owner'] == 'manual', ('BEHAVIOR_WHEEL_CANCELLATION_NEW_ONLY', wheel_events)
        # This scenario creates its own answer room and never consumes leftovers
        # from the preceding gesture cancellation.
        accept('Establish independent room before send supersession.')
        page.locator('[data-conversation-thread]').hover()
        native_events.append({"kind": "send-setup", "barrier": establish_scrollback(page, lambda: page.mouse.wheel(0, -80), min_clearance=0)})
        send_setup = sample('supersession-fresh-manual')
        assert send_setup['totalTail'] > 0 and send_setup['latest'] and send_setup['owner'] == 'manual', ('SETUP_SEND_GEOMETRY', send_setup)
        page.evaluate("window.__delivery='http-first';window.__nativeAcceptance=[]")
        count = page.evaluate('window.__sends.length')
        page.locator('[data-conversation-input]').fill('This fresh send supersedes Latest.')
        _arm_motion(page, 'latest')
        page.get_by_role('button', name='Jump to latest message').click()
        _await_motion(page, 'SETUP_LATEST_ACCEPTANCE')
        page.evaluate("window.__nativeRequireAcceptance=true")
        if control == 'broken-supersession':
            page.evaluate('window.__nativeDisableSupersession=true')
        page.locator('[data-conversation-input]').press('Enter')
        page.wait_for_function('n=>window.__sends.length===n+1&&!!window.__resolveDelivery', arg=count)
        if control == 'late-acceptance':
            page.wait_for_function("!window.__nativeMotion.capture('late').motion")
        page.evaluate('window.__resolveDelivery()')
        page.wait_for_function('window.__nativeAcceptance.length===1')
        acceptance = page.evaluate('window.__nativeAcceptance[0]')
        native_events.append(acceptance)
        assert acceptance['valid'], ('SETUP_LATEST_ACCEPTANCE', acceptance)
        # Check supersession while the old Latest intent can still own the pane.
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        send_owner = page.locator('[data-conversation-thread]').get_attribute('data-conversation-position-owner')
        page.locator('[data-conversation-outgoing]').last.wait_for()
        page.wait_for_timeout(380)
        page.evaluate("window.__publishOutcome(window.__sends.at(-1),'prompt')")
        page.wait_for_timeout(200)
        remember_anchor()
        latest_send = sample('accepted-send')
        assert abs(latest_send['sentTop'] - 32) < 2 and latest_send['totalTail'] > 100, ('BEHAVIOR_SEND_SUPERSESSION', latest_send)
        page.wait_for_timeout(300)
        assert abs(sample('new-send-still-owns')['sentTop'] - 32) < 2
        native_events.append({'kind': 'original-supersession-checks', 'passed': True})
        assert send_owner != 'latest', ('BEHAVIOR_SEND_SUPERSESSION_NEW_ONLY', send_owner, acceptance)
        if control:
            for check in checks:
                assert check[1], check
            return

        # A second trusted gesture and an accepted send supersede queued retirement.
        # No wait between wheel and send lets already-queued observer work overlap.
        wheel(-50, 'retirement-new-gesture', wait=0)
        replacement = accept('The new send replaces pending retirement work.')
        assert abs(replacement['sentTop'] - 32) < 2 and replacement['totalTail'] > 100, replacement
        pulse('replacement-send-notification')
        assert abs(sample('replacement-send-held')['sentTop'] - 32) < 2

        # A layout invalidation during manual retirement gets fresh measurements.
        wheel(-90, 'resize-manual')
        page.set_viewport_size({'width': 1100, 'height': 820})
        page.wait_for_timeout(200)
        resized = sample('resized')
        pulse('resize-notification')
        assert sample('resize-settled')['totalTail'] <= resized['totalTail'] + 1
        # Content shrink races retirement remeasurement. Existing layout rules own
        # any unavoidable clamp; retirement must settle once and never regrow.
        page.evaluate("window.__publishAnswer('The answer is now short.')")
        page.wait_for_timeout(220)
        shrunk = sample('content-shrunk')
        pulse('shrink-notification')
        assert sample('shrink-settled')['totalTail'] <= shrunk['totalTail'] + 1

        # Pause animation while trusted Home cancels an accepted send at history
        # top. Inset normalization cannot require a negative preserving offset.
        from datetime import UTC, datetime, timedelta
        wheel(-100000, 'top-before-send')
        clock_start = datetime(2026, 10, 27, tzinfo=UTC)
        page.clock.install(time=clock_start)
        page.clock.pause_at(clock_start + timedelta(seconds=1))
        page.evaluate("window.__delivery='http-first'")
        count = page.evaluate('window.__sends.length')
        page.locator('[data-conversation-input]').fill('Cancel this send placement at the history top.')
        page.locator('[data-conversation-input]').press('Enter')
        page.wait_for_function('n=>window.__sends.length===n+1', arg=count)
        page.evaluate('window.__resolveDelivery()')
        page.locator('[data-conversation-outgoing]').last.wait_for()
        page.locator('[data-conversation-thread]').press('Home')
        page.clock.run_for(100)
        top = sample('top-inset-deferred')
        assert top['top'] == 0 and top['paddingTop'] == 32 and top['totalTail'] == 0, top
        page.evaluate('window.__pulse()')
        page.clock.run_for(100)
        top_after = sample('top-notification-deferred')
        assert top_after['top'] == 0 and top_after['paddingTop'] == 32 and top_after['totalTail'] == 0, top_after
        assert not errors, errors
        for check in checks:
            assert check[1], check
    finally:
        data = page.evaluate('window.__retirementStop=true;({frames:window.__retirementFrames??[],events:window.__retirementEvents??[]})')
        data.update(samples=samples, checks=checks, errors=errors, seconds=round(time.perf_counter() - started, 3), newHosts=0)
        data['nativeControl'] = {'control': control, 'events': native_events, 'motion': page.evaluate('window.__nativeMotion?.events??[]')}
        data['video'] = str(page.video.path())
        page.screenshot(path=str(output / 'retirement-final.png'))
        print('REPLY_SPACE_RETIREMENT_PROOF ' + json.dumps({'seconds':data['seconds'],'frames':len(data['frames']),'samples':len(samples)}), flush=True)
        page.close()
        (output / 'reply-space-retirement-proof.json').write_text(json.dumps(data, indent=2))


def run(browser, urls):
    run_retirement(browser, urls)

    INPUT = "[data-conversation-input]"

    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.set_default_timeout(5_000)
    page.goto([__file__, *urls][1], wait_until="domcontentloaded")
    page.wait_for_function("window.__tailOpened?.() === 1")

    assert page.locator(INPUT).get_attribute('placeholder') == 'Message Worker...'

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
    page.wait_for_timeout(100)
    assert page.get_by_text("in flight when the page went", exact=True).count() == 0
    assert page.locator("[data-conversation-held-stack]").count() == 0

    # The server comes back. Nothing asked it yet, so nothing has been told — and then the
    # read happens, the record turns out not to have the message, and only now is it true
    # to say that nobody ever said whether it arrived.
    page.evaluate("window.__theServerIsDown(false)")
    page.evaluate("window.__setServerReachable(true)")
    page.wait_for_function("Array.from(document.querySelectorAll('[data-conversation-outgoing]')).some(node => node.innerText.includes('no answer came'))")
    # The same real send path proves destination selection and record/HTTP order.
    # An unresolved send is durable but has no provisional queue or thread bubble.
    import json
    import os
    import time
    from pathlib import Path

    started = time.perf_counter()
    proof = []
    output = Path(os.environ.get("PANELS_E2E_FAILURE_DIR", "/tmp"))
    output.mkdir(parents=True, exist_ok=True)
    for outcome in ("unanswered", "refused", "uncertain", "prompt", "accepted", "restored", "record-first", "http-first", "queued", "cancelled"):
        case = browser.new_page(viewport={"width": 1100, "height": 800}, reduced_motion="no-preference")
        case.set_default_timeout(5_000)
        if outcome == "accepted":
            case.clock.install()
        case.goto(urls[0] + "?lifecycle&running", wait_until="domcontentloaded")
        case.wait_for_function("window.__tailOpened?.() === 1")
        case.locator('[data-conversation-entry="complete"]').wait_for()
        if outcome=='accepted':
            case.locator('[data-conversation-lens-choice="full"]').click()
        case.wait_for_timeout(300)
        case.evaluate("outcome => window.__delivery = outcome", outcome)
        case.evaluate("""() => {
          window.__deliveryFrames=[];window.__deliveryStop=false;window.__sentBubble=null;
          const start=performance.now();
          const sample=()=>{
            const thread=document.querySelector('[data-conversation-thread]');
            const nodes=[...document.querySelectorAll('[data-conversation-outgoing]')];
            const bubble=nodes.find(n=>n.closest('[data-conversation-thread]'));
            if(bubble&&!window.__sentBubble)window.__sentBubble=bubble;
            window.__deliveryFrames.push({ms:performance.now()-start,count:nodes.length,
              queue:!!document.querySelector('[data-conversation-held-stack]'),
              top:bubble?bubble.getBoundingClientRect().top-thread.getBoundingClientRect().top:null,
              scrollTop:thread?.scrollTop??null,same:!bubble||bubble===window.__sentBubble});
            if(!window.__deliveryStop)requestAnimationFrame(sample);
          };requestAnimationFrame(sample);
        }""")
        case.locator(INPUT).fill("Have a look at the bundle and tell me what changed.")
        case.locator(INPUT).press("Enter")
        case.wait_for_function("window.__sends?.length === 1 && !!window.__resolveDelivery")
        case.wait_for_timeout(100)
        pending = case.evaluate("({bubbles:document.querySelectorAll('[data-conversation-outgoing]').length,queue:!!document.querySelector('[data-conversation-held-stack]')})")
        assert pending == {"bubbles": 0, "queue": False}, (outcome, pending)
        if outcome == "record-first":
            case.evaluate("window.__publishOutcome(window.__sends[0], 'prompt')")
            case.locator('[data-conversation-outgoing]').wait_for()
            case.wait_for_timeout(350)
            case.evaluate("window.__resolveDelivery()")
        else:
            if outcome == 'cancelled':
                case.locator('[data-conversation-thread]').hover()
                _arm_motion(case, 'sent')
            case.evaluate("window.__resolveDelivery()")
        if outcome == "queued":
            case.locator('[data-conversation-held-stack]').wait_for()
            assert case.locator('[data-conversation-thread] [data-conversation-outgoing]').count() == 0
            case.wait_for_timeout(100)
            frames=case.evaluate('window.__deliveryStop=true;window.__deliveryFrames')
            assert all(row['count'] <= 1 for row in frames), frames
            proof.append({'outcome':outcome,'pending':pending,'frames':frames})
            case.close()
            continue
        case.locator('[data-conversation-outgoing]').wait_for()
        if outcome == 'cancelled':
            _motion_wheel(case, -160, 'SETUP_ACCEPTED_SEND_WHEEL')
        case.wait_for_timeout(450)
        if outcome == "http-first":
            case.evaluate("window.__publishOutcome(window.__sends[0], 'prompt')")
            case.wait_for_timeout(350)
        settled = case.evaluate("""() => {const n=window.__sentBubble,t=n.closest('[data-conversation-thread]');return {same:n===document.querySelector('[data-conversation-outgoing]'),top:n.getBoundingClientRect().top-t.getBoundingClientRect().top,bottom:t.getBoundingClientRect().bottom-n.getBoundingClientRect().bottom,room:t.querySelector('[data-conversation-reserved-space]')?.getBoundingClientRect().height??0,text:n.innerText};}""")
        frames=case.evaluate('window.__deliveryStop=true;window.__deliveryFrames')
        assert settled["same"] and all(row['same'] and row['count'] <= 1 and not row['queue'] for row in frames), (outcome, settled, frames)
        assert case.locator("[data-conversation-error], [data-conversation-fate]").count() == 0
        if outcome in ("prompt", "accepted", "restored", "record-first", "http-first"):
            assert abs(settled["top"] - 32) < 2 and settled["room"] > 100, (outcome, settled)
            visible=[row for row in frames if row['top'] is not None]
            moving=[row for a,row in zip(visible,visible[1:],strict=False) if abs(row['scrollTop']-a['scrollTop'])>.5]
            assert len(moving)>3, (outcome, visible)
            duration=moving[-1]['ms']-visible[0]['ms']
            assert 150 <= duration <= 340, (outcome, duration, moving)
            assert all(b['scrollTop'] >= a['scrollTop']-1 for a,b in zip(visible,visible[1:],strict=False)), (outcome, visible)
            # Late browser-owned layout cannot start another animated chase.
            case.evaluate("""()=>{const n=window.__sentBubble;const first=n.closest('[data-conversation-thread]').querySelector('[data-conversation-row]');const image=document.createElement('img');image.style.cssText='display:block;width:1px;height:180px';first.append(image);window.__lateLayout=image;}""")
            case.wait_for_timeout(100)
            gap=case.evaluate("window.__sentBubble.getBoundingClientRect().top-window.__sentBubble.closest('[data-conversation-thread]').getBoundingClientRect().top")
            assert abs(gap-32)<2, (outcome,gap)
            case.evaluate("window.__lateLayout.remove()")
        elif outcome == "cancelled":
            assert abs(settled['top']-32)>20,settled
            after=case.evaluate('window.__sentBubble.getBoundingClientRect().top')
            case.wait_for_timeout(300)
            assert abs(case.evaluate('window.__sentBubble.getBoundingClientRect().top')-after)<2
        else:
            assert settled["room"] == 0 and abs(settled["bottom"] - 12) < 2, (outcome, settled)
            assert {"unanswered": "no answer came", "refused": "not delivered", "uncertain": "delivery uncertain"}[outcome] in settled["text"], (outcome, settled)
        if outcome == "unanswered":
            case.evaluate("""()=>{window.__delivery='queued';window.__retryQueueFrames=[];window.__retryQueueStop=false;
              const sample=()=>{window.__retryQueueFrames.push({queue:!!document.querySelector('[data-conversation-held-stack]'),thread:document.querySelectorAll('[data-conversation-thread] [data-conversation-outgoing]').length});if(!window.__retryQueueStop)requestAnimationFrame(sample);};sample();}""")
            case.locator('[data-conversation-held-send-again]').click()
            case.wait_for_function('window.__sends.length===2')
            case.evaluate('window.__resolveDelivery()')
            case.locator('[data-conversation-held-stack]').wait_for()
            case.wait_for_timeout(350)
            retry_frames=case.evaluate('window.__retryQueueStop=true;window.__retryQueueFrames')
            assert all(not row['queue'] or row['thread']==0 for row in retry_frames),retry_frames
            assert case.locator('[data-conversation-thread] [data-conversation-outgoing]').count()==0
            proof.append({'outcome':'unanswered retry confirmed queue','frames':retry_frames})
        if outcome == "restored":
            case.reload(wait_until="domcontentloaded")
            case.locator('[data-conversation-entry="complete"]').wait_for()
            case.wait_for_function("document.querySelector('[data-conversation-outgoing]')?.innerText.includes('no answer came')")
            case.wait_for_timeout(400)
            assert case.locator('[data-conversation-reserved-space]').count() == 0
        if outcome == "accepted":
            case.screenshot(path=str(output / 'accepted-send-held.png'))
            # A late transport timeout exposes actions in the same lifted bubble.
            case.clock.fast_forward(181_000)
            case.wait_for_function("window.__sentBubble.innerText.includes('no answer came')")
            case.wait_for_timeout(300)
            assert abs(case.evaluate("window.__sentBubble.getBoundingClientRect().top-window.__sentBubble.closest('[data-conversation-thread]').getBoundingClientRect().top")-32)<2
            case.locator('[data-conversation-held-send-again]').click()
            case.wait_for_function("window.__sends.length===2")
            assert case.evaluate("window.__sentBubble===document.querySelector('[data-conversation-outgoing]')")
            case.evaluate('window.__resolveDelivery()')
            case.wait_for_timeout(350)
            case.evaluate("window.__publishOutcome(window.__sends[1], 'prompt')")
            case.wait_for_function("!window.__sentBubble.innerText.includes('no answer came')")
            case.wait_for_timeout(350)
            assert case.evaluate("window.__sentBubble === document.querySelector('[data-conversation-outgoing]')")
            assert abs(case.evaluate("window.__sentBubble.getBoundingClientRect().top-window.__sentBubble.closest('[data-conversation-thread]').getBoundingClientRect().top") - 32) < 2
        if outcome=='accepted':
            case.evaluate("window.__publishStream('The reply starts here.')")
            case.wait_for_timeout(300)
            short=case.evaluate("""()=>{const t=document.querySelector('[data-conversation-thread]');return {gap:window.__sentBubble.getBoundingClientRect().top-t.getBoundingClientRect().top,room:t.querySelector('[data-conversation-reserved-space]')?.getBoundingClientRect().height??0};}""")
            assert abs(short['gap']-32)<2 and short['room']>100,short
            crossing=case.evaluate(r"""()=>new Promise(resolve=>{
              const t=document.querySelector('[data-conversation-thread]');const start=performance.now(),before=t.scrollHeight,frames=[];
              window.__publishStream('\n\n'+Array.from({length:14},(_,i)=>'Crossing answer room '+i+'. The arriving reply continues over several lines.').join('\n\n'));
              const sample=()=>{const stream=t.querySelector('.c2-streaming');frames.push({ms:performance.now()-start,top:t.scrollTop,growth:t.scrollHeight-before,room:t.querySelector('[data-conversation-reserved-space]')?.getBoundingClientRect().height??0,gap:stream?t.getBoundingClientRect().bottom-stream.getBoundingClientRect().bottom:null});if(performance.now()-start<2600)requestAnimationFrame(sample);else resolve(frames);};sample();
            })""")
            moving=[b for a,b in zip(crossing,crossing[1:],strict=False) if b['top']-a['top']>.5]
            assert len(moving)>3 and moving[-1]['ms']>300,crossing
            assert crossing[-1]['room']==0 and -2<=crossing[-1]['gap']<=18,crossing[-1]
            proof.append({'outcome':'stream crosses held answer room','short':short,'frames':crossing})
        proof.append({'outcome':outcome,'pending':pending,'settled':settled,'frames':frames})
        print("delivery-lifecycle", outcome, pending, settled)
        case.close()
    receipt={'cases':proof,'seconds':round(time.perf_counter()-started,3),'newHosts':0}
    (output/'delivery-placement-proof.json').write_text(json.dumps(receipt,indent=2))
    print('DELIVERY_PLACEMENT_PROOF '+json.dumps({'seconds':receipt['seconds'],'outcomes':len(proof)}),flush=True)
    page.goto(urls[0]+'?unstarted',wait_until='domcontentloaded')
    page.locator(INPUT).wait_for()
    assert page.locator(INPUT).get_attribute('placeholder')=='Send the first message to start it...'
    page.goto(urls[0]+'?placeholder=Write+a+custom+message',wait_until='domcontentloaded')
    page.locator(INPUT).wait_for()
    assert page.locator(INPUT).get_attribute('placeholder')=='Write a custom message'
    pass  # Shared runner owns the browser lifecycle.

    print("live-conversation-unanswered-send-browser.test.mjs: all assertions passed")
