# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

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
            case.wait_for_timeout(80)
            case.locator('[data-conversation-thread]').hover()
            case.mouse.wheel(0,-160)
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
