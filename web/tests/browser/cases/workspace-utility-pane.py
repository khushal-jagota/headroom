# ruff: noqa: E501
"""A utility screen has to be livable inside the Workspace pane.

Two risks that no unit test reaches. A link inside an embedded screen used to navigate
the whole window, which only a real click through the real router shows. And the pane's
scroll has to be its own, which means proving it after loading finishes and the skeleton
is gone, with a real layout and a real scroll height.
"""

import json
import os
import runpy
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "support"))
from vps_metrics_fixture import MetricsFixture  # noqa: E402


def run(browser, urls):
    url = urls[0]
    # Enough Worker types that the Config index is taller than the pane.
    worker_types = [
        "amend_worker", "coding", "debugging", "exploration", "general",
        "initiative_planning", "initiative_review", "new_worker",
        "product_design", "research",
    ]
    skill = {"name": "panels-worker", "description": "Shared", "markdown_body": "Body"}
    launch = {"employee_backend": "codex", "employee_launch_model": None, "employee_launch_reasoning_effort": None}
    workers = [
        {"worker_type": worker_type, "label": worker_type.replace("_", " ").title(),
         "specialist_skill_name": "panels-worker", "launch_defaults": launch}
        for worker_type in worker_types
    ]
    manifests = [
        {
            "worker_type": worker_type,
            "label": worker_type.replace("_", " ").title(),
            "stages": [{"id": "done", "label": "Done", "gating_field": None, "is_terminal": True, "ownership_mode": None}],
            "fields": [],
        }
        for worker_type in worker_types
    ]
    unexpected = []
    metrics = MetricsFixture()
    metrics.append()
    metrics_calls = []

    fold = {"enabled": False, "count": 3}
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py"))

    def respond(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        if fold["enabled"] and path == "/api/navigation/view/snapshot":
            board = {"managers": [
                {"id": f"fold_{index}", "title": f"Settled fixture {index}", "created_at": index + 1,
                 "settled": True, "tickets": []}
                for index in range(fold["count"])
            ]}
            source = fixture["navigation_response"](board, {"chief_of_staff": {"label": "Chief"}})
            view = json.loads(parse_qs(parts.query)["view"][0])
            route.fulfill(json=fixture["scoped_navigation_response"](source, view))
            return
        if path.startswith("/api/vps-metrics/"):
            metrics_calls.append(path)
            response = metrics.client.request(route.request.method, path + ("?" + parts.query if parts.query else ""))
            route.fulfill(status=response.status_code, content_type="application/json", body=response.content)
            return
        if path == "/api/resource-body":
            # Versioned reads arrive wrapped. The screen only needs the body.
            key = parse_qs(parts.query).get("key", [""])[0]
            inner = {
                "/api/worker-types?supported=true": {"worker_types": manifests},
                "/api/worker-types": {"worker_types": manifests},
                "/api/feedback/count": {"open_count": 0},
                "/api/deployment-status": {"state": "idle", "deployed_sha": None, "target_sha": None, "outcome": None, "detail": None, "valid_until": None},
            }.get(key)
            if inner is None:
                unexpected.append(key)
                route.fulfill(status=503, json={"error": {"code": "fixture", "message": key}})
                return
            route.fulfill(json={"key": key, "epoch": "fixture", "revision": "1", "body": inner})
            return
        if path == "/api/workers":
            result = {"workers": workers, "chief_of_staff": {"employee_id": "chief", "label": "Chief", "skill": skill, "launch_defaults": launch}}
        elif path == "/api/skills":
            result = {"skills": [skill]}
        elif path == "/api/worker-types":
            result = {"worker_types": manifests}
        elif path == "/api/feedback/count":
            result = {"open_count": 0}
        elif path == "/api/meta":
            result = {"test_mode": True, "app_sha": None}
        elif path == "/api/review":
            result = {"items": [], "running_worker_count": 0}
        elif path == "/api/conversation/backends":
            result = {"backends": []}
        else:
            # The rail's own data is not what this case is about. It is refused rather
            # than faked, which also proves the pane does not depend on it.
            unexpected.append(path)
            route.fulfill(status=503, json={"error": {"code": "fixture", "message": path}})
            return
        route.fulfill(json=result)

    context = browser.new_context(viewport={"width": 1280, "height": 700})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", respond)
    rail = page.locator(".rail-utilities")
    pane = page.locator(".board-workspace-right")
    scroller = page.locator("[data-utility-scroll]")

    # The rail's failed source cannot gate independently loaded panes. Mark its real
    # node so later URL changes prove this is one mounted shell, not a lookalike.
    # Usage opens in the pane, and its period choice survives tab changes.
    page.goto(url + "#/workspace/utility/usage", wait_until="domcontentloaded")
    expect(rail).to_be_visible()
    expect(pane.locator("nav.metrics")).to_be_visible()
    rail.evaluate("node => window.__persistentRail = node")
    pane.get_by_role("link", name="Tokens", exact=True).click()
    pane.get_by_role("button", name="7d", exact=True).click()
    pane.get_by_role("link", name="Cost", exact=True).click()
    expect(pane.get_by_role("button", name="7d", exact=True)).to_have_attribute("aria-pressed", "true")
    pane.get_by_role("link", name="Tokens").click()
    page.wait_for_function("location.hash === '#/workspace/utility/usage/tokens'")
    expect(rail).to_be_visible()
    expect(pane.get_by_role("link", name="Tokens", exact=True)).to_have_attribute("aria-current", "page")

    # The deep address is an address: it survives a reload, so it can also be pasted.
    page.reload(wait_until="domcontentloaded")
    expect(rail).to_be_visible()
    expect(pane.get_by_role("link", name="Tokens", exact=True)).to_have_attribute("aria-current", "page")
    assert page.evaluate("location.hash") == "#/workspace/utility/usage/tokens"

    # Settings keeps the pane and the list the rail was showing.
    page.goto(url + "#/workspace/utility/settings?view=managers", wait_until="domcontentloaded")
    expect(pane.locator("[data-settings]")).to_be_visible()
    # The back-arrow to Usage belonged to a page that reached Settings from Usage.
    assert pane.locator('a[href="#/backends"]').count() == 0
    pane.get_by_role("link", name="Scheduled tasks").click()
    page.wait_for_function("location.hash === '#/workspace/utility/settings/scheduled-tasks?view=managers'")
    expect(rail).to_be_visible()

    # A row of the Config index is a link like any other, and it was never reported.
    page.goto(url + "#/workspace/utility/settings?view=managers", wait_until="domcontentloaded")
    pane.locator('a[href*="/config/chief-of-staff"]').click()
    page.wait_for_function("location.hash === '#/workspace/utility/settings/config/chief-of-staff?view=managers'")
    expect(rail).to_be_visible()

    # The pane owns its scroll. The skeleton is gone by now, so nothing it styles is
    # holding this up, and the scroller is the pane's own element.
    page.goto(url + "#/workspace/utility/settings", wait_until="domcontentloaded")
    expect(pane.locator('a[href*="/config/workers/research"]')).to_be_visible()
    # The region the pane wraps the screen in has finished, so nothing it styles while
    # loading is holding the scroll up.
    expect(scroller.locator("> .skeleton-region")).to_have_attribute("aria-busy", "false")
    geometry = scroller.evaluate(
        "node => ({over: node.scrollHeight - node.clientHeight, overflowY: getComputedStyle(node).overflowY})"
    )
    assert geometry["overflowY"] == "auto", geometry
    assert geometry["over"] > 0, geometry
    scroller.evaluate("node => { node.scrollTop = node.scrollHeight; }")
    assert scroller.evaluate("node => node.scrollTop") > 0
    # The last row is reachable, which is what the owner could not do.
    expect(pane.locator('a[href*="/config/workers/research"]')).to_be_in_viewport()
    # One scroller, not two. A second one inside would swallow the wheel first.
    inner = scroller.evaluate(
        "node => [...node.querySelectorAll('*')].filter(child => "
        "  ['auto','scroll'].includes(getComputedStyle(child).overflowY)"
        "  && child.scrollHeight > child.clientHeight).length"
    )
    assert inner == 0, inner
    # An editor change must replace its keyed Config detail instead of retaining
    # the previous selection inside the persistent shell.
    page.evaluate("location.hash='#/settings/config/chief-of-staff'")
    expect(pane.locator('[data-agent-id="chief_of_staff"]')).to_be_visible()
    page.evaluate("location.hash='#/settings/config/worker-skill'")
    expect(pane.locator('[data-agent-id="panels-worker"]')).to_be_visible()
    expect(pane.locator('[data-agent-id="chief_of_staff"]')).to_have_count(0)

    # The new foot uses the public Usage route, whose internal choices must also
    # survive a tab change across the persistent shell's external-pane boundary.
    page.evaluate("location.hash='#/backends/tokens'")
    expect(pane.locator('nav.metrics')).to_be_visible()
    pane.get_by_role('button', name='7d', exact=True).click()
    pane.get_by_role('link', name='Cost', exact=True).click()
    expect(pane.get_by_role('button', name='7d', exact=True)).to_have_attribute('aria-pressed', 'true')

    # Root destinations use the same mounted rail, even when its data is refused.
    page.evaluate("location.hash='#/settings'")
    expect(pane.locator('[data-settings]')).to_be_visible()
    rail.evaluate("node => window.__persistentRail = node")
    for destination, selector in [("#/review", ".review-screen"), ("#/vps", '[data-screen="vps"]'), ("#/settings", "[data-settings]")]:
        page.evaluate("hash => location.hash=hash", destination)
        expect(pane.locator(selector)).to_be_visible()
        assert rail.evaluate("node => node === window.__persistentRail")

    # The real App route owns the live page. Leaving it stops reads and lease renewals.
    page.evaluate("location.hash='#/vps'")
    expect(pane.locator('[data-vps-metric="cpu"]')).to_contain_text("27")
    assert metrics_calls.count('/api/vps-metrics/activity') > 0
    vps_evidence = Path(os.environ.get('PANELS_VPS_ARTIFACT_DIR', os.environ['PANELS_SIDEBAR_ARTIFACT_DIR']))
    vps_evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(animations="disabled", path=str(vps_evidence / 'vps-shell-pane.png'))
    page.evaluate("location.hash='#/settings'")
    expect(pane.locator('[data-settings]')).to_be_visible()
    before = len(metrics_calls)
    page.wait_for_timeout(2200)
    page.evaluate("dispatchEvent(new Event('focus')); dispatchEvent(new Event('online'))")
    page.wait_for_timeout(30)
    assert len(metrics_calls) == before, metrics_calls[before:]
    page.evaluate("location.hash='#/vps'")
    expect(pane.locator('[data-vps-metric="cpu"]')).to_contain_text("27")
    assert '/api/vps-metrics/latest' in metrics_calls[before:]
    assert '/api/vps-metrics/activity' in metrics_calls[before:]
    assert rail.evaluate("node => node === window.__persistentRail")

    # Preview's own full-height document styles must remain inside the same pane.
    page.route("**/files/tickets/t_fixture/artifacts/wide.html", lambda route: route.fulfill(
        content_type="text/html", body="<html><body style='width:3000px;height:3000px'>Wide artifact</body></html>"))
    page.evaluate("location.hash='#/preview?source=ticket&ticket=t_fixture&path=artifacts%2Fwide.html'")
    expect(pane.locator('[data-file-preview-route] iframe')).to_be_visible()
    assert rail.evaluate("node => node === window.__persistentRail")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    bounds = pane.bounding_box()
    assert bounds and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 701, bounds

    # At minimum width all six controls and both end labels remain inside the rail.
    grip = page.get_by_role("separator", name="Resize workspace panel")
    grip.focus()
    for _ in range(20):
        page.keyboard.press("Shift+ArrowLeft")
    expect(grip).to_have_attribute("aria-valuenow", "232")
    expect(rail.locator('.utility')).to_have_count(6)
    rail_bounds = page.locator('.workspace-rail').bounding_box()
    assert rail_bounds and abs(rail_bounds['width'] - 232) < 2, rail_bounds
    for name in ["Review", "Usage", "Ideas", "Feedback", "VPS", "Settings"]:
        control = rail.get_by_label(name, exact=True)
        control.focus()
        label = control.locator('.utility-label')
        expect(label).to_have_css("opacity", "1")
        box = label.bounding_box()
        assert box and box['x'] >= rail_bounds['x'] and box['x'] + box['width'] <= rail_bounds['x'] + rail_bounds['width'] + 1, (name, box, rail_bounds)

    # Only the shell changes at 720; every selected utility receives compact treatment.
    page.set_viewport_size({"width": 721, "height": 700})
    expect(page.locator('.workspace-rail')).to_be_visible()
    expect(page.locator('.workspace-mobile-nav')).to_be_hidden()
    page.set_viewport_size({"width": 720, "height": 700})
    expect(page.locator('.workspace-rail')).to_be_hidden()
    expect(page.locator('.workspace-mobile-nav')).to_be_visible()
    expect(pane).to_be_visible()
    page.locator('.workspace-mobile-nav [data-screen="workspace"]').click()
    expect(page.locator('.workspace-rail')).to_be_visible()
    expect(pane).to_be_hidden()
    page.locator('[data-screen="more"]').click()
    menu = page.locator('[data-shell-more-menu]')
    expect(menu).to_contain_text("Work")
    expect(menu).to_contain_text("System")
    menu.get_by_role('link', name='Settings', exact=True).click()
    expect(pane.locator('[data-settings]')).to_be_visible()
    expect(page.locator('.workspace-rail')).to_be_hidden()
    # Resizing while the compact bar owns focus must reveal an equivalent target.
    for name in ["Review", "Workspace"]:
        link = page.locator('.workspace-mobile-nav').get_by_role('link', name=name, exact=True)
        link.focus()
        page.set_viewport_size({"width": 721, "height": 700})
        page.wait_for_function("document.activeElement && document.activeElement.getClientRects().length > 0 && document.activeElement !== document.body")
        page.set_viewport_size({"width": 720, "height": 700})
    evidence = Path(os.environ['PANELS_SIDEBAR_ARTIFACT_DIR'])
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(animations="disabled", path=str(evidence / 'shell-720.png'))
    page.set_viewport_size({"width": 1280, "height": 700})
    page.screenshot(animations="disabled", path=str(evidence / 'shell-desktop-minimum-rail.png'))

    fold["enabled"] = True
    verify_settled_fold(page, url, fold, evidence)

    assert not errors, errors
    unwanted = [path for path in unexpected if not path.startswith(("/api/navigation", "/api/changes", "/api/managers", "/api/projects", "/api/schedules", "/api/conversation", "/api/workers/chief-of-staff"))]
    assert not unwanted, unwanted
    context.close()
    metrics.close()


def verify_settled_fold(page, url, fixture, evidence):
    """Real Svelte/layout/WAAPI proof that the controller's synthetic DOM cannot give.

    One sequential flow exercises delayed navigation publication and fresh scoped
    snapshots during folds. Geometry comes from animation frames, never a fake DOM.
    """
    started = time.perf_counter()
    page.goto(url + "#/workspace/utility/settings", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    head = page.locator(".settled-head")
    expect(head).to_contain_text("(3)")
    page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
    page.evaluate("""() => {
      window.__foldProof = [];
      window.__foldCapture = (name, duration) => {
        const samples = [], start = performance.now();
        window.__foldProof.push({name, samples});
        return new Promise(resolve => {
          const sample = () => {
            const section = document.querySelector('.settled');
            const body = section.querySelector('[data-settled-scroll]');
            const rows = [...section.querySelectorAll('[data-settled-item]')];
            const animations = section.getAnimations({subtree:true});
            samples.push({ms:performance.now()-start, height:section.getBoundingClientRect().height,
              body:body?.getBoundingClientRect().height ?? 0, rows:rows.length,
              bodyPresent:!!body, expanded:section.querySelector('button').getAttribute('aria-expanded'),
              cap:parseFloat(getComputedStyle(section).maxHeight),
              rowEffects:animations.filter(a => a.effect?.target?.closest?.('[data-settled-item]')).length,
              sectionEffects:section.getAnimations().length,
              opacity:body ? +getComputedStyle(body).opacity : null,
              scrollHeight:body?.scrollHeight ?? 0});
            if (performance.now()-start < duration) requestAnimationFrame(sample);
            else resolve(samples);
          }; requestAnimationFrame(sample);
        });
      };
      window.__foldStart = (name, duration, toggles=[]) => {
        window.__foldDone=false;
        window.__foldCapture(name,duration).then(() => window.__foldDone=true);
        document.querySelector('.settled-head').click();
        for (const ms of toggles) setTimeout(() => document.querySelector('.settled-head').click(),ms);
      };
    }""")

    def start(name, duration=850, delay=0, toggles=None):
        page.evaluate("([name,duration,delay,toggles]) => { window.__navigationDelay=delay; window.__foldStart(name,duration,toggles); }",
                      [name, duration, delay, toggles or []])

    def finish():
        page.wait_for_function("window.__foldDone")
        return page.evaluate("window.__foldProof.at(-1).samples")

    def smooth(samples, opening=True):
        heights = [sample["height"] for sample in samples]
        assert max(heights) - min(heights) > 20, samples
        low, high = min(heights), max(heights)
        assert sum(low + 2 < value < high - 2 for value in heights) >= 3, samples
        # A stale animation target used to disappear and jump to intrinsic height.
        assert all(abs(current["height"] - previous["height"]) < 2
                   for previous, current in zip(samples, samples[1:], strict=False)
                   if not current["sectionEffects"]), samples
        assert abs(heights[-1] - heights[-3]) < 1, samples[-4:]
        assert all(sample["rowEffects"] == 0 for sample in samples), samples
        assert samples[-1]["expanded"] == ("true" if opening else "false"), samples[-1]

    # A settled window arrives within Glide, then grows while the fold still moves.
    start("late-within-glide-and-refresh", delay=100)
    page.wait_for_function("document.querySelectorAll('[data-settled-item]').length === 3")
    fixture["count"] = 7
    page.evaluate("window.__navigationDelay=0; void window.__refreshNavigation()")
    samples = finish()
    smooth(samples)
    assert samples[-1]["rows"] == 7, samples[-1]
    page.screenshot(path=str(evidence / "settled-fold-open.png"))

    # The body survives until the close completes, even when the source changes.
    start("close-with-refresh", duration=650)
    page.wait_for_timeout(70)
    fixture["count"] = 5
    page.evaluate("void window.__refreshNavigation()")
    samples = finish()
    smooth(samples, False)
    assert any(sample["bodyPresent"] and sample["height"] > samples[-1]["height"] + 2 for sample in samples)
    assert not samples[-1]["bodyPresent"], samples[-1]

    # Content arrives after the original Glide deadline. It still enters as a fold.
    start("late-after-glide", duration=1050, delay=450)
    samples = finish()
    smooth(samples)
    assert samples[-1]["rows"] == 5, samples[-1]
    assert any(sample["ms"] > 450 and sample["sectionEffects"] for sample in samples), samples

    # Rapid close/open/close preserves the sampled starting height and latest intent.
    start("reversal", duration=850, toggles=[90, 180])
    samples = finish()
    smooth(samples, False)
    assert not samples[-1]["bodyPresent"], samples[-1]

    # The production flex cap and internal scrollbar constrain a large settled page.
    fixture["count"] = 30
    start("capped-open", duration=850)
    samples = finish()
    smooth(samples)
    final = samples[-1]
    assert final["rows"] == 30 and final["scrollHeight"] > final["body"] + 50, final
    bounds = page.locator('.settled').bounding_box()
    rail = page.locator('.workspace-rail').bounding_box()
    assert bounds and rail and bounds['height'] <= rail['height'] * .5 + 1, (bounds, rail)
    body = page.locator('[data-settled-scroll]')
    body.evaluate("node => node.scrollTop=node.scrollHeight")
    assert body.evaluate("node => node.scrollTop") > 0
    page.screenshot(path=str(evidence / "settled-fold-capped.png"))

    # Reduced motion uses the same body lifecycle without timed effects.
    page.emulate_media(reduced_motion="reduce")
    start("reduced-close", duration=100)
    samples = finish()
    assert not samples[-1]["bodyPresent"] and all(sample["sectionEffects"] == 0 for sample in samples), samples
    start("reduced-open", duration=180)
    samples = finish()
    assert samples[-1]["rows"] == 30 and all(sample["sectionEffects"] == 0 for sample in samples), samples
    proof = page.evaluate("window.__foldProof")
    (evidence / "settled-fold-frames.json").write_text(json.dumps({
        "seconds": round(time.perf_counter() - started, 3), "newHosts": 0, "flows": proof,
    }, indent=2))
