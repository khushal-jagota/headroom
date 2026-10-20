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
    page.evaluate("window.__load(true)")
    page.wait_for_function("document.querySelector('[data-entering]')")
    assert page.evaluate("window.__status === document.querySelector('[data-fixed] [role=status]')")
    assert page.evaluate("window.__statusChanges") == ["", "Loading document"]
    assert page.evaluate("!window.__status.closest('[aria-busy=true]')")
    assert page.locator('[data-fixed] [aria-busy=true]').count() == 1
    # Control animation time rather than depend on host scheduling speed.
    interrupted = page.evaluate("""async () => {
      const layer = document.querySelector('[data-fixed] .skeleton');
      const entrance = layer.getAnimations()[0];
      entrance.pause(); entrance.currentTime = 60;
      const before = +getComputedStyle(layer).opacity;
      window.__load(false);
      await new Promise(requestAnimationFrame);
      return {before, from:+layer.style.getPropertyValue('--skeleton-from')};
    }""")
    assert 0 < interrupted["before"] < 1, interrupted
    assert abs(interrupted["before"] - interrupted["from"]) < 0.001, interrupted
    page.wait_for_function("!document.querySelector('[data-leaving]')")
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

    measurements = []
    for width in [1100, 390]:
        page.set_viewport_size({"width": width, "height": 900})
        for long in [False, True]:
            page.evaluate("window.__load(true)")
            page.wait_for_function("document.querySelector('[data-entering]')")
            before = page.evaluate("""() => ({
              control:document.querySelector('[data-control]').getBoundingClientRect().y,
              flow:document.querySelector('[data-flow]').getBoundingClientRect().height,
              footer:document.querySelector('footer').getBoundingClientRect().y})""")
            assert before["flow"] == 112, before
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
            measurements.append({"width": width, "long": long, "before": before, "after": after,
                                 "intrinsic_growth": after["footer"] - before["footer"]})
    output = os.environ.get("PANELS_SKELETON_ARTIFACT_DIR")
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "geometry.json").write_text(json.dumps(measurements, indent=2))
        page.evaluate("window.__load(true)")
        page.wait_for_timeout(200)
        page.screenshot(path=str(directory / "skeleton-narrow.png"), full_page=True)
    assert not errors, errors
    page.close()
