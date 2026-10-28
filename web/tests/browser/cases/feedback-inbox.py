# ruff: noqa: E501
"""One persistent feedback composer, entered through desktop or compact navigation."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect


def run(browser, urls):

    page = browser.new_page(viewport={"width": 1280, "height": 844})
    # Only capture's bounded count is global; opening it must not read full history.
    page.goto(urls[0] + "#/unknown", wait_until="networkidle")
    trigger = page.locator("[data-feedback-trigger]")
    trigger.click()
    expect(page.locator("[data-feedback-capture]")).to_have_count(1)
    expect(page.locator("[data-feedback-view]")).to_contain_text("1 open")
    shell_requests = page.evaluate("window.__requests()")
    assert any(r["path"] == "/api/resource-body?key=%2Fapi%2Ffeedback%2Fcount" for r in shell_requests)
    assert not any(r["path"] == "/api/feedback" for r in shell_requests)
    # The count is the inbox entry, replacing the former Feedback icon badge.
    assert page.locator('[aria-label="Feedback"] .count-badge').count() == 0
    page.locator("[data-feedback-view]").click()
    page.wait_for_function("location.hash === '#/feedback'")
    expect(page.locator('[data-feedback-note="f_1"]')).to_be_visible()
    assert any(r["path"] == "/api/feedback" for r in page.evaluate("window.__requests()"))

    # Rendered status owns its transition lifecycle; pure resolution cannot prove
    # healthy removal, rapid reversal or reduced-motion card behavior.
    status = page.locator('.rail-foot .status-card')
    for state, word in [("preparing", "Deploying"), ("problem", "Problem"), ("restarting", "Deploying")]:
        page.evaluate("state => window.__deployment(state)", state)
        expect(status).to_have_text(word)
    page.evaluate("window.__deployment('idle')")
    expect(status).to_have_count(0)
    page.evaluate("window.__connection('reconnecting')")
    expect(status).to_have_text("Reconnecting")
    page.evaluate("window.__connection('connected')")
    expect(status).to_have_count(0)
    page.evaluate("window.__deployment('preparing')")
    expect(status).to_have_text("Deploying")
    page.evaluate("async () => { await window.__deployment('idle'); await window.__deployment('problem'); }")
    expect(status).to_have_text("Problem")
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("window.__deployment('idle')")
    expect(status).to_have_count(0)
    page.evaluate("window.__deployment('preparing')")
    expect(status).to_have_text("Deploying")
    page.emulate_media(reduced_motion="no-preference")

    # Closing keeps a device-local draft and restores it.
    trigger = page.locator("[data-feedback-trigger]")
    trigger.click()
    input_box = page.locator("[data-feedback-input]")
    assert input_box.evaluate("element => element === document.activeElement")
    assert page.locator(".fb-context .chip").inner_text() == "Feedback"
    input_box.fill("unfinished")
    page.keyboard.press("Escape")
    assert page.evaluate("localStorage.getItem('panels.feedback.draft')") == "unfinished"
    assert trigger.evaluate("element => element === document.activeElement")
    page.reload(wait_until="networkidle")
    page.locator('[data-feedback-note="f_1"]').wait_for()
    trigger = page.locator("[data-feedback-trigger]")
    assert page.evaluate("localStorage.getItem('panels.feedback.draft')") == "unfinished"
    trigger.click()
    input_box = page.locator("[data-feedback-input]")
    assert input_box.input_value() == "unfinished"

    expect(page.locator('.rail-foot [data-shell-status]')).to_contain_text("Deploying")
    page.wait_for_function("""() => {
      const capture=document.querySelector('[data-feedback-capture]')?.getBoundingClientRect();
      const foot=document.querySelector('.rail-foot')?.getBoundingClientRect();
      return capture && foot && capture.bottom <= foot.top + 1;
    }""")
    capture = page.locator('[data-feedback-capture]').bounding_box()
    foot = page.locator('.rail-foot').bounding_box()
    assert capture and foot and capture['y'] + capture['height'] <= foot['y'] + 1
    evidence = Path(os.environ["PANELS_SIDEBAR_ARTIFACT_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(animations="disabled", path=str(evidence / "feedback-desktop.png"))

    # Shift+Enter inserts a line; Enter saves with the current route context.
    input_box.fill("first line")
    page.keyboard.press("Shift+Enter")
    input_box.type("second line")
    page.keyboard.press("Enter")
    page.locator("[data-feedback-toast]").wait_for()
    toast_text = page.locator("[data-feedback-toast]").inner_text()
    assert toast_text == "Saved to Feedback · View", repr(toast_text)
    posted = [
        r
        for r in page.evaluate("window.__requests()")
        if r["path"] == "/api/feedback" and r["method"] == "POST"
    ][-1]
    assert posted["body"] == {
        "text": "first line\nsecond line",
        "page_address": "#/feedback",
        "page_label": "Feedback",
    }

    # The toast action returns from another screen to the saved canonical note.
    page.evaluate("location.hash = '#/unknown'")
    page.locator("[data-feedback-toast] a", has_text="View").click()
    page.wait_for_function("location.hash === '#/feedback'")
    page.locator("[data-feedback-note]", has_text="first line\nsecond line").wait_for()

    # Failure leaves the text and changes the retry action.
    trigger.click()
    input_box.fill("fail")
    page.keyboard.press("Enter")
    page.locator(".error-line").wait_for()
    assert input_box.input_value() == "fail"
    assert page.locator("[data-feedback-save]").inner_text() == "Try again"
    page.keyboard.press("Escape")

    # Dismiss moves immediately; Undo reopens through the same canonical action.
    page.locator('[data-feedback-note="f_1"] [data-feedback-action="dismiss"]').click()
    page.locator("[data-feedback-toast] button", has_text="Undo").click()
    page.locator('[data-feedback-note="f_1"]').wait_for()
    paths = [r["path"] for r in page.evaluate("window.__requests()")]
    assert "/api/feedback/f_1/dismiss" in paths
    assert "/api/feedback/f_1/reopen" in paths

    # At phone size the capture becomes a touch-safe sheet above the bottom nav.
    phone_context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    phone = phone_context.new_page()
    phone.goto([__file__, *urls][1] + "#/feedback", wait_until="networkidle")
    more = phone.locator('[data-screen="more"]')
    more.click()
    phone.locator('[data-shell-more-menu] [aria-label="Feedback"]').click()
    expect(phone.locator('[data-shell-more-menu]')).to_have_count(0)
    expect(phone.locator('[data-feedback-capture]')).to_have_count(1)
    phone.wait_for_function("""() => {
      const sheet=document.querySelector('[data-feedback-capture]')?.getBoundingClientRect();
      const nav=document.querySelector('.workspace-mobile-nav')?.getBoundingClientRect();
      return sheet && nav && sheet.bottom <= nav.top + 1;
    }""")
    # Capture's scrim belongs below the persistent bar, so More remains reachable.
    assert more.evaluate("""node => {
      const box=node.getBoundingClientRect();
      return node.contains(document.elementFromPoint(box.left+box.width/2, box.top+box.height/2));
    }"""), "Feedback scrim blocks the compact bar"
    sheet = phone.locator("[data-feedback-capture]").bounding_box()
    nav = phone.locator(".workspace-mobile-nav").bounding_box()
    assert sheet and nav and sheet["y"] + sheet["height"] <= nav["y"] + 1, (sheet, nav)
    phone_input = phone.locator("[data-feedback-input]")
    assert phone_input.get_attribute("enterkeyhint") == "done"
    assert phone_input.evaluate("element => getComputedStyle(element).fontSize") == "16px"
    assert phone.locator("[data-feedback-save]").is_visible()
    evidence = Path(os.environ["PANELS_SIDEBAR_ARTIFACT_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)
    phone.screenshot(animations="disabled", path=str(evidence / "feedback-mobile.png"))
    phone.keyboard.press("Escape")
    expect(more).to_be_focused()
    # A composer remains the same instance and keeps its draft through a width change.
    phone.locator('[data-screen="more"]').click()
    phone.locator('[data-shell-more-menu] [aria-label="Feedback"]').click()
    phone_input.fill("resized draft")
    phone.set_viewport_size({"width": 900, "height": 844})
    expect(phone_input).to_have_value("resized draft")
    phone.keyboard.press("Escape")
    expect(phone.locator('[data-feedback-trigger]')).to_be_focused()
    phone_context.close()

    # A resolved back_up state must produce a visible card, then remove itself
    # on expiry without another fetch. Reuse the same shell and browser host.
    page.clock.install()
    page.evaluate("window.__deployment('back_up')")
    expect(status).to_have_text("Deployed")
    expect(status).to_be_visible()
    assert status.locator('.status-mark').evaluate("node => getComputedStyle(node).animationName") == "none"
    page.screenshot(animations="disabled", path=str(evidence / "deploy-success-desktop.png"))
    page.evaluate("window.__connection('reconnecting')")
    expect(status).to_have_text("Reconnecting")
    page.evaluate("window.__connection('connected')")
    expect(status).to_have_text("Deployed")
    # An older bundle still sees the ready
    # update above Deployed. Neither event restarts the recorded success window.
    page.evaluate("window.__release('shipped')")
    expect(status).to_contain_text("New version ready")
    page.evaluate("window.__release('booted')")
    expect(status).to_have_text("Deployed")
    page.clock.fast_forward(300_001)
    expect(status).to_have_count(0)
    # The board no longer mounts the recovery display or reads its removed API.
    expect(page.locator('[data-deployment-interruptions]')).to_have_count(0)
    assert not any("deployment-interruptions" in request["path"]
                   for request in page.evaluate("window.__requests()"))
    page.screenshot(animations="disabled", path=str(evidence / "shell-without-interruption-panel.png"))
    pass  # Shared runner owns the browser lifecycle.

    # The update prompt is now an occupant of this same slot, so only the rendered
    # surface can show which card wins, that the action is reachable, and that the
    # held sentence changes the rail foot's height without disturbing the rail.
    # The deploy fixture has already expired above, so the slot starts empty.
    expect(status).to_have_count(0)
    page.evaluate("window.__release('shipped')")
    expect(status).to_contain_text("New version ready")
    expect(status.locator(".status-action")).to_have_text("Update")
    assert status.evaluate("node => node.tagName") == "BUTTON"
    # A failed deploy displaces it: a rolled back release still differs from the boot.
    page.evaluate("window.__deployment('problem')")
    expect(status).to_have_text("Problem")
    assert status.evaluate("node => node.tagName") == "DIV"
    page.evaluate("window.__deployment('idle')")
    expect(status).to_contain_text("New version ready")
    expect(status.locator(".status-action")).to_have_text("Update")
    page.screenshot(animations="disabled", path=str(evidence / "update-ready-desktop.png"))

    # Asking for the reload while a message is composed holds it, and says why.
    page.evaluate(
        "document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:true}}))"
    )
    status.click()
    expect(status).to_contain_text("Update applies when this message is safe")
    expect(status.locator(".status-action")).to_have_count(0)
    assert status.evaluate("node => node.tagName") == "DIV"
    # The long copy wraps inside the rail rather than widening or truncating it.
    rail = page.locator(".board-workspace-left")
    assert rail.evaluate("node => node.scrollWidth <= node.clientWidth"), "Held update widened the rail"
    assert status.evaluate(
        "node => node.scrollWidth <= node.clientWidth + 1"
    ), "Held update truncated its own sentence"
    # The taller card pushes the rail foot open rather than overlapping its neighbour.
    page.wait_for_function("""() => {
      const utilities=document.querySelector('.rail-utilities')?.getBoundingClientRect();
      const card=document.querySelector('.rail-foot .status-card')?.getBoundingClientRect();
      return utilities && card && card.bottom <= utilities.top + 1;
    }""")
    page.screenshot(animations="disabled", path=str(evidence / "update-held-desktop.png"))

    # Both breakpoints: on mobile the same card is a band inside the bottom bar.
    page.set_viewport_size({"width": 390, "height": 844})
    band = page.locator(".compact-bar .status-card")
    expect(band).to_contain_text("Update applies when this message is safe")
    bar = page.locator(".compact-bar-row").bounding_box()
    box = band.bounding_box()
    assert box and bar and box["y"] + box["height"] <= bar["y"] + 1, (box, bar)
    assert box["width"] >= 389, box
    page.screenshot(animations="disabled", path=str(evidence / "update-held-mobile.png"))

    # The hold releases the moment the message is safe, and only then.
    page.evaluate("window.__heldPage = true")
    with page.expect_navigation(wait_until="networkidle"):
        page.evaluate(
            "document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:false}}))"
        )
    page.wait_for_function("() => window.__heldPage === undefined")
    # A new window clears the marker before its module installs the drivers.
    page.wait_for_function("() => typeof window.__deployment === 'function'")

    # The approved plan's evidence: every state of the one slot, at both breakpoints.
    # The page reloaded above, so this starts from a fresh boot with no update served.
    for label, drive in [
        ("deploying", "window.__deployment('preparing')"),
        ("problem", "window.__deployment('problem')"),
        ("update-ready", "window.__deployment('idle'); window.__release('shipped')"),
    ]:
        page.evaluate(drive)
        for breakpoint_, size in [("desktop", (1280, 844)), ("mobile", (390, 844))]:
            page.set_viewport_size({"width": size[0], "height": size[1]})
            shown = page.locator(
                ".rail-foot .status-card" if breakpoint_ == "desktop" else ".compact-bar .status-card"
            )
            expect(shown).to_be_visible()
            page.screenshot(animations="disabled", path=str(evidence / f"state-{label}-{breakpoint_}.png"))
    # Held needs the action, which only the visible breakpoint's card carries.
    page.evaluate(
        "document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:true}}))"
    )
    for breakpoint_, size in [("desktop", (1280, 844)), ("mobile", (390, 844))]:
        page.set_viewport_size({"width": size[0], "height": size[1]})
        shown = page.locator(
            ".rail-foot .status-card" if breakpoint_ == "desktop" else ".compact-bar .status-card"
        )
        if shown.evaluate("node => node.tagName") == "BUTTON":
            shown.click()
        expect(shown).to_contain_text("Update applies when this message is safe")
        page.screenshot(animations="disabled", path=str(evidence / f"state-update-held-{breakpoint_}.png"))

    # A swap between two open occupants holds the slot at grid-template-rows:1fr, so the
    # row glide cannot carry it. At the rail's minimum width the held sentence wraps the
    # most, which is where an unanimated intrinsic height change is worst.
    page.set_viewport_size({"width": 1280, "height": 844})
    page.evaluate("localStorage.setItem('panels.workspace.rail-width','232')")
    page.reload(wait_until="networkidle")
    page.locator(".rail-foot").wait_for()
    page.evaluate("""() => {
      window.__slotActions = {
        held: () => {
          document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:true}}));
          document.querySelector('.rail-foot .status-card').click();
        },
        problem: () => window.__deployment('problem'),
        ready: () => window.__release('shipped'),
        close: () => { window.__deployment('idle'); window.__release('booted'); },
        returning: () => window.__deployment('problem'),
      };
      window.__slotStart = (duration, action) => {
        const slot = document.querySelector('.rail-foot [data-shell-status]');
        const samples = [];
        const started = performance.now();
        window.__slotDone = false;
        const step = () => {
          const ms = performance.now() - started;
          const utilities = document.querySelector('.rail-utilities').getBoundingClientRect();
          const foot = document.querySelector('.rail-foot').getBoundingClientRect();
          const box = slot.getBoundingClientRect();
          samples.push({
            ms: Math.round(ms),
            height: Number(box.height.toFixed(2)),
            slotBottom: Number(box.bottom.toFixed(2)),
            utilitiesTop: Number(utilities.top.toFixed(2)),
            footTop: Number(foot.top.toFixed(2)),
            closing: slot.hasAttribute('data-closing'),
            effects: slot.getAnimations().length,
            // The collapse is a grid-template-rows transition. Only a scripted effect
            // on the slot can be a height animation that outlived its swap.
            heightEffects: slot.getAnimations()
              .filter((animation) => animation.transitionProperty === undefined).length,
          });
          if (ms < duration) requestAnimationFrame(step);
          else { window.__slotFrames = samples; window.__slotDone = true; }
        };
        requestAnimationFrame(step);
        window.__slotActions[action]();
      };
      // A swap landing on a swap, then a close. Each stage interrupts the one before.
      window.__slotOverlap = (duration, stages) => {
        window.__slotStart(duration, stages[0][1]);
        for (const [at, action] of stages.slice(1)) {
          setTimeout(() => window.__slotActions[action](), at);
        }
      };
    }""")

    def swap(action, duration=560):
        page.evaluate("([d,a]) => window.__slotStart(d,a)", [duration, action])
        page.wait_for_function("window.__slotDone")
        return page.evaluate("window.__slotFrames")

    def smooth(samples, label):
        heights = [sample["height"] for sample in samples]
        low, high = min(heights), max(heights)
        assert high - low > 10, (label, samples)
        between = sum(low + 1 < value < high - 1 for value in heights)
        assert between >= 3, (label, between, samples)
        assert all(abs(current["height"] - previous["height"]) < 6
                   for previous, current in zip(samples, samples[1:], strict=False)), (label, samples)
        # The utility row is pinned to the foot of the rail, so the foot's own top takes
        # the whole distance. It never travels through the card.
        settled = samples[-1]["footTop"] + samples[-1]["height"]
        assert all(abs(sample["footTop"] + sample["height"] - settled) < 2
                   for sample in samples), (label, samples)
        # The card's own box runs past the slot while the slot reveals it. The slot
        # clips that, so the slot's edge is what must stay clear of the utility row.
        assert all(sample["slotBottom"] <= sample["utilitiesTop"] + 1
                   for sample in samples), (label, samples)
        assert abs(heights[-1] - heights[-3]) < 1, (label, samples[-4:])

    page.evaluate("window.__release('shipped')")
    status = page.locator(".rail-foot .status-card")
    expect(status).to_contain_text("New version ready")
    page.screenshot(animations="disabled", path=str(evidence / "narrow-update-ready-desktop.png"))
    ready_to_held = swap("held")
    expect(status).to_contain_text("Update applies when this message is safe")
    smooth(ready_to_held, "ready-to-held")
    page.screenshot(animations="disabled", path=str(evidence / "narrow-update-held-desktop.png"))

    held_to_problem = swap("problem")
    expect(status).to_have_text("Problem")
    smooth(held_to_problem, "held-to-problem")
    page.screenshot(animations="disabled", path=str(evidence / "narrow-problem-desktop.png"))

    # Reduced motion keeps the same occupants and the same hold, without a timed effect.
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("window.__deployment('idle')")
    expect(status).to_contain_text("Update applies when this message is safe")
    reduced = swap("problem", duration=200)
    expect(status).to_have_text("Problem")
    assert all(sample["heightEffects"] == 0 for sample in reduced), reduced
    assert abs(reduced[0]["height"] - reduced[-1]["height"]) < 2, reduced
    page.emulate_media(reduced_motion="no-preference")

    # Overlapping swaps: a cancelled resize settles its promise after its replacement
    # exists, so cleanup that does not check identity releases the wrong handle. The
    # collapse then has nothing to cancel and a stale height effect overrides it.
    def overlap(stages, duration=1100):
        page.evaluate("([d,s]) => window.__slotOverlap(d,s)", [duration, stages])
        page.wait_for_function("window.__slotDone")
        return page.evaluate("window.__slotFrames")

    interrupted: dict[str, object] = {}
    # Clear the earlier hold and the Problem card, so this starts from a ready update.
    page.evaluate("window.__release('booted'); window.__deployment('idle')")
    expect(page.locator(".rail-foot .status-card")).to_have_count(0)
    page.evaluate("window.__release('shipped')")
    expect(status).to_contain_text("New version ready")
    closed_at = 260
    overlapped = overlap([[0, "held"], [120, "problem"], [closed_at, "close"]])
    expect(page.locator(".rail-foot .status-card")).to_have_count(0)
    # The close boundary itself, not only the tail. Cancelling the running height used
    # to reveal the taller intrinsic row here, which showed as a jump up before the fall.
    # The boundary is the slot's own closing flag, not the clock: the close is async, so
    # a timed cutoff lands while the interrupted swap is still legitimately growing.
    began = next(index for index, sample in enumerate(overlapped) if sample["closing"])
    leaving = overlapped[began - 1]
    closing = overlapped[began:]
    # One frame of the interrupted swap's own growth can land between the last open
    # sample and the first closing one, which is about 3px here. The defect this guards
    # was a jump of about 28px, so the slack cannot hide it.
    assert all(sample["height"] <= leaving["height"] + 4 for sample in closing), (leaving, closing)
    assert all(current["height"] <= previous["height"] + 1
               for previous, current in zip(closing, closing[1:], strict=False)), closing
    assert overlapped[-1]["height"] < 1, overlapped[-4:]
    assert overlapped[-1]["heightEffects"] == 0, overlapped[-4:]

    # A swap landing while the row is still entering, left to finish while open. The
    # close in the sequences below reaches zero either way, so it hides this endpoint:
    # only an open ending shows whether the swap aimed at the card or at the row's
    # intermediate size, which it would then snap away from when the effect ended.
    page.evaluate("window.__release('booted'); window.__deployment('idle')")
    expect(page.locator(".rail-foot .status-card")).to_have_count(0)
    entering = overlap([[0, "ready"], [80, "held"]], duration=1200)
    expect(status).to_contain_text("Update applies when this message is safe")
    card = status.bounding_box()
    assert card and abs(entering[-1]["height"] - (card["height"] + 8)) < 1.5, (card, entering[-1])
    assert all(abs(current["height"] - previous["height"]) < 8
               for previous, current in zip(entering, entering[1:], strict=False)), entering
    assert abs(entering[-1]["height"] - entering[-3]["height"]) < 1, entering[-4:]
    assert entering[-1]["heightEffects"] == 0, entering[-4:]
    interrupted["swapDuringEntrance"] = entering

    # A card returning mid-close reverses the row, but the scripted collapse is what is
    # on screen. Cancelling it there exposed the taller grid height before the reopen.
    def continuous(samples, label, interrupted_at):
        # The fixture records elapsed milliseconds for each sample. Keep the
        # eight-pixel bound per 16ms when Chromium delivers a delayed frame.
        assert all(abs(current["height"] - previous["height"])
                   < 8 * max(1, (current["ms"] - previous["ms"]) / 16)
                   for previous, current in zip(samples, samples[1:], strict=False)), (label, samples)
        # The return has to travel. Cancelling the scripted collapse instead handed the
        # height straight to the row, which during the exit delay is still at full size,
        # so the slot arrived within a frame or two rather than easing there.
        resumed = [sample for sample in samples if sample["ms"] > interrupted_at]
        assert len(resumed) >= 8, (label, resumed)
        low = min(resumed[0]["height"], samples[-1]["height"])
        high = max(resumed[0]["height"], samples[-1]["height"])
        travelling = [sample for sample in resumed if low + 1 < sample["height"] < high - 1]
        assert len(travelling) >= 3, (label, low, high, resumed)
        assert samples[-1]["height"] > 1, (label, samples[-4:])
        assert samples[-1]["heightEffects"] == 0, (label, samples[-4:])

    # Settled means the row has finished too, not merely that the card is as tall as the
    # slot. The slot carries the card's bottom margin, so the weaker test passed early.
    settled_open = """() => {
      const slot=document.querySelector('.rail-foot [data-shell-status]');
      const card=document.querySelector('.rail-foot .status-card');
      if (!slot || !card || slot.getAnimations().length) return false;
      const spacing = parseFloat(getComputedStyle(card).marginBottom) || 0;
      return Math.abs(slot.getBoundingClientRect().height
        - (card.getBoundingClientRect().height + spacing)) < 1;
    }"""
    # The close must interrupt a swap, because only then is a scripted collapse what is
    # on screen for the return to interrupt in turn.
    for label, at in [("returnDuringExitDelay", 200), ("returnDuringCollapse", 420)]:
        page.evaluate("window.__release('booted'); window.__deployment('idle')")
        expect(page.locator(".rail-foot .status-card")).to_have_count(0)
        page.evaluate("window.__release('shipped')")
        expect(status).to_contain_text("New version ready")
        page.wait_for_function(settled_open)
        frames = overlap([[0, "held"], [120, "close"], [at, "returning"]], duration=1200)
        expect(status).to_have_text("Problem")
        continuous(frames, label, at)
        interrupted[label] = frames
        card = status.bounding_box()
        assert card and abs(frames[-1]["height"] - (card["height"] + 8)) < 2, (label, card, frames[-1])
    page.evaluate("window.__release('booted'); window.__deployment('idle')")
    expect(page.locator(".rail-foot .status-card")).to_have_count(0)

    # The same overlap under reduced motion runs with no timed effect at all.
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("window.__release('shipped')")
    expect(status).to_contain_text("New version ready")
    reduced_overlap = overlap([[0, "held"], [120, "problem"], [260, "close"]], duration=700)
    expect(page.locator(".rail-foot .status-card")).to_have_count(0)
    assert all(sample["heightEffects"] == 0 for sample in reduced_overlap), reduced_overlap
    assert reduced_overlap[-1]["height"] < 1, reduced_overlap[-4:]
    page.emulate_media(reduced_motion="no-preference")

    # The action is reachable again afterwards: the reopened slot is neither hidden
    # nor inert, so the card takes focus and keeps it.
    page.evaluate(
        "document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:false}}))"
    )
    page.evaluate("window.__release('shipped')")
    expect(status).to_contain_text("New version ready")
    slot_element = page.locator(".rail-foot [data-shell-status]")
    assert slot_element.get_attribute("aria-hidden") is None
    assert slot_element.locator("> div").get_attribute("inert") is None
    status.focus()
    assert status.evaluate("node => node === document.activeElement")
    # The card's own entrance transition is expected. No scripted height effect is.
    assert slot_element.evaluate(
        "node => node.getAnimations().filter(a => a.transitionProperty === undefined).length"
    ) == 0

    (evidence / "status-slot-frames.json").write_text(json.dumps({
        "railWidth": 232,
        "readyToHeld": ready_to_held,
        "heldToProblem": held_to_problem,
        "reducedMotionHeldToProblem": reduced,
        "overlappingSwapsThenClose": overlapped,
        "reducedMotionOverlap": reduced_overlap,
        **interrupted,
    }, indent=2))

    # The hold survives every swap, including the overlapping ones above: the reload
    # lands only once the message is safe. This discards the page, so it runs last.
    page.evaluate(
        "document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:true}}))"
    )
    status.click()
    expect(status).to_contain_text("Update applies when this message is safe")
    page.evaluate("window.__heldNarrow = true")
    page.evaluate(
        "document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:false}}))"
    )
    page.wait_for_function("() => window.__heldNarrow === undefined")
