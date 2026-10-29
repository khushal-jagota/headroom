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
from owner_updates_fixture import OwnerUpdatesFixture  # noqa: E402
from vps_metrics_fixture import MetricsFixture  # noqa: E402

_state_barriers = runpy.run_path(str(Path(__file__).resolve().parents[1] / "support/state_barriers.py"))
establish_scrollback = _state_barriers["establish_scrollback"]
refresh_projection = _state_barriers["refresh_projection"]


def run(browser, urls):
    url = urls[0]
    verify_owner_updates(browser, url)
    verify_conversation_overlay(browser, url)
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
            "fields": [], "advance": {}, "ceiling_range": ["done"], "default_ceiling": "done",
            "worker_profile_id": worker_type, "default_backend": "codex",
            "default_model": None, "default_reasoning_effort": None,
        }
        for worker_type in worker_types
    ]
    unexpected = []
    metrics = MetricsFixture()
    metrics.append()
    metrics_calls = []

    fold = {"enabled": False, "count": 3, "overflow": False, "active_count": 1, "release_sha": "fixture-boot"}
    card_fixture = {"enabled": False}
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py"))

    def respond(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        if (fold["enabled"] or card_fixture["enabled"]) and path == "/api/navigation/view/snapshot":
            board = {"managers": [
                {"id": f"fold_{index}", "title": ("Settled" + "UnbrokenTitle" * 12) if fold["overflow"] else f"Settled fixture {index}", "created_at": index + 1,
                 "settled": True, "tickets": []}
                for index in range(fold["count"])
            ]}
            if fold["overflow"]:
                board["managers"].extend(
                    {"id": f"active_{index}", "title": f"Active fixture {index}", "created_at": index + 1,
                     "project_id": f"project_{index}", "project": "Project" + "UnbrokenName" * 12,
                     "settled": False, "tickets": []}
                    for index in range(fold["active_count"])
                )
            if card_fixture["enabled"]:
                board = card_fixture["board"]
            source = fixture["navigation_response"](board, {"chief_of_staff": card_fixture.get("chief", {"label": "Chief"})})
            view = json.loads(parse_qs(parts.query)["view"][0])
            route.fulfill(json=fixture["scoped_navigation_response"](source, view))
            return
        if path == "/api/recap-requests" and card_fixture.get("recap_worker"):
            assert route.request.post_data_json == {"target": {"kind": "ticket", "id": card_fixture["recap_worker"]["id"]}}
            card_fixture["recap_worker"]["recap"] = ""
            card_fixture["recap_requests"] = card_fixture.get("recap_requests", 0) + 1
            route.fulfill(json={"fate": "started"})
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
                "/api/deployment-status": {"state": "preparing", "deployed_sha": None, "target_sha": None, "outcome": None, "detail": None, "valid_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))},
            }.get(key)
            if inner is None:
                unexpected.append(key)
                route.fulfill(status=503, json={"error": {"code": "fixture", "message": key}})
                return
            route.fulfill(json={"key": key, "epoch": "fixture", "revision": "1", "body": inner})
            return
        if path == "/api/updates":
            route.fulfill(json={"configured": False, "available": None, "request": None, "activity": "unknown", "detail": None})
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
            result = {"test_mode": True, "app_sha": fold["release_sha"]}
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

    def host_with_boot_sha(route):
        response = route.fetch()
        route.fulfill(response=response, body=response.text().replace(
            "<head>", '<head><meta name="panels-app-sha" content="fixture-boot">', 1))

    page.route(url, host_with_boot_sha)
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
    verify_rail_overflow(page, fold, evidence)
    capture_card_motion(browser, url, card_fixture, respond, evidence)
    verify_card_geometry(page, url, card_fixture, evidence)
    verify_rail_pagination(page, url, card_fixture, evidence)
    fold["release_sha"] = "fixture-boot"
    verify_recap_pointer_floor(browser, page, url, card_fixture, respond, evidence)

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


def verify_card_geometry(page, url, fixture, evidence):
    """A final-state test cannot detect a border snap or double neighbor movement.

    Reuse the real App host. Pause browser-owned effects at common normalized times
    to measure the actual border and text, independent of machine frame cadence.
    """
    started = time.perf_counter()

    def ticket(identity, parent="geometry_a"):
        return {"id": identity, "manager_id": parent, "title": "Worker " + identity,
                "worker_type": "coding", "stage": "needs_implementation", "ticket_status": "agent",
                "conversation_id": "conversation_" + identity,
                "conversation_signal": {"working": False, "message_for_owner": False}}

    first, second = ticket("geometry_one"), ticket("geometry_two")
    chief_ticket = ticket("geometry_chief", None)
    fixture.update(enabled=True, board={"managers": [
        {"id": "geometry_a", "title": "A manager title that wraps across several lines in the narrow sidebar", "created_at": 1, "tickets": [first, second]},
        {"id": "geometry_b", "title": "Neighbor geometry", "created_at": 2, "tickets": []},
        {"id": "geometry_settled", "title": "Settled geometry", "created_at": 3, "settled": True, "tickets": []},
    ], "columns": [{"cards": [chief_ticket]}]}, chief={"label": "Chief"})
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 1280, "height": 900})
    page.mouse.move(1100, 800)
    page.goto(url + "#/workspace/utility/settings", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator('[data-ticket-id="geometry_two"]')).to_be_visible()
    page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
    page.evaluate("""() => {
      window.__cardProof = [];
      window.__cardSample = id => {
        const cards = [...document.querySelectorAll('.manager-card')];
        const card = cards.find(node => node.dataset.railMotion === 'card:'+id);
        const next = cards[cards.indexOf(card)+1];
        const rect = card.getBoundingClientRect(), title = card.querySelector('.item-title');
        const titleRect = title.getBoundingClientRect();
        const style = getComputedStyle(title);
        return {top:rect.top,bottom:rect.bottom,height:rect.height,
          gap:next ? next.getBoundingClientRect().top-rect.bottom : null,
          titleHeight:titleRect.height,titleScale:titleRect.height/parseFloat(style.lineHeight),
          tickets:[...card.querySelectorAll('[data-ticket-id]')].map(n=>n.dataset.ticketId),
          headings:[...card.querySelectorAll('[data-bucket-key]')].map(n=>n.dataset.bucketKey),
          cardHeight:card.style.height,slotHeight:card.parentElement.style.height,
          scrollTop:card.closest('.active-work-scroll').scrollTop,
          scrollHeight:card.closest('.active-work-scroll').scrollHeight,
          scrollClient:card.closest('.active-work-scroll').clientHeight,
          anchorValue:card.closest('.active-work-scroll').style.getPropertyValue('overflow-anchor'),
          anchorPriority:card.closest('.active-work-scroll').style.getPropertyPriority('overflow-anchor'),
          transform:getComputedStyle(card).transform,
          neighborTransform:next ? getComputedStyle(next).transform : null,
          effects:card.getAnimations().filter(a=>a.effect?.getKeyframes().some(k=>'height' in k)).length};
      };
      window.__finiteEffects = () => document.getAnimations().filter(a =>
        a.effect && a.effect.getTiming().iterations !== Infinity);
    }""")
    # This real-DOM boundary check catches stale direct-child selectors that a
    # selector fake cannot catch. A rejected boundary stalls subsequent source use.
    assert page.evaluate("args=>window.__railDomMatches(...args)", [
        [chief_ticket], fixture["board"]["managers"], {"label": "Chief"},
    ])
    proof = []

    def sample(identity="geometry_a"):
        return page.evaluate("id => window.__cardSample(id)", identity)

    def refresh():
        # The fixture refresh resolves after snapshot delivery. Let Svelte commit
        # before testing the controller's busy marker.
        page.evaluate("async () => { await window.__refreshNavigation(); await new Promise(requestAnimationFrame); }")

    def settled():
        page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")

    def finish_effects():
        page.evaluate("window.__sampleEffects.forEach(a=>a.finish())")
        settled()

    def transition(name, mutate, identity="geometry_a", membership_same=False):
        before = sample(identity)
        mutate()
        refresh()
        # The real card must own a size effect. A row entrance is no substitute.
        try:
            page.wait_for_function("""id => {
              const node=document.querySelector('[data-rail-motion="card:'+id+'"]');
              return node.getAnimations().some(a=>a.effect?.getKeyframes().some(k=>'height' in k));
            }""", arg=identity, timeout=5000)
        except Exception:
            (evidence / "card-transition-failure.json").write_text(json.dumps({"name": name, "before": before, "after": sample(identity)}, indent=2))
            raise
        page.evaluate("window.__sampleEffects=window.__finiteEffects(); window.__sampleEffects.forEach(a=>a.pause())")
        frames = []
        for fraction in [0, .1, .25, .5, .75, .9, 1]:
            page.evaluate("p=>window.__sampleEffects.forEach(a=>a.currentTime=Number(a.effect.getTiming().duration)*p)", fraction)
            frames.append({"progress": fraction, **sample(identity)})
            if fraction == .5:
                page.screenshot(path=str(evidence / ("card-" + name + "-midpoint.png")))
        finish_effects()
        after = sample(identity)
        (evidence / ("card-"+name+"-frames.json")).write_text(json.dumps({"name": name, "before": before, "samples": frames, "after": after,
          "scrollWrites": page.evaluate("window.__scrollWrites ?? []")}, indent=2))
        low, high = sorted([before["height"], after["height"]])
        assert high-low > 10, (name, before, after)
        assert sum(low+.5 < row["height"] < high-.5 for row in frames) >= 3, (name, frames)
        assert abs(frames[0]["top"]-before["top"]) < 1.5, (name, before, frames)
        assert abs(frames[0]["height"]-before["height"]) < 1, (name, before, frames)
        assert abs(frames[-1]["height"]-after["height"]) < 1, (name, after, frames)
        assert all(row["gap"] is None or abs(row["gap"]-before["gap"]) < 1.5 for row in frames), (name, frames)
        assert all(abs(row["titleHeight"]-before["titleHeight"]) < .5 for row in frames), (name, frames)
        assert not after["cardHeight"] and not after["slotHeight"] and not after["effects"], after
        if membership_same:
            assert sorted(before["tickets"]) == sorted(after["tickets"]), (before, after)
        proof.append({"name": name, "before": before, "samples": frames, "after": after})
        return after

    page.screenshot(path=str(evidence / "card-initial.png"))
    transition("header-appears", lambda: second.update(awaiting_approval=True), membership_same=True)
    transition("header-disappears", lambda: second.update(awaiting_approval=False), membership_same=True)
    arriving = ticket("geometry_arriving")
    transition("worker-appears", lambda: fixture["board"]["managers"][0]["tickets"].append(arriving))
    transition("worker-finishes", lambda: arriving.update(stage="done", ticket_status="done"))
    assert "geometry_arriving" not in sample()["tickets"]
    chief_second = ticket("geometry_chief_two", None)
    transition("chief-worker-appears", lambda: fixture["board"]["columns"][0]["cards"].append(chief_second), "chief")
    transition("chief-worker-finishes", lambda: chief_second.update(stage="done", ticket_status="done"), "chief")

    # Hover defers structure but a conversation signal still changes immediately.
    row = page.locator('[data-ticket-id="geometry_one"]')
    row.hover()
    before = sample()
    second.update(awaiting_approval=True)
    first["conversation_signal"]["message_for_owner"] = True
    refresh()
    expect(row).to_have_attribute("data-unread", "true")
    assert sample()["headings"] == before["headings"]
    assert sample()["height"] == before["height"]
    page.mouse.move(1100, 800)
    settled()
    assert "awaiting_approval" in sample()["headings"]

    # Latest queued updates win. The frame record also catches overlap on reversal.
    page.evaluate("""() => {
      window.__rapidSamples=[]; window.__rapidCapture=true;
      const take=()=>{window.__rapidSamples.push(window.__cardSample('geometry_a'));
        if(window.__rapidCapture) requestAnimationFrame(take);}; requestAnimationFrame(take);
    }""")
    second.update(awaiting_approval=False)
    refresh()
    page.wait_for_function("""() => document.querySelector('[data-rail-motion="card:geometry_a"]').getAnimations()
      .some(a=>a.effect?.getKeyframes().some(k=>'height' in k))""")
    second.update(awaiting_approval=True)
    refresh()
    second.update(awaiting_approval=False)
    refresh()
    settled()
    page.evaluate("window.__rapidCapture=false")
    rapid = page.evaluate("window.__rapidSamples")
    assert all(row["gap"] >= -1 for row in rapid), rapid
    assert "awaiting_approval" not in sample()["headings"]
    proof.append({"name": "rapid-latest-intent", "samples": rapid, "after": sample()})
    page.screenshot(path=str(evidence / "card-rapid-final.png"))

    # Reduced motion before an update and when enabled during travel must release sizes.
    page.emulate_media(reduced_motion="reduce")
    second.update(awaiting_approval=True)
    refresh()
    expect(page.locator('[data-rail-motion="heading:geometry_a:awaiting_approval"]')).to_be_visible()
    settled()
    reduced = sample()
    assert not reduced["cardHeight"] and not reduced["slotHeight"] and not reduced["effects"], reduced
    proof.append({"name": "reduced-before", "after": reduced})
    page.emulate_media(reduced_motion="no-preference")
    second.update(awaiting_approval=False)
    refresh()
    page.wait_for_function("""() => document.querySelector('[data-rail-motion="card:geometry_a"]').getAnimations()
      .some(a=>a.effect?.getKeyframes().some(k=>'height' in k))""")
    page.emulate_media(reduced_motion="reduce")
    settled()
    reduced = sample()
    assert not reduced["cardHeight"] and not reduced["slotHeight"] and not reduced["effects"], reduced
    proof.append({"name": "reduced-during", "after": reduced})
    page.screenshot(path=str(evidence / "card-reduced-final.png"))
    page.emulate_media(reduced_motion="no-preference")

    # The same card nodes and keyboard target survive a resize and group transfer.
    row.focus()
    page.locator('[data-rail-motion="card:geometry_a"]').evaluate("n=>window.__geometryCard=n")
    page.set_viewport_size({"width": 1000, "height": 500})
    refresh()
    settled()
    assert page.locator('[data-rail-motion="card:geometry_a"]').evaluate("n=>n===window.__geometryCard")
    expect(row).to_be_focused()
    row.evaluate("n=>n.blur()")
    page.locator('[data-manager="geometry_a"] .item-head').hover()
    page.mouse.move(900, 450)
    expect(page.locator('[data-manager="geometry_a"] .item-title')).to_contain_text("A manager title that wraps")
    settled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    scroll = page.locator('[data-active-work-scroll]')
    scroll.evaluate("async n=>{n.scrollTop=n.scrollHeight; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);}")
    expect(page.locator('[data-manager="geometry_b"]')).to_be_in_viewport()
    assert page.locator('.settled-head').is_visible()
    assert not page.locator('.manager-card [style*="height:"]').count()
    page.screenshot(path=str(evidence / "card-short-wrapped-final.png"))
    # A bottom-anchored scroll can clamp when the final slot shrinks. The same
    # border/gap proof must hold there, while the real scroll anchor is active.
    sampler = page.evaluate("window.__cardSample.toString()")
    fixture["board"]["managers"][0]["tickets"].extend(ticket("scroll_"+str(i)) for i in range(12))
    page.reload(wait_until="domcontentloaded")
    expect(page.locator('[data-manager="geometry_a"] [data-ticket-id]')).to_have_count(14)
    settled()
    page.evaluate("code=>window.__cardSample=eval('('+code+')')", sampler)
    page.mouse.move(900, 450)
    scroll.evaluate("""node => {
      window.__scrollWrites=[]; window.__motionScrollEvents=[];
      node.addEventListener('scroll',event=>window.__motionScrollEvents.push({ms:performance.now(),
        trusted:event.isTrusted,top:node.scrollTop,range:node.scrollHeight-node.clientHeight}),{capture:true});
      const property=Object.getOwnPropertyDescriptor(Element.prototype,'scrollTop');
      Object.defineProperty(node,'scrollTop',{configurable:true,get(){return property.get.call(this)},
        set(value){window.__scrollWrites.push({ms:performance.now(),before:property.get.call(this),value,stack:new Error().stack});property.set.call(this,value)}});
    }""")
    scroll.evaluate("async n=>{n.scrollTop=n.scrollHeight; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);}")
    assert scroll.evaluate("n=>n.scrollTop") > 0
    second.update(awaiting_approval=True)
    refresh()
    settled()
    scroll.evaluate("async n=>{n.scrollTop=n.scrollHeight; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);}")
    page.evaluate("""() => {
      window.__naturalScrollSamples=[]; window.__naturalScrollCapture=true;
      const take=()=>{window.__naturalScrollSamples.push({ms:performance.now(),...window.__cardSample('geometry_a')});
        if(window.__naturalScrollCapture)requestAnimationFrame(take);}; take();
    }""")
    second.update(awaiting_approval=False)
    refresh()
    settled()
    page.evaluate("window.__naturalScrollCapture=false")
    (evidence / "card-scrolled-shrink-natural.json").write_text(json.dumps({
        "samples":page.evaluate("window.__naturalScrollSamples"),
        "scrollWrites":page.evaluate("window.__scrollWrites"),
        "nativeScrollEvents":page.evaluate("window.__motionScrollEvents"),
    }, indent=2))
    natural = page.evaluate("window.__naturalScrollSamples")
    assert max(row['scrollHeight']-row['scrollClient'] for row in natural)-min(row['scrollHeight']-row['scrollClient'] for row in natural) > 5, natural
    # This explicitly proves the browser range contracted during motion; the
    # samples and write stacks distinguish that movement from native input.
    # Range arithmetic cannot recover movement erased before capture.
    assert all(row["gap"] >= -1 for row in natural), natural
    low, high = sorted([natural[0]["height"], natural[-1]["height"]])
    assert sum(low+.5 < row["height"] < high-.5 for row in natural) >= 3, natural
    assert all(abs(row["titleHeight"]-natural[0]["titleHeight"]) < .5 for row in natural), natural
    assert all(abs(current["top"]-previous["top"]) < 1.5
               for previous,current in zip(natural,natural[1:], strict=False)
               if previous["transform"] == current["transform"] == "none"
               and abs(current["height"]-previous["height"]) < .5), natural
    proof.append({"name":"scrolled-natural-shrink","samples":natural,"after":sample()})
    assert not page.locator('.rail-moving,.rail-travelling,.rail-travelling-card').count()

    # BoardRoute persists across URL changes. Unmount the actual App through the
    # existing host to prove disposal releases styles on the detached nodes too.
    second.update(awaiting_approval=True)
    refresh()
    page.wait_for_function("""() => document.querySelector('[data-rail-motion="card:geometry_a"]').getAnimations()
      .some(a=>a.effect?.getKeyframes().some(k=>'height' in k))""")
    page.evaluate("""() => {
      window.__disposedCard=document.querySelector('[data-rail-motion="card:geometry_a"]');
      window.__disposedSlot=window.__disposedCard.parentElement;
      return window.__unmountWorkspace();
    }""")
    disposed = page.evaluate("""() => ({height:window.__disposedCard.style.height,
      slotHeight:window.__disposedSlot.style.height,
      motion:window.__disposedCard.classList.contains('rail-moving'),
      connected:window.__disposedCard.isConnected})""")
    assert disposed == {"height": "", "slotHeight": "", "motion": False, "connected": False}, disposed
    proof.append({"name": "dispose-during-resize", "after": disposed})
    (evidence / "card-geometry-frames.json").write_text(json.dumps({
        "seconds": round(time.perf_counter()-started, 3), "newHosts": 0,
        "method": "Real browser border boxes at normalized WAAPI progress; rapid changes use animation frames", "flows": proof,
    }, indent=2))


def capture_card_motion(browser, url, fixture, respond, evidence):
    """Record a short, ordinary-speed review clip in the same existing App host."""
    recordings = []
    for reduced in [False, True]:
        started = time.perf_counter()
        one = {"id": "clip_one", "manager_id": "clip_manager", "title": "Repair sidebar animations",
               "worker_type": "coding", "stage": "needs_implementation", "ticket_status": "agent"}
        two = {**one, "id": "clip_two", "title": "Check the final card geometry"}
        rows = [one, two]
        fixture.update(enabled=True, board={"managers": [
            {"id": "clip_manager", "title": "Left sidebar and shell design", "created_at": 1, "tickets": rows},
            {"id": "clip_neighbor", "title": "The next manager keeps its spacing", "created_at": 2, "tickets": []},
        ]}, chief={"label": "Chief"})
        context = browser.new_context(viewport={"width": 1100, "height": 700},
                                      record_video_dir=str(evidence / "video"),
                                      record_video_size={"width": 1100, "height": 700},
                                      reduced_motion="reduce" if reduced else "no-preference")
        page = context.new_page()
        page.route("**/api/**", respond)
        try:
            page.goto(url + "#/workspace/utility/settings", wait_until="domcontentloaded")
            expect(page.locator('[data-ticket-id="clip_two"]')).to_be_visible()
            page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
            page.mouse.move(1000, 650)
            page.wait_for_timeout(250)
            for approval in [True, False]:
                two["awaiting_approval"] = approval
                page.evaluate("void window.__refreshNavigation()")
                page.wait_for_timeout(650)
            arriving = {**one, "id": "clip_arriving", "title": "A new Worker appears"}
            rows.append(arriving)
            page.evaluate("void window.__refreshNavigation()")
            page.wait_for_timeout(650)
            arriving.update(stage="done", ticket_status="done")
            page.evaluate("void window.__refreshNavigation()")
            page.wait_for_timeout(750)
            for approval in [True, False, True]:
                two["awaiting_approval"] = approval
                page.evaluate("void window.__refreshNavigation()")
                page.wait_for_timeout(90)
            page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
            page.wait_for_timeout(300)
        finally:
            video = page.video
            context.close()
            video.save_as(str(evidence / ("card-motion-reduced.webm" if reduced else "card-motion-normal.webm")))
            video.delete()
            recordings.append({"reducedMotion": reduced, "seconds": round(time.perf_counter()-started, 3)})
    (evidence / "card-review-clips.json").write_text(json.dumps({"newHosts": 0, "contexts": 2, "recordings": recordings}, indent=2))


def verify_rail_pagination(page, url, fixture, evidence):
    """Actual scoped pages, DOM publication, native scrolling, and surviving rows.

    Coordinator arbitration uses cheap unit coverage. This existing App flow proves
    the boundary that unit hosts cannot: projected pages become measured real rows.
    """
    started = time.perf_counter()
    chief_rows = [{"id": f"page_worker_{i:02}", "manager_id": None, "title": f"Paging Worker {i:02}",
                   "worker_type": "coding", "stage": "needs_implementation", "ticket_status": "agent"}
                  for i in range(40)]
    fixture.update(enabled=True, board={"managers": [
        {"id": f"page_manager_{i:02}", "title": f"Paging manager {i:02}", "created_at": i+1, "tickets": []}
        for i in range(70)
    ], "columns": [{"cards": chief_rows}]}, chief={"label": "Chief"})
    page.set_viewport_size({"width": 1100, "height": 650})
    page.emulate_media(reduced_motion="no-preference")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator('.chief [data-ticket-id]')).to_have_count(30)
    expect(page.locator('[data-manager]')).to_have_count(60)
    page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
    scroll = page.locator('[data-active-work-scroll]')
    page.mouse.move(900, 600)
    proof = []

    def window(section):
        return page.evaluate("""section => window.__navigationFrames.flatMap(frame=>frame.content.windows)
          .filter(window=>window.request.section===section &&
            (section!=='rail_tickets'||window.request.owner?.kind==='chief')).at(-1)""", section)

    def members(section):
        selector = '.chief [data-ticket-id]' if section == 'rail_tickets' else '[data-manager]'
        key = 'ticketId' if section == 'rail_tickets' else 'manager'
        return page.locator(selector).evaluate_all("(nodes,key)=>nodes.map(n=>n.dataset[key])", key)

    def settled():
        page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")

    def top(selector):
        return page.locator(selector).evaluate("n=>n.getBoundingClientRect().top")

    def settle_scroll():
        page.evaluate("async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)}")

    def page_changed(section, prior):
        page.wait_for_function("""([section,prior])=>window.__navigationFrames.flatMap(frame=>frame.content.windows)
          .filter(window=>window.request.section===section &&
            (section!=='rail_tickets'||window.request.owner?.kind==='chief')).at(-1)?.request.anchor!==prior""",
                               arg=[section, prior["request"]["anchor"]])
        # A future window in navigation alone cannot prove rendered membership.
        page.wait_for_function("""section=>{
          const view=window.__navigationFrames.flatMap(frame=>frame.content.windows)
            .filter(w=>w.request.section===section&&(section!=='rail_tickets'||w.request.owner?.kind==='chief')).at(-1);
          const nodes=[...document.querySelectorAll(section==='rail_tickets'?'.chief [data-ticket-id]':'[data-manager]')];
          const actual=nodes.map(n=>section==='rail_tickets'?n.dataset.ticketId:n.dataset.manager).sort();
          return JSON.stringify(actual)===JSON.stringify(view.members.map(k=>k.id).sort());
        }""", arg=section)
        settled()
        return window(section)

    # A native scroll requests a child overlap before the visible half disappears.
    prior = window('rail_tickets')
    anchor = '[data-ticket-id="page_worker_17"]'
    before = scroll.evaluate("""(surface,selector)=>{
      const row=document.querySelector(selector);surface.scrollTop+=row.getBoundingClientRect().top-surface.getBoundingClientRect().top-8;
      return row.getBoundingClientRect().top;
    }""", anchor)
    after_view = page_changed('rail_tickets', prior)
    assert abs(top(anchor)-before) < 1.5, (before, top(anchor), after_view)
    assert after_view['request']['direction'] == 'after' and after_view['request']['anchor'] == prior['forward_overlap']
    proof.append({"name":"card-forward-native-scroll","before":before,"after":top(anchor),"request":after_view['request'],"members":members('rail_tickets')})

    # Home at the physical boundary requests the backward page. The first visible
    # surviving row remains fixed while earlier rows re-enter the same card.
    scroll.evaluate("n=>n.scrollTop=0")
    settle_scroll()
    prior = window('rail_tickets')
    first_id = members('rail_tickets')[0]
    anchor = f'[data-ticket-id="{first_id}"]'
    before = top(anchor)
    page.locator('.chief .item-head').focus()
    page.keyboard.press('Home')
    after_view = page_changed('rail_tickets', prior)
    assert after_view['request']['direction'] == 'before' and after_view['request']['anchor'] == prior['backward_overlap']
    assert abs(top(anchor)-before) < 1.5, (before, top(anchor), after_view)
    proof.append({"name":"card-backward-keyboard","before":before,"after":top(anchor),"request":after_view['request'],"members":members('rail_tickets')})
    page.locator('.chief .item-head').evaluate('n=>n.blur()')

    # The same mounted App and scoped transport now pause an overlap response.
    # Native wheel input continues after the request, protecting the gap left by
    # immediate-publication coverage above. No additional browser host is created.
    continued_scroll_started = time.perf_counter()
    for queued in [False, True]:
        page.reload(wait_until="domcontentloaded")
        expect(page.locator('.chief [data-ticket-id]')).to_have_count(30)
        settled()
        page.mouse.move(900, 600)
        prior = window('rail_tickets')
        scroll.evaluate("""n => {
          n.style.setProperty('overflow-anchor','auto','important');
          window.__holdNavigation=true;
          window.__navigationOrder=[];
          window.__paginationScrollEvents=[];
          n.addEventListener('scroll',event=>window.__paginationScrollEvents.push({
            trusted:event.isTrusted, top:n.scrollTop, ms:performance.now()}), {capture:true});
        }""")
        anchor = '[data-ticket-id="page_worker_17"]'
        scroll.evaluate("""(surface,selector)=>{const row=document.querySelector(selector);
          surface.scrollTop+=row.getBoundingClientRect().top-surface.getBoundingClientRect().top-8;
        }""", anchor)
        page.wait_for_function('window.__heldNavigation.length>0')
        request_top = top(anchor)
        # Hover deliberately holds card structure only in the queued-publication
        # case. The delayed case releases on a trusted native scroll at capture
        # phase, before the production action receives that event.
        row = page.locator(anchor)
        if queued:
            row.hover()
            page.evaluate("async()=>{window.__holdNavigation=false;window.__releaseNavigation();await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)}")
            assert members('rail_tickets') == [key['id'] for key in prior['members']]
        else:
            bounds = scroll.bounding_box()
            page.mouse.move(bounds['x']+1, bounds['y']+30)
        scroll.evaluate("""(n,queued) => {
          window.__releaseOnNativeScroll=event=>{
            if(!event.isTrusted) return;
            n.removeEventListener('scroll',window.__releaseOnNativeScroll,true);
            const row=document.querySelector('[data-ticket-id="page_worker_17"]');
            window.__continuedScroll={top:row.getBoundingClientRect().top,scrollTop:n.scrollTop,
              trusted:event.isTrusted,ms:performance.now(),queued};
            if(!queued){
              // Withhold this dispatch from the action: publication must sample
              // native movement itself, rather than relying on an onScroll call.
              event.stopImmediatePropagation();
              window.__navigationOrder.push({kind:'native-scroll-before-action',ms:performance.now(),top:n.scrollTop});
              window.__holdNavigation=false;window.__releaseNavigation();
            }
          };
          n.addEventListener('scroll',window.__releaseOnNativeScroll,true);
        }""", queued)
        page.mouse.wheel(0, 36)
        page.wait_for_function('window.__continuedScroll?.queued === '+str(queued).lower())
        continued = page.evaluate('window.__continuedScroll')
        assert continued['trusted'] and abs(continued['top']-request_top)>10, (request_top,continued)
        if queued:
            # Further native movement occurred while the new page was already
            # queued. Leaving the row publishes the still-deferred card once.
            assert members('rail_tickets') == [key['id'] for key in prior['members']]
        (evidence / ('queued-publication-before-wait.json' if queued else 'delayed-publication-before-wait.json')).write_text(json.dumps({
            'prior':prior,'continued':continued,'frames':page.evaluate('window.__navigationFrames'),
            'order':page.evaluate('window.__navigationOrder'),'held':page.evaluate('window.__heldNavigation.length'),
            'heldFrames':page.evaluate('window.__heldNavigationFrames'),
            'members':members('rail_tickets')},indent=2))
        page.mouse.move(900, 600)
        if queued:
            page.wait_for_function("""()=>document.querySelector('[data-ticket-id="page_worker_00"]')?.getAnimations()
              .some(a=>a.playState==='running' && a.effect?.getKeyframes().some(k=>'opacity' in k))""")
            bounds = scroll.bounding_box()
            page.mouse.move(bounds['x']+1,bounds['y']+30)
            scroll.evaluate("""n=>{
              window.__exitScrollSamples=[];window.__captureExitScroll=true;
              n.addEventListener('wheel',event=>window.__exitWheel={ms:performance.now(),trusted:event.isTrusted,
                before:n.scrollTop,deltaY:event.deltaY},{once:true,capture:true});
              n.addEventListener('scroll',event=>{
                const row=document.querySelector('[data-ticket-id="page_worker_17"]');
                const exit=document.querySelector('[data-ticket-id="page_worker_00"]');
                const exitRunning=exit?.getAnimations().some(a=>a.playState==='running'&&a.effect?.getKeyframes().some(k=>'opacity' in k))??false;
                window.__exitContinued={top:row.getBoundingClientRect().top,scrollTop:n.scrollTop,ms:performance.now(),
                  trusted:event.isTrusted,exitRunning,wheel:window.__exitWheel};
                window.__exitScrollTake=()=>{
                  const row=document.querySelector('[data-ticket-id="page_worker_17"]');
                  window.__exitScrollSamples.push({ms:performance.now(),top:row?.getBoundingClientRect().top,
                    scrollTop:n.scrollTop,range:n.scrollHeight-n.clientHeight,transform:row?getComputedStyle(row).transform:null,
                    published:!document.querySelector('[data-ticket-id="page_worker_00"]'),busy:Boolean(document.querySelector('[data-rail-motion-busy]'))});
                };
                const sample=()=>{if(!window.__captureExitScroll)return;window.__exitScrollTake();
                  requestAnimationFrame(sample);};sample();
              },{once:true,capture:true});
            }""")
            page.mouse.wheel(0,36)
            page.wait_for_function('window.__exitContinued')
            continued = page.evaluate('window.__exitContinued')
            assert continued['exitRunning'] and continued['wheel']['trusted'] and continued['wheel']['deltaY']>0, continued
            assert continued['scrollTop']-continued['wheel']['before']>20 and continued['ms']>=continued['wheel']['ms'], continued
        after_view = page_changed('rail_tickets', prior)
        if queued:
            page.evaluate('window.__exitScrollTake();window.__captureExitScroll=false')
            exit_samples = page.evaluate('window.__exitScrollSamples')
            (evidence / 'queued-publication-scroll-during-exit.json').write_text(json.dumps({
                'continued':continued,'samples':exit_samples},indent=2))
            assert not exit_samples[0]['published'] and exit_samples[-1]['published'] and not exit_samples[-1]['busy'], exit_samples
            assert exit_samples[0]['range']-exit_samples[-1]['range']>5, exit_samples
            assert all(abs(sample['top']-continued['top'])<1.5 for sample in exit_samples), (continued,exit_samples)
        assert abs(top(anchor)-continued['top']) < 1.5, (queued,continued,top(anchor),after_view)
        policy = scroll.evaluate("n=>({value:n.style.getPropertyValue('overflow-anchor'),priority:n.style.getPropertyPriority('overflow-anchor')})")
        assert policy == {'value':'auto','priority':'important'}, policy
        order = page.evaluate('window.__navigationOrder')
        if not queued:
            assert [entry['kind'] for entry in order][:2] == ['native-scroll-before-action','publication'], order
        proof.append({'name':'queued-publication-continued-wheel' if queued else 'delayed-publication-native-scroll-before-action',
                      'requestTop':request_top,'continued':continued,'after':top(anchor),'policy':policy,
                      'eventOrder':order,'scrollEvents':page.evaluate('window.__paginationScrollEvents')})
    # A separate ordering proof moves the actual DOM scroller and publishes the
    # held wire frame in one JavaScript task. Its movement is programmatic; the
    # later scroll event is native. This is the precise pre-dispatch boundary,
    # complementary to the physical-wheel proof above.
    page.reload(wait_until="domcontentloaded")
    expect(page.locator('.chief [data-ticket-id]')).to_have_count(30)
    settled()
    page.mouse.move(900, 600)
    prior = window('rail_tickets')
    scroll.evaluate("""n=>{window.__holdNavigation=true;
      const row=document.querySelector('[data-ticket-id="page_worker_17"]');
      n.scrollTop+=row.getBoundingClientRect().top-n.getBoundingClientRect().top-8;
    }""")
    page.wait_for_function('window.__heldNavigation.length>0')
    settle_scroll()
    expected = scroll.evaluate("""n=>{
      window.__navigationOrder=[];
      n.addEventListener('scroll',event=>window.__navigationOrder.push({kind:'native-scroll-dispatch',trusted:event.isTrusted,ms:performance.now(),top:n.scrollTop}),{once:true,capture:true});
      n.scrollTop+=36;
      const top=document.querySelector('[data-ticket-id="page_worker_17"]').getBoundingClientRect().top;
      window.__navigationOrder.push({kind:'programmatic-movement',ms:performance.now(),top:n.scrollTop});
      window.__holdNavigation=false;window.__releaseNavigation();
      return top;
    }""")
    after_view = page_changed('rail_tickets', prior)
    assert abs(top('[data-ticket-id="page_worker_17"]')-expected)<1.5, (expected,top('[data-ticket-id="page_worker_17"]'))
    order = page.evaluate('window.__navigationOrder')
    assert [entry['kind'] for entry in order][:3] == ['programmatic-movement','publication','native-scroll-dispatch'], order
    assert order[2]['trusted'], order
    proof.append({'name':'same-task-publication-before-native-scroll-dispatch','movement':'programmatic',
                  'expected':expected,'after':top('[data-ticket-id="page_worker_17"]'),'eventOrder':order})

    continued_scroll_seconds = time.perf_counter() - continued_scroll_started
    # Restore the initial page before retaining the existing completion and parent
    # overlap proofs below.
    page.reload(wait_until="domcontentloaded")
    expect(page.locator('.chief [data-ticket-id]')).to_have_count(30)
    settled()

    # A live completion publication must not request another page. It also leaves
    # the first parent visible at the physical top for backward anchor proof.
    chief_request = window('rail_tickets')['request']
    for row in chief_rows:
        row.update(stage='done',ticket_status='done')
    page.evaluate('async()=>{await window.__refreshNavigation();await new Promise(requestAnimationFrame)}')
    expect(page.locator('.chief [data-ticket-id]')).to_have_count(0)
    settled()
    assert (window('rail_tickets')['request']['anchor'],window('rail_tickets')['request']['direction']) == (chief_request['anchor'],chief_request['direction']), (chief_request,window('rail_tickets')['request'])

    # Parent pages use the same physical scroller, with a different surviving row.
    prior = window('rail_parents')
    anchor = '[data-manager="page_manager_35"]'
    before = scroll.evaluate("""(surface,selector)=>{
      const row=document.querySelector(selector);surface.scrollTop+=row.getBoundingClientRect().top-surface.getBoundingClientRect().top-8;
      return row.getBoundingClientRect().top;
    }""", anchor)
    after_view = page_changed('rail_parents', prior)
    (evidence / 'parent-pagination-state.json').write_text(json.dumps({
        'before':before,'after':top(anchor),'scrollTop':scroll.evaluate('n=>n.scrollTop'),
        'oldWindow':prior,'newWindow':after_view,'parentMembers':members('rail_parents'),
        'chiefMembers':members('rail_tickets'),
    },indent=2))
    assert after_view['request']['direction'] == 'after' and after_view['request']['anchor'] == prior['forward_overlap']
    assert abs(top(anchor)-before) < 1.5, (before, top(anchor), after_view)
    proof.append({"name":"parent-forward-native-scroll","before":before,"after":top(anchor),"request":after_view['request'],"members":members('rail_parents')})

    prior = window('rail_parents')
    first_id = members('rail_parents')[0]
    anchor = f'[data-manager="{first_id}"]'
    # The wheel first moves to the physical top. Compare the surviving row with
    # that user-requested position, rather than its position before user travel.
    before = top(anchor)+scroll.evaluate('n=>n.scrollTop')
    bounds = scroll.bounding_box()
    page.mouse.move(bounds['x']+1, bounds['y']+30)
    page.mouse.wheel(0, -100000)
    after_view = page_changed('rail_parents', prior)
    (evidence / 'parent-pagination-state.json').write_text(json.dumps({
        'before':before,'after':top(anchor),'scrollTop':scroll.evaluate('n=>n.scrollTop'),
        'oldWindow':prior,'newWindow':after_view,'parentMembers':members('rail_parents'),
        'chiefMembers':members('rail_tickets'),
    },indent=2))
    assert after_view['request']['direction'] == 'before' and after_view['request']['anchor'] == prior['backward_overlap']
    assert abs(top(anchor)-before) < 1.5, (before, top(anchor), after_view)
    proof.append({"name":"parent-backward-wheel","before":before,"after":top(anchor),"request":after_view['request'],"members":members('rail_parents')})
    page.mouse.move(900, 600)
    page.screenshot(path=str(evidence / 'rail-pagination-final.png'))
    (evidence / 'rail-pagination.json').write_text(json.dumps({
        "seconds":round(time.perf_counter()-started,3),"newHosts":0,
        "continuedScrollSeconds":round(continued_scroll_seconds,3),"flows":proof,
    }, indent=2))


def verify_conversation_overlay(browser, url):
    """Actual Chief host inside the responsive workspace owns the geometry."""
    started = time.perf_counter()
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py"))
    tickets = [dict(id=f"t_overlay{i}", title=f"Overlay document row {i}", worker_type="coding", stage="needs_implementation", ticket_status="agent", recap="Document row remains readable under the floating conversation.") for i in range(36)]
    tickets[0].update(stage="needs_brief", ticket_status="user", assigned=True, gating_field="brief", conversation_id="ticket-overlay")
    brief = "\n\n".join(f"Document paragraph {i}. " + "This content retains its place beneath the floating conversation. " * 4 for i in range(30)) + "\n\nFinal document paragraph."
    detail = {**tickets[0], "project_id": None, "project": "", "manager_id": None, "manager_title": None, "employee_backend": "codex", "employee_launch_model": None, "employee_launch_reasoning_effort": None, "employee_configuration_editable": False, "ceiling": "done", "ceiling_holder": {"kind":"owner","id":"owner"}, "blocked": False, "is_backlogged": False, "blocker_summary": {"blocked_by": []}, "field_values": {"brief": brief}, "field_summaries": {"brief": {"revision": brief, "byte_count": len(brief.encode())}}, "pending_proposal": None}
    manifest = {"worker_types": [{"worker_type":"coding", "label":"Coding", "stages":[{"id":"needs_brief","label":"Brief","gating_field":"brief","is_terminal":False,"ownership_mode":"user"},{"id":"done","label":"Done","gating_field":None,"is_terminal":True,"ownership_mode":None}], "fields":[{"id":"brief","label":"Brief"}], "advance":{"needs_brief":"done"}, "ceiling_range":["none","needs_brief","done"], "default_ceiling":"needs_brief"}]}
    source = fixture["navigation_response"]({"columns": [{"cards": tickets}]}, {"chief_of_staff": {"label": "Chief", "conversation_id": "bounded-reader"}})
    evidence = Path(os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR", "/tmp/panels-overlay")) / "overlay"
    evidence.mkdir(parents=True, exist_ok=True)
    proof = {"nativeKeyboard": "Not tested: desktop Chromium visualViewport simulation proves layout bounds only.", "hosts": []}

    def respond(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        if path == "/api/navigation/view/snapshot":
            view = json.loads(parse_qs(parts.query)["view"][0])
            route.fulfill(json=fixture["scoped_navigation_response"](source, view))
            return
        key = parse_qs(parts.query).get("key", [path])[0] if path == "/api/resource-body" else path
        values = {
            "/api/workers/chief-of-staff/settings": {"employee_id": "chief", "label": "Chief", "recap": "The document keeps its full height beneath the conversation.", "skill": None, "launch_defaults": {"employee_backend": "codex", "employee_launch_model": None, "employee_launch_reasoning_effort": None}},
            "/api/chief/conversation/start-values": {"backend_key": "codex", "model": None, "reasoning_effort": None},
            "/api/tickets/t_overlay0/workspace": detail,
            "/api/tickets/t_overlay0/conversation/start-values": {"backend_key":"codex","model":None,"reasoning_effort":None},
            "/api/worker-types?supported=true": manifest,
            "/api/worker-types": manifest,
            "/api/feedback/count": {"open_count": 0},
            "/api/deployment-status": {"state": "idle", "deployed_sha": None, "target_sha": None, "outcome": None, "detail": None, "valid_until": None},
            "/api/updates": {"configured": False, "available": None, "request": None, "activity": "unknown", "detail": None},
            "/api/meta": {"test_mode": True, "app_sha": None},
            "/api/review": {"items": [], "running_worker_count": 0},
        }
        body = values.get(key, {})
        route.fulfill(json={"key": key, "epoch": "overlay", "revision": "1", "body": body} if path == "/api/resource-body" else body)

    for name, width, height in (("wide-chief", 1800, 900), ("phone-chief", 390, 844), ("narrow-ticket", 960, 900)):
        ticket_host = name == "narrow-ticket"
        print("OVERLAY_HOST " + name, flush=True)
        context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        page.route("**/api/**", respond)
        page.add_init_script("""(() => {
          const viewport = new EventTarget();
          Object.assign(viewport,{offsetTop:0,offsetLeft:0,width:innerWidth,height:innerHeight,scale:1});
          Object.defineProperty(window,'visualViewport',{value:viewport,configurable:true});
          window.__keyboardViewport=(top,height)=>{Object.assign(viewport,{offsetTop:top,height});viewport.dispatchEvent(new Event('resize'));viewport.dispatchEvent(new Event('scroll'));};
        })();""")
        page.route("**/files/tickets/t_overlay0/overlay.html", lambda route: route.fulfill(content_type="text/html", body="<!doctype html><html><head><style>html{color-scheme:light;background:#fffaf2;color:#29241f;font:16px/1.6 Georgia,serif}body{margin:24px}h1{font-size:24px;line-height:1.2}</style></head><body><h1>Artifact behind the conversation</h1>" + "<p>Artifact content keeps its original frame.</p>" * 40 + "<p id='end'>Final artifact paragraph.</p></body></html>"))
        page.goto(url + "?overlay=1#/workspace/" + ("t_overlay0" if ticket_host else "chief-of-staff"), wait_until="domcontentloaded")
        pane = page.locator('[data-conversation-pane]')
        box = page.locator('[data-conversation-input]')
        document = page.locator('.ticket-doc' if ticket_host else '.chief-workspace .manager-doc')
        final_text = 'Final document paragraph.' if ticket_host else 'Overlay document row 35'
        try:
            expect(box).to_be_visible()
        except AssertionError:
            print('OVERLAY_ERRORS ' + repr(errors),flush=True)
            raise
        if ticket_host:
            grip = page.get_by_role('separator', name='Resize workspace panel')
            grip_box = grip.bounding_box()
            page.mouse.move(grip_box['x'] + grip_box['width']/2, grip_box['y'] + grip_box['height']/2)
            page.mouse.down()
            page.mouse.move(width - 440, grip_box['y'] + grip_box['height']/2, steps=5)
            page.mouse.up()
            page.wait_for_timeout(100)
        expect(document.get_by_text(final_text, exact=True)).to_have_count(1)
        # Initial selection stays product-owned. Choose dock state for this proof.
        if pane.get_attribute('data-conversation-state') == 'opened':
            page.locator('[data-conversation-collapse]').click()
        # Open by the same controls as an owner. A viewport read follows each layout.
        box.click()
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        box.fill("Draft keeps its place.\n" * 6)
        page.wait_for_timeout(250)
        doc_before = document.bounding_box()
        dock_before = page.locator('.conversation-layer').bounding_box()
        assert doc_before['y'] + doc_before['height'] <= dock_before['y'] + 1, (name, doc_before, dock_before)
        document.evaluate('e=>e.scrollTop=240')
        scroll_before = document.evaluate('e=>e.scrollTop')
        page.locator('[data-conversation-expand]').click()
        expect(pane).to_have_attribute('data-conversation-state', 'opened')
        page.wait_for_timeout(250)
        doc_open = document.bounding_box()
        card_open = pane.bounding_box()
        title = document.locator('.ticket-title' if ticket_host else '.manager-workspace-title').bounding_box()
        assert abs(card_open['x'] - title['x']) <= 1, (name, card_open, title)
        assert abs(card_open['x'] + card_open['width'] - title['x'] - title['width']) <= 1, (name, card_open, title)
        assert doc_before == doc_open, (name, doc_before, doc_open)
        assert document.evaluate('e=>e.scrollTop') == scroll_before
        assert card_open['y'] < doc_open['y'] + doc_open['height'] and card_open['y'] + card_open['height'] > doc_open['y']
        host = page.locator('.ticket-page' if ticket_host else '.chief-workspace.manager-page').bounding_box()
        assert card_open['width'] <= host['width'] and card_open['x'] >= host['x']
        if name == 'wide-chief':
            assert 1152 < host['width'] < width and host['x'] > 0, (name, host)
        if ticket_host:
            assert host['width'] < 500, host
        # The transcript owns its own trailing clearance and follows its final row.
        thread = page.locator('[data-conversation-thread]')
        thread.focus()
        page.keyboard.press('End')
        page.wait_for_function('''() => {
          const thread=document.querySelector('[data-conversation-thread]');
          return thread && thread.scrollHeight-thread.clientHeight-thread.scrollTop<=1;
        }''')
        tail = page.get_by_text('Owner answer 2000.', exact=False).last
        expect(tail).to_be_in_viewport()
        def transcript_bounds(page=page):
            return page.evaluate('''() => {const e=document.querySelector('[data-conversation-thread]'),r=e.getBoundingClientRect();const rows=[...e.querySelectorAll('[data-conversation-row]')];const tail=rows.at(-1)?.getBoundingClientRect();return {top:r.top,bottom:r.bottom,scrollTop:e.scrollTop,scrollHeight:e.scrollHeight,clientHeight:e.clientHeight,tail:tail?{top:tail.top,bottom:tail.bottom}:null};}''')
        transcript_open = transcript_bounds()
        assert transcript_open['tail']['bottom'] <= transcript_open['bottom'] + 1, (name, transcript_open)
        highwater = document.evaluate('e=>e.scrollHeight')
        # A scrollback reader keeps the same content while the composer changes.
        # Reader input releases an older exact-text destination before scrollback.
        thread.focus()
        growth = box.evaluate("e=>parseFloat(getComputedStyle(e.closest('.ticket-page,.manager-page')??e.closest('[data-conversation-pane]')).getPropertyValue('--composer-growth'))||0")
        establish_scrollback(page, lambda page=page: page.keyboard.press('PageUp'),
                             min_clearance=growth + 2, input_kind='keydown', key='PageUp')
        anchor = thread.evaluate('''e=>{const r=e.getBoundingClientRect();const row=[...e.querySelectorAll('[data-conversation-row]')].find(n=>n.getBoundingClientRect().bottom>r.top+10);return row?{key:row.closest('[data-conversation-view-key]')?.getAttribute('data-conversation-view-key'),text:row.textContent,top:row.getBoundingClientRect().top-r.top,bottomOffset:row.getBoundingClientRect().top-r.bottom}:null;}''')
        assert anchor and anchor['key'], (name, anchor)
        anchor_proof = []
        def assert_anchor(label, *, edge='top', anchor=anchor, thread=thread, name=name, anchor_proof=anchor_proof):
            measured = thread.evaluate('''(e,key)=>{const row=[...e.querySelectorAll('[data-conversation-view-key]')].find(n=>n.dataset.conversationViewKey===key);const content=row?.querySelector('[data-conversation-row]')??row;const bounds=e.getBoundingClientRect();return content?{text:content.textContent,top:content.getBoundingClientRect().top-bounds.top,bottomOffset:content.getBoundingClientRect().top-bounds.bottom}:null;}''', anchor['key'])
            coordinate = 'bottomOffset' if edge == 'bottom' else 'top'
            anchor_proof.append({'operation':label,'edge':edge,'before':anchor,'after':measured,'tolerance':2})
            print('ANCHOR_GEOMETRY '+json.dumps({'name':name,**anchor_proof[-1]}),flush=True)
            assert measured and measured['text'] == anchor['text'] and abs(measured[coordinate] - anchor[coordinate]) <= 2, (name,label,anchor,measured)
            return measured
        box.fill('A shorter draft')
        page.wait_for_timeout(250)
        assert box.input_value() == 'A shorter draft'
        assert_anchor('composer shrink')
        box.fill('Draft grows again.\n' * 6)
        page.wait_for_timeout(250)
        assert_anchor('composer growth')
        # Collapse cannot remove document scroll range or clamp the reader.
        document.evaluate('e=>e.scrollTop=e.scrollHeight')
        end_scroll = document.evaluate('e=>e.scrollTop')
        collapse_before = {'thread':transcript_bounds(),'anchor':anchor}
        page.locator('[data-conversation-collapse]').click()
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        page.wait_for_timeout(250)
        assert not thread.is_visible()
        assert document.evaluate('e=>e.scrollHeight') >= highwater
        assert document.evaluate('e=>e.scrollTop') == end_scroll
        # Dock state keeps the mounted reader and its full-conversation anchor.
        page.locator('[data-conversation-expand]').click()
        page.wait_for_timeout(250)
        assert_anchor('return from dock')
        collapse_after = {'thread':transcript_bounds(),'anchor':anchor}
        page.locator('[data-conversation-collapse]').click()
        page.wait_for_timeout(250)
        box.click()
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        final = document.get_by_text(final_text, exact=True)
        document.evaluate('e=>e.scrollTop=e.scrollHeight')
        page.wait_for_timeout(100)
        card_peek = pane.bounding_box()
        final_box = final.bounding_box()
        # Wheel over uncovered document space above the full-column card.
        uncovered_point = {'x':host['x'] + host['width']/2, 'y':(host['y'] + card_peek['y'])/2}
        assert page.evaluate('point=>Boolean(document.elementFromPoint(point.x,point.y)?.closest(".ticket-doc,.manager-doc"))', uncovered_point), (name, uncovered_point)
        page.mouse.move(uncovered_point['x'], uncovered_point['y'])
        page.mouse.wheel(0, final_box['y'] + final_box['height'] - card_peek['y'] + 24)
        page.wait_for_timeout(180)
        final_box = final.bounding_box()
        assert final_box['y'] >= host['y'], (name, final_box, host, page.evaluate('''([x,y])=>document.elementFromPoint(x,y)?.outerHTML.slice(0,500)''',[uncovered_point['x'],uncovered_point['y']]),document.evaluate('e=>e.scrollTop'))
        assert final_box['y'] + final_box['height'] <= card_peek['y'] + 1, (name, final_box, card_peek)
        page.screenshot(path=str(evidence / f'{name}-document-tail.png'), animations='disabled')
        # Simulated keyboard reduction plus displacement stays inside the host intersection.
        page.evaluate('window.__keyboardViewport(100, 420)')
        page.wait_for_timeout(250)
        card_keyboard = pane.bounding_box()
        assert card_keyboard['y'] >= max(host['y'], 100) - 1, (name, card_keyboard, host)
        assert card_keyboard['y'] + card_keyboard['height'] <= min(host['y'] + host['height'], 520) + 1, (name, card_keyboard, host)
        # Full conversation follows the displaced viewport without losing scrollback.
        page.locator('[data-conversation-expand]').click()
        page.wait_for_timeout(250)
        assert_anchor('keyboard reduction and displacement')
        thread.evaluate('e=>e.scrollTop=e.scrollHeight')
        page.wait_for_timeout(100)
        expect(tail).to_be_in_viewport()
        keyboard_transcript = transcript_bounds()
        assert keyboard_transcript['tail']['bottom'] <= keyboard_transcript['bottom'] + 1, (name, keyboard_transcript)
        page.screenshot(path=str(evidence / f'{name}-keyboard.png'), animations='disabled')
        page.evaluate('window.__keyboardViewport(0, innerHeight)')
        document.evaluate('e=>e.scrollTop=0')
        page.wait_for_timeout(250)
        thread.evaluate('e=>e.scrollTop=e.scrollHeight')
        page.wait_for_timeout(100)
        page.screenshot(path=str(evidence / f'{name}.png'), animations='disabled')
        page.locator('[data-conversation-collapse]').click()
        page.wait_for_timeout(250)
        artifact_proof = None
        if ticket_host:
            page.evaluate('window.__keyboardViewport(0, innerHeight)')
            page.wait_for_timeout(200)
            doc_scroll_before_artifact = document.evaluate('e=>e.scrollTop')
            page.evaluate("location.hash='#/workspace/t_overlay0?source=ticket&ticket=t_overlay0&path=overlay.html'")
            artifact = page.locator('[data-ticket-artifact]')
            frame = artifact.locator('iframe')
            expect(frame).to_be_visible()
            expect(artifact.locator('[data-ticket-artifact-close]')).to_be_in_viewport()
            frame_before = frame.bounding_box()
            page.locator('[data-conversation-expand]').click()
            page.wait_for_timeout(250)
            assert frame.bounding_box() == frame_before
            page.locator('[data-conversation-collapse]').click()
            page.wait_for_timeout(250)
            artifact_scroll = artifact.locator('.ticket-artifact-body')
            card = pane.bounding_box()
            frame_tail = frame.bounding_box()
            assert frame_tail['y'] + frame_tail['height'] <= card['y'] + 1, (frame_tail, card)
            assert frame_tail['height'] == frame_before['height']
            artifact.frame_locator('iframe').locator('#end').evaluate('e=>e.ownerDocument.defaultView.scrollTo(0,e.ownerDocument.documentElement.scrollHeight)')
            page.wait_for_timeout(100)
            artifact_end = artifact.frame_locator('iframe').locator('#end').bounding_box()
            assert artifact_end['y'] + artifact_end['height'] <= card['y'] + 1, (artifact_end,card)
            assert artifact_end['y'] >= artifact_scroll.bounding_box()['y'], (artifact_end,artifact_scroll.bounding_box())
            page.screenshot(path=str(evidence / 'narrow-ticket-artifact-tail.png'), animations='disabled')
            artifact_scroll.evaluate('e=>e.scrollTop=0')
            page.wait_for_timeout(100)
            expect(artifact.locator('[data-ticket-artifact-refresh]')).to_be_in_viewport()
            page.screenshot(path=str(evidence / 'narrow-ticket-artifact.png'), animations='disabled')
            artifact_proof = {'frameOriginal':frame_before,'frameTail':frame_tail,'finalParagraph':artifact_end,'scrollHeight':artifact_scroll.evaluate('e=>e.scrollHeight'),'clientHeight':artifact_scroll.evaluate('e=>e.clientHeight')}
            artifact.locator('[data-ticket-artifact-close]').click()
            expect(artifact).to_have_count(0)
            assert document.evaluate('e=>e.scrollTop') == doc_scroll_before_artifact
        proof['hosts'].append({'name':name,'host':host,'titleChannel':title,'document':doc_open,'openedCard':card_open,'scrollTop':scroll_before,'retainedScrollHeight':highwater,'documentTail':final_box,'transcriptOpened':transcript_open,'scrollbackAnchor':anchor,'anchorChecks':anchor_proof,'collapse':{'before':collapse_before,'after':collapse_after},'keyboardCard':card_keyboard,'keyboardTranscript':keyboard_transcript,'artifact':artifact_proof})
        draft_before_dismissal = box.input_value()
        doc_before_dismissal = document.evaluate('e=>e.scrollTop')
        point = {'x':host['x'] + host['width']/2, 'y':(host['y'] + pane.bounding_box()['y'])/2}
        hit = page.evaluate('''point=>Boolean(document.elementFromPoint(point.x,point.y)?.closest('.ticket-doc,.manager-doc'))''', point)
        assert hit, (name,point)
        page.mouse.click(point['x'], point['y'])
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        assert box.input_value() == draft_before_dismissal
        assert document.evaluate('e=>e.scrollTop') == doc_before_dismissal
        proof['hosts'][-1]['documentClickAboveCard'] = {'point':point,'documentHit':hit,'draftPreserved':True,'documentScrollPreserved':True}
        assert not errors, errors
        context.close()
    proof['seconds'] = time.perf_counter() - started
    (evidence / 'overlay-geometry.json').write_text(json.dumps(proof, indent=2))
    print('OVERLAY_PROOF ' + json.dumps(proof), flush=True)


def verify_rail_overflow(page, fixture, evidence):
    """Use the same host to prove real flex sizing and native scroll boundaries.

    Synthetic geometry cannot expose min-content width or programmatic horizontal
    scroll. Existing fold motion checks above retain the independent motion proof.
    """
    started = time.perf_counter()
    fixture["overflow"] = True
    rail = page.locator(".workspace-rail")
    active = page.locator("[data-active-work-scroll]")
    settled = page.locator("[data-settled-scroll]")
    head = page.locator(".settled-head")
    grip = page.get_by_role("separator", name="Resize workspace panel")
    proof = []

    def status_geometry(word="Deploying"):
        status = page.locator(".status-card:visible")
        expect(status).to_contain_text(word)
        bounds = status.bounding_box()
        rail_bounds = rail.bounding_box()
        viewport = page.viewport_size
        assert bounds and rail_bounds and viewport
        assert bounds["x"] >= rail_bounds["x"] and bounds["x"] + bounds["width"] <= rail_bounds["x"] + rail_bounds["width"] + 1, bounds
        assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= viewport["height"], bounds
        for scroller in [active, *([settled] if settled.count() else [])]:
            box = scroller.bounding_box()
            assert box and box["y"] + box["height"] <= bounds["y"] + 1, (box, bounds)
        return bounds

    def geometry(scroller):
        return scroller.evaluate("""node => {
          node.scrollLeft=100;
          const style=getComputedStyle(node);
          return {clientWidth:node.clientWidth, scrollWidth:node.scrollWidth, scrollLeft:node.scrollLeft,
            clientHeight:node.clientHeight, scrollHeight:node.scrollHeight,
            overflowX:style.overflowX, overflowY:style.overflowY, gutter:style.scrollbarGutter,
            paddingRight:style.paddingRight, marginRight:style.marginRight};
        }""")

    for width in [232, "default", 520]:
        if width == "default":
            grip.dblclick()
        else:
            grip.focus()
            for _ in range(20):
                page.keyboard.press("Shift+ArrowLeft" if width == 232 else "Shift+ArrowRight")
            expect(grip).to_have_attribute("aria-valuenow", str(width))
        for count in [1, 24]:
            fixture["count"] = count
            fixture["active_count"] = count
            page.evaluate("void window.__refreshNavigation()")
            expect(rail.locator(".manager-card:not(.chief)")).to_have_count(count)
            expect(head).to_contain_text(f"({count})")
            for opened in [False, True]:
                if head.get_attribute("aria-expanded") != str(opened).lower():
                    head.click()
                expect(settled).to_have_count(1 if opened else 0)
                page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
                if opened:
                    expect(settled.locator("[data-settled-item]")).to_have_count(count)
                for scroller in [active, *([settled] if opened else [])]:
                    values = geometry(scroller)
                    proof.append({"width": width, "count": count, "open": opened,
                                  "scroller": scroller.get_attribute("class"), **values})
                    assert values["scrollWidth"] == values["clientWidth"], proof[-1]
                    assert values["scrollLeft"] == 0, proof[-1]
                    assert values["overflowY"] == "auto" and values["gutter"] == "stable", proof[-1]
                    if count == 24:
                        assert values["scrollHeight"] > values["clientHeight"], proof[-1]
                    scroller.evaluate("node => node.scrollTop=node.scrollHeight")
                    last = scroller.locator(".manager-card:not(.chief)" if scroller == active else "[data-settled-item]").last
                    control = last.locator(".settle" if scroller == active else ".reopen")
                    page.keyboard.press("Tab")
                    control.focus()
                    expect(control).to_be_in_viewport()
                    expect(control).to_have_css("opacity", "1")
                    focus = control.evaluate("""node => {
                      const box=node.getBoundingClientRect(), clip=node.closest('[data-active-work-scroll],[data-settled-scroll]').getBoundingClientRect();
                      const style=getComputedStyle(node), extent=parseFloat(style.outlineWidth)+parseFloat(style.outlineOffset);
                      return {focused:document.activeElement===node, outline:style.outlineStyle,
                        inside:box.left-extent>=clip.left && box.right+extent<=clip.right};
                    }""")
                    assert focus["focused"] and focus["outline"] == "solid" and focus["inside"], focus
                    scroller.evaluate("node => node.scrollTop=0")
                assert rail.evaluate("node => node.scrollWidth === node.clientWidth")
                status_geometry()
        page.screenshot(path=str(evidence / f"rail-overflow-{width}.png"))
        if width == 232:
            # Reproduce the old layout in browser memory only for review evidence.
            old_style = page.add_style_tag(content="""
                .active-work-scroll,.settled-body { overflow:auto!important; }
                .settled-title>span { min-width:auto!important; overflow-wrap:normal!important; }
                .manager-eyebrow.rail>span { min-width:auto!important; overflow-wrap:normal!important; }
                .manager-eyebrow.rail .settle { flex-shrink:1!important; }
            """)
            before = {"active": geometry(active), "settled": geometry(settled)}
            page.screenshot(path=str(evidence / "rail-overflow-232-before.png"))
            old_style.evaluate("node => node.remove()")
            proof.append({"browserOnlyOriginalCss": before})
            assert before["active"]["scrollWidth"] > before["active"]["clientWidth"], before
            assert before["settled"]["scrollWidth"] > before["settled"]["clientWidth"], before
    for viewport_width in [721, 720]:
        page.set_viewport_size({"width": viewport_width, "height": 700})
        if viewport_width == 720:
            page.locator('.workspace-mobile-nav [data-screen="workspace"]').click()
        expect(rail).to_be_visible()
        status_geometry()
        assert geometry(active)["scrollLeft"] == 0
        assert geometry(settled)["scrollLeft"] == 0
        page.screenshot(path=str(evidence / f"rail-overflow-status-{viewport_width}.png"))
    # The predecessor's real prompt shares this slot. Exercise its public monitor
    # and Update control, rather than a second host or a simulated status card.
    fixture["release_sha"] = "fixture-next"
    page.evaluate("dispatchEvent(new Event('pageshow'))")
    status_samples = []
    for state, word in [("ready", "New version ready"), ("held", "Page refresh waits until this message is safe")]:
        if state == "held":
            page.evaluate("document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:true}}))")
            page.locator("[data-shell-refresh]:visible").click()
        for viewport_width in [721, 720]:
            page.set_viewport_size({"width": viewport_width, "height": 700})
            expect(rail).to_be_visible()
            if viewport_width == 721:
                grip.focus()
                for _ in range(20):
                    page.keyboard.press("Shift+ArrowLeft")
                expect(grip).to_have_attribute("aria-valuenow", "232")
            bounds = status_geometry(word)
            status = page.locator(".status-card:visible")
            assert status.locator(".status-action").count() == (1 if state == "ready" else 0)
            values = []
            for scroller, selector in [(active, ".manager-card:not(.chief) .settle"), (settled, ".reopen")]:
                dimensions = geometry(scroller)
                assert dimensions["scrollWidth"] == dimensions["clientWidth"] and dimensions["scrollLeft"] == 0, dimensions
                assert dimensions["overflowY"] == "auto" and dimensions["gutter"] == "stable", dimensions
                assert dimensions["scrollHeight"] > dimensions["clientHeight"], dimensions
                scroller.evaluate("node => node.scrollTop=node.scrollHeight")
                control = scroller.locator(selector).last
                page.keyboard.press("Tab")
                control.focus()
                expect(control).to_be_in_viewport()
                expect(control).to_have_css("opacity", "1")
                expect(control).to_have_css("outline-style", "solid")
                focus_inside = control.evaluate("""node => {
                  const box=node.getBoundingClientRect(), clip=node.closest('[data-active-work-scroll],[data-settled-scroll]').getBoundingClientRect();
                  const style=getComputedStyle(node), extent=parseFloat(style.outlineWidth)+parseFloat(style.outlineOffset);
                  return document.activeElement===node && box.left-extent>=clip.left && box.right+extent<=clip.right;
                }""")
                assert focus_inside
                scroller.evaluate("node => node.scrollTop=0")
                values.append(dimensions)
            status_samples.append({"state": state, "viewportWidth": viewport_width, "status": bounds, "scrollers": values})
            page.screenshot(path=str(evidence / f"rail-overflow-status-{state}-{viewport_width}.png"))
    (evidence / "rail-overflow-status-geometry.json").write_text(json.dumps(status_samples, indent=2))
    (evidence / "rail-overflow-geometry.json").write_text(json.dumps({
        "seconds": round(time.perf_counter() - started, 3), "newHosts": 0, "samples": proof,
    }, indent=2))


def verify_owner_updates(browser, url, *, problem_only=False):
    """Browser clicks cross real owner routes and durable receipts on the existing host.

    The provider records external dispatch only. The Problem composition must keep
    confirmation, retry, and cancellation usable beside the failure explanation.
    The narrow problem_only path skips unrelated recovery and cutover checks.
    A synthetic SHA change proves refresh hold, not deployment or database migration.
    """
    from dataclasses import replace

    started = time.perf_counter()
    backend = OwnerUpdatesFixture()
    navigation = runpy.run_path(str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py"))
    source = navigation["navigation_response"]({}, {"chief_of_staff": {"label": "Chief", "conversation_id": "bounded-reader"}})
    deployment = {"state": "idle", "deployed_sha": None, "target_sha": None, "outcome": None, "detail": None, "valid_until": None, "success_display_until": None}
    posts = []
    documents = []
    navigation_health = {"mode": "current", "generation": 1, "reason": None}
    errors = []
    context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    evidence = Path(os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR", "/tmp/panels-owner-updates")) / "owner-updates"
    evidence.mkdir(parents=True, exist_ok=True)

    def respond(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        if path.startswith("/api/updates") or path == "/api/meta":
            if route.request.method != "GET":
                posts.append({"path": path, "body": route.request.post_data_json,
                              "boot_sha": route.request.headers.get("x-panels-app-sha")})
            response = backend.client.request(route.request.method, path,
                                              content=route.request.post_data,
                                              headers={"Content-Type": "application/json"})
            route.fulfill(status=response.status_code, content_type="application/json", body=response.content)
            return
        if path == "/api/navigation/view/snapshot":
            view = json.loads(parse_qs(parts.query)["view"][0])
            authority = {**source["authority"], "availability": {
                "state": "current" if navigation_health["mode"] == "current" else "unavailable",
                "generation": str(navigation_health["generation"]), "reason": navigation_health["reason"],
            }, "permit": source["authority"]["permit"] if navigation_health["mode"] == "current" else None}
            projected = {**source, "mode": navigation_health["mode"], "authority": authority}
            route.fulfill(json=navigation["scoped_navigation_response"](projected, view))
            return
        key = parse_qs(parts.query).get("key", [path])[0] if path == "/api/resource-body" else path
        values = {
            "/api/workers/chief-of-staff/settings": {"employee_id": "chief", "label": "Chief", "recap": "Owner updates", "skill": None, "launch_defaults": {"employee_backend": "codex", "employee_launch_model": None, "employee_launch_reasoning_effort": None}},
            "/api/chief/conversation/start-values": {"backend_key": "codex", "model": None, "reasoning_effort": None},
            "/api/worker-types?supported=true": {"worker_types": []},
            "/api/feedback/count": {"open_count": 0},
            "/api/deployment-status": deployment,
            "/api/review": {"items": [], "running_worker_count": 0},
        }
        body = values.get(key, {})
        route.fulfill(json={"key": key, "epoch": "owner-updates", "revision": "1", "body": body} if path == "/api/resource-body" else body)

    def host_with_boot_sha(route):
        documents.append(backend.current_sha)
        response = route.fetch()
        route.fulfill(response=response, body=response.text().replace(
            "<head>", f'<head><meta name="panels-app-sha" content="{backend.current_sha}">', 1))

    page.route("**/api/**", respond)
    page.route(url + "?*", host_with_boot_sha)
    card = page.locator(".status-card:visible")
    box = page.locator("[data-conversation-input]")

    def signal():
        with page.expect_response(lambda response: urlsplit(response.url).path == "/api/updates" and response.status == 200):
            page.evaluate("window.__ownerUpdatesSignal()")

    try:
        page.goto(url + "?overlay=1&owner-updates=1#/workspace/chief-of-staff", wait_until="domcontentloaded")
        expect(box).to_be_visible()
        expect(card.get_by_role("button", name="Update now", exact=True)).to_be_visible()
        page.screenshot(path=str(evidence / "update-available.png"))
        # Reuse this App, actual navigation transport, and composer. No new host.
        health_started = time.perf_counter()
        health = card.locator("[data-navigation-health]")
        retained_draft = "Keep this draft through navigation availability changes."
        box.fill(retained_draft)
        page.evaluate("window.__navigationDocument = document; window.__disconnectNavigation()")
        expect(health).to_have_attribute("data-navigation-health", "reconnecting")
        expect(health).to_have_text("Navigation reconnecting")
        expect(box).to_have_value(retained_draft)
        expect(page.locator("[data-chief-conversation-error], [data-chief-conversation-retry], .rail-work .error-line, .chief-workspace .error-line")).to_have_count(0)
        expect(card.get_by_role("button", name="Update now", exact=True)).to_be_visible()
        page.screenshot(path=str(evidence / "navigation-reconnecting.png"))
        page.evaluate("window.__navigationOffline = false")
        expect(health).to_have_count(0)
        reason = "Runtime <script> is unavailable. " + "The server keeps durable navigation data while runtime authority recovers. " * 3
        navigation_health.update(mode="degraded", generation=2, reason=reason)
        page.evaluate("void window.__refreshNavigation()")
        expect(health).to_have_attribute("data-navigation-health", "degraded")
        expect(health.get_by_text("Navigation degraded", exact=True)).to_be_visible()
        expect(health.locator(".navigation-reason")).to_have_text(reason)
        expect(health.locator("script")).to_have_count(0)
        expect(box).to_have_value(retained_draft)
        page.screenshot(path=str(evidence / "navigation-degraded-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        compact_health = page.locator(".compact-bar [data-navigation-health]")
        expect(compact_health).to_be_visible()
        expect(compact_health.locator(".navigation-reason")).to_have_text(reason)
        dimensions = compact_health.evaluate("node => ({width:node.clientWidth, scroll:node.scrollWidth, reasonHeight:node.querySelector('.navigation-reason').getBoundingClientRect().height, lineHeight:parseFloat(getComputedStyle(node).lineHeight)})")
        assert dimensions["scroll"] == dimensions["width"], dimensions
        assert dimensions["reasonHeight"] > dimensions["lineHeight"] * 2, dimensions
        page.screenshot(path=str(evidence / "navigation-degraded-compact.png"))
        page.set_viewport_size({"width": 1280, "height": 900})
        expect(health).to_have_attribute("data-navigation-health", "degraded")
        assert page.evaluate("document === window.__navigationDocument")
        navigation_seconds = time.perf_counter() - health_started
        if not problem_only:
            # A live connection must still disclose an expired runner record. Then the
            # normal update controls return through the existing contentless change signal.
            recovery = "Recovery required: the candidate did not complete its commit."
            deployment.update(state="unknown", detail=recovery)
            signal()
            expect(card.get_by_text(recovery, exact=True)).to_be_visible()
            expect(card.get_by_text("Reconnecting", exact=True)).to_be_visible()
            page.screenshot(path=str(evidence / "recovery-required.png"))
            deployment.update(state="idle", detail=None)
            signal()
            expect(card.get_by_role("button", name="Update now", exact=True)).to_be_visible()

        # The actual ShellStatus composition must retain controls under a permanent
        # deployment failure. Opening confirmation before the transition also proves
        # that the shared UpdateActions instance keeps its local decision state.
        failure = "The previous update failed. The installed application remains available."
        card.get_by_role("button", name="Update now", exact=True).click()
        expect(card.get_by_role("button", name="Proceed with update", exact=True)).to_be_visible()
        deployment.update(state="problem", outcome="failed", detail=failure)
        signal()
        expect(card.get_by_text("Problem", exact=True)).to_be_visible()
        expect(card.get_by_text(failure, exact=True)).to_be_visible()
        expect(card.get_by_role("button", name="Proceed with update", exact=True)).to_be_visible()
        deployment.update(state="idle", outcome=None, detail=None)
        signal()
        expect(card.get_by_role("button", name="Proceed with update", exact=True)).to_be_visible()
        deployment.update(state="problem", outcome="failed", detail=failure)
        signal()
        expect(card.get_by_role("button", name="Proceed with update", exact=True)).to_be_visible()
        card.get_by_role("button", name="Go back", exact=True).click()
        expect(card.get_by_role("button", name="Update now", exact=True)).to_be_visible()
        expect(health).to_have_attribute("data-navigation-health", "degraded")
        expect(box).to_have_value(retained_draft)
        page.screenshot(path=str(evidence / "problem-with-update-actions.png"))
        navigation_health.update(mode="current", generation=3, reason=None)
        page.evaluate("void window.__refreshNavigation()")
        expect(health).to_have_count(0)
        expect(card.get_by_text("Problem", exact=True)).to_be_visible()
        expect(card.get_by_role("button", name="Update now", exact=True)).to_be_visible()
        expect(box).to_have_value(retained_draft)
        box.fill("")
        (evidence / "navigation-health-proof.json").write_text(json.dumps({
            "seconds": round(navigation_seconds, 3), "newHosts": 0,
            "boundary": "App + production scoped navigation transport + both shell mounts + retained composer + existing Problem and update controls",
            "compactReason": dimensions, "reason": reason,
        }, indent=2))

        for activity, warning in (("unknown", "Activity is unknown."), ("busy", "Work is active.")):
            backend.activity.activity = activity
            signal()
            expect(card.locator("[data-update-activity]")).to_have_attribute("data-update-activity", activity)
            before_posts, before_dispatches = len(posts), len(backend.provider.dispatched)
            card.get_by_role("button", name="Update now", exact=True).click()
            expect(card.get_by_text(warning, exact=False)).to_be_visible()
            assert len(posts) == before_posts and len(backend.provider.dispatched) == before_dispatches
            if activity == "unknown":
                page.screenshot(path=str(evidence / "confirmation.png"))
            card.get_by_role("button", name="Proceed with update", exact=True).click()
            expect(card.get_by_text("Update queued", exact=False)).to_be_visible()
            receipt = backend.store.read()
            assert receipt is not None and receipt.state == "dispatched" and receipt.confirm_activity
            assert len(backend.provider.dispatched) == before_dispatches + 1
            body = posts[-1]["body"]
            selected = backend.provider.offered
            assert body == {"publication_run_id": selected.publication_run_id, "artifact_id": selected.artifact_id,
                            "proof_artifact_id": selected.proof_artifact_id, "app_sha": selected.manifest.app_sha,
                            "artifact_digest": selected.manifest.artifact_digest, "archive_sha256": selected.archive_sha256,
                            "mode": "now", "confirm_activity": True}
            assert posts[-1]["boot_sha"] == "a" * 40
            assert backend.provider.dispatched[-1].request_id == receipt.request_id
            expect(card.get_by_text("Problem", exact=True)).to_be_visible()
            expect(card.get_by_text(failure, exact=True)).to_be_visible()
            if activity == "busy":
                # A failed request leaves an available publication usable for retry.
                # Its failure and the deployment failure remain visible at each step.
                failed_request = "The runner refused the previous attempt."
                backend.store.transition(receipt.request_id, frozenset({"dispatched"}), "failed", detail=failed_request)
                signal()
                expect(card.get_by_text(failed_request, exact=True)).to_be_visible()
                card.get_by_role("button", name="Update now", exact=True).click()
                expect(card.get_by_text("Work is active.", exact=False)).to_be_visible()
                expect(card.get_by_text(failure, exact=True)).to_be_visible()
                expect(card.get_by_text(failed_request, exact=True)).to_be_visible()
                assert len(backend.provider.dispatched) == before_dispatches + 1
                page.screenshot(path=str(evidence / "problem-with-retry-confirmation.png"))
                card.get_by_role("button", name="Proceed with update", exact=True).click()
                expect(card.get_by_text("Update queued", exact=False)).to_be_visible()
                assert len(backend.provider.dispatched) == before_dispatches + 2
                expect(card.get_by_text(failure, exact=True)).to_be_visible()
            card.get_by_role("button", name="Cancel update", exact=True).click()
            expect(card.get_by_role("button", name="Update when idle", exact=True)).to_be_visible()
            expect(card.get_by_text(failure, exact=True)).to_be_visible()
            assert backend.store.read().state == "cancelled"

        backend.activity.activity = "unknown"
        signal()
        expect(card.locator("[data-update-activity]")).to_have_attribute("data-update-activity", "unknown")
        card.get_by_role("button", name="Update when idle", exact=True).click()
        expect(card.get_by_text("Update waits for idle", exact=False)).to_be_visible()
        assert backend.store.read().state == "pending"
        assert len(backend.provider.dispatched) == 3
        expect(card.get_by_text(failure, exact=True)).to_be_visible()
        page.screenshot(path=str(evidence / "problem-with-pending-cancel.png"))
        pinned = backend.store.read().release
        backend.provider.offered = replace(pinned, artifact_id=201, manifest=replace(pinned.manifest, app_sha="f" * 40))
        backend.service.refresh_available(force=True)
        signal()
        expect(card.get_by_text("Update waits for idle · bbbbbbb", exact=True)).to_be_visible()
        assert backend.store.read().release == pinned
        card.get_by_role("button", name="Cancel update", exact=True).click()
        expect(card.get_by_text("Update available · fffffff", exact=True)).to_be_visible()
        assert backend.store.read().state == "cancelled"
        expect(card.get_by_text(failure, exact=True)).to_be_visible()
        if problem_only:
            assert errors == [], errors
            (evidence / "problem-composition-proof.json").write_text(json.dumps({
                "seconds": round(time.perf_counter() - started, 3), "newHosts": 0,
                "dispatchCount": len(backend.provider.dispatched), "requests": posts,
                "boundary": "Mounted ShellStatus preserves Problem explanation and UpdateActions through confirmation, failed retry, and pending cancellation",
            }, indent=2))
            return

        deployment.update(state="idle", outcome=None, detail=None)
        backend.provider.offered = pinned
        backend.service.refresh_available(force=True)
        backend.activity.activity = "idle"
        signal()
        expect(card.get_by_text("Update available · bbbbbbb", exact=True)).to_be_visible()
        expect(card.locator("[data-update-activity]")).to_have_attribute("data-update-activity", "idle")
        card.get_by_role("button", name="Update now", exact=True).click()
        expect(card.get_by_text("Update queued", exact=False)).to_be_visible()
        receipt = backend.store.read()
        backend.store.transition(receipt.request_id, frozenset({"dispatched"}), "applying")
        signal()
        expect(card.get_by_text("Update in progress", exact=False)).to_be_visible()
        expect(card.get_by_role("button", name="Cancel update", exact=True)).to_have_count(0)
        assert len(backend.provider.dispatched) == 4

        # The application version changes synthetically. The retained App and its
        # actual composer keep the unsent draft until the owner clears it.
        draft = "Keep this unsent draft across the installed revision change."
        box.fill(draft)
        page.evaluate("window.__beforeUpdateDocument = document")
        backend.store.transition(receipt.request_id, frozenset({"applying"}), "applied")
        backend.current_sha = pinned.manifest.app_sha
        backend.service.current_app_sha = backend.current_sha
        signal()
        page.evaluate("dispatchEvent(new Event('pageshow'))")
        expect(card.get_by_text("Refresh page", exact=True)).to_be_visible()
        expect(box).to_have_value(draft)
        assert len(documents) == 1
        page.evaluate("window.__disconnectNavigation()")
        expect(health).to_have_attribute("data-navigation-health", "reconnecting")
        assert not health.evaluate("node => Boolean(node.closest('button'))")
        health.click()
        expect(card.locator("[data-shell-refresh]")).to_be_visible()
        assert page.evaluate("document === window.__beforeUpdateDocument")
        page.screenshot(path=str(evidence / "navigation-with-refresh-control.png"))
        card.locator("[data-shell-refresh]").click()
        expect(card.get_by_text("Page refresh waits until this message is safe", exact=True)).to_be_visible()
        expect(box).to_have_value(draft)
        assert page.evaluate("document === window.__beforeUpdateDocument")
        assert len(documents) == 1
        page.screenshot(path=str(evidence / "refresh-keeps-draft.png"))
        box.fill("")
        page.wait_for_function("document !== window.__beforeUpdateDocument")
        expect(box).to_be_visible()
        assert documents == ["a" * 40, "b" * 40]
        expect(page.get_by_text("Refresh page", exact=True)).to_have_count(0)
        assert errors == [], errors
        (evidence / "proof.json").write_text(json.dumps({
            "seconds": round(time.perf_counter() - started, 3), "newHosts": 0,
            "dispatchCount": len(backend.provider.dispatched), "requests": posts, "documentVersions": documents,
            "boundary": "Browser actions -> actual FastAPI owner routes -> UpdateService -> file receipt -> fake external dispatcher",
            "cutover": "Synthetic installed SHA; no service replacement, hosted ingress, or database migration proof",
        }, indent=2))
    finally:
        context.close()
        backend.close()


def verify_recap_pointer_floor(browser, page, url, fixture, respond, evidence):
    """Prove layout and stationary hit testing through the real request and projection.

    Unit hover tests cannot prove a ResizeObserver-driven panel stays under a pointer.
    Reuse the App host and navigation transport, with no live backend or new host.
    """
    started = time.perf_counter()
    worker = {"id": "recap_floor", "title": "Recap floor", "worker_type": "coding",
              "stage": "needs_implementation", "ticket_status": "agent", "recap": "Initial recap"}
    neighbor = {**worker, "id": "recap_neighbor", "title": "Other recap", "recap": "Short recap"}
    fixture.update(enabled=True, recap_worker=worker, board={"managers": [
        {"id": "recap_manager", "title": "Recap manager", "created_at": 1, "tickets": [worker, neighbor]},
    ]}, chief={"label": "Chief"})
    proof = {"newHosts": 0, "samples": [], "sessions": []}
    panel = page.locator('[data-rail-hover-panel]')
    text = panel.locator('[data-recap-text]')
    row = page.locator('[data-ticket-id="recap_floor"]')

    def mounted_projection(target_page=page):
        # Initial rail-parent delivery discovers each manager's ticket window.
        # Wait for that intended subscription before the single explicit refresh.
        target_page.wait_for_function("""() => {
          const sources=window.__navigationSources?.();
          if(sources?.length!==1)return false;
          const request=JSON.parse(new URL(sources[0].url,location.href).searchParams.get('view'));
          const managers=request.windows.filter(w=>w.section==='rail_tickets'&&w.owner?.kind==='manager');
          return managers.length===1&&managers[0].owner.id==='recap_manager';
        }""", timeout=5000)

    def refresh(target_page=page):
        required = []
        if target_page.locator('[data-rail-hover-panel][aria-hidden="false"]').count():
            required.append({"selector": '[data-rail-hover-panel][aria-hidden="false"] [data-recap-text]',
                             "text": worker["recap"]})
        refresh_projection(target_page, required_content=required,
                           required_recaps=[{"id": worker["id"], "text": worker["recap"]}])

    def open_panel():
        row.hover()
        expect(panel).to_have_attribute("aria-hidden", "false")
        page.wait_for_timeout(300)
        panel.hover()

    def leave():
        page.mouse.move(1200, 650)
        page.get_by_role("separator", name="Resize workspace panel").focus()
        expect(panel).to_have_attribute("aria-hidden", "true")

    def set_lines(lines):
        # Use real font metrics to create exactly the requested number of wrapped
        # lines. A long token wraps by the production overflow-wrap rule.
        count = panel.locator("[data-recap-slot]").evaluate("""(node, lines) => {
          const css=getComputedStyle(node), canvas=document.createElement('canvas');
          const ctx=canvas.getContext('2d'); ctx.font=css.font;
          return Math.floor(node.getBoundingClientRect().width/ctx.measureText('W').width)*(lines-1)+1;
        }""", lines)
        worker["recap"] = "W" * count
        refresh()
        expect(text).to_have_text(worker["recap"])
        assert text.evaluate("node => Math.round(node.getBoundingClientRect().height/parseFloat(getComputedStyle(node).lineHeight))") == lines
        return worker["recap"]

    def sample_change(label, mutate, floor=None, point=None):
        # Start before publication, so every animation frame includes actual panel
        # bounds and hit testing at a pointer that never moves during the change.
        page.evaluate("""({point}) => {
          window.__recapFrames=[];
          window.__recapSampleUntil=null;
          const start=performance.now();
          window.__recapSampling=new Promise(resolve=>{
            function sample(){
              const panel=document.querySelector('[data-rail-hover-panel]'), r=panel.getBoundingClientRect();
              window.__recapFrames.push({ms:performance.now()-start,x:r.x,y:r.y,width:r.width,height:r.height,
                visible:panel.getAttribute('aria-hidden')==='false',
                hit:point ? Boolean(document.elementFromPoint(point.x,point.y)?.closest('[data-rail-hover-panel]')) : null});
              if(window.__recapSampleUntil===null || performance.now()<window.__recapSampleUntil) requestAnimationFrame(sample); else resolve();
            } requestAnimationFrame(sample);
          });
        }""", {"point": point})
        mutate()
        page.evaluate("window.__recapSampleUntil=performance.now()+380")
        page.evaluate("() => window.__recapSampling")
        frames = page.evaluate("window.__recapFrames")
        proof["samples"].append({"label": label, "point": point, "floor": floor, "frames": frames})
        (evidence / "recap-floor-geometry.json").write_text(json.dumps(proof, indent=2))
        assert frames[-1]["ms"] >= 380, (label, frames[-1])
        if floor is not None:
            assert all(frame["height"] >= floor - 1 for frame in frames), (label, floor, frames)
        if point:
            assert all(frame["visible"] and frame["hit"] for frame in frames), (label, point, frames)
        return panel.bounding_box()["height"]

    def request():
        before = fixture.get("recap_requests", 0)
        ask = panel.get_by_role('button', name='Ask for a recap', exact=True)
        expect(ask).to_have_attribute('aria-disabled', 'false')
        with page.expect_response('**/api/recap-requests'):
            ask.click()
        assert fixture["recap_requests"] == before + 1
        refresh()
        expect(text).to_have_count(0)

    def keyboard_request(before_request=None):
        expect(panel.locator('[data-request-recap]')).to_have_attribute('data-request-recap', 'ticket:recap_floor')
        ask = panel.get_by_role('button', name='Ask for a recap', exact=True)
        # HTTP delivery precedes mutateJson's query invalidations. The action stays
        # busy until those finish, so a second Enter must wait for its public state.
        expect(ask).to_have_attribute('aria-disabled', 'false')
        ask.focus()
        expect(ask).to_be_focused()
        if before_request:
            before_request()
        with page.expect_response('**/api/recap-requests'):
            page.keyboard.press('Enter')

    for reduced, lower in [(False, False), (False, True), (True, False)]:
        page.set_viewport_size({"width": 1280, "height": 700})
        page.emulate_media(reduced_motion="reduce" if reduced else "no-preference")
        worker["recap"] = "Initial recap"
        page.mouse.move(1200, 650)
        page.goto(url + '#/workspace/utility/settings', wait_until='domcontentloaded')
        # A hash-only goto can retain the preceding case's navigation scope.
        page.reload(wait_until='domcontentloaded')
        mounted_projection()
        refresh()
        expect(row).to_be_visible()
        row.evaluate("node => node.style.marginTop=''")
        page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
        if lower:
            # Place the real row near the viewport bottom without a second host.
            # This exercises production viewport clamping and upward growth.
            row.evaluate("node => node.style.marginTop='390px'")
            row.scroll_into_view_if_needed()
            assert row.bounding_box()["y"] > 450
        open_panel()
        three = set_lines(3)
        page.wait_for_timeout(300)
        if not reduced and not lower:
            page.screenshot(path=str(evidence / "recap-floor-initial-three-lines.png"))
        floor = panel.bounding_box()["height"]
        ask = panel.get_by_role('button', name='Ask for a recap', exact=True).bounding_box()
        point = {"x": ask["x"] + ask["width"] / 2, "y": ask["y"] + ask["height"] / 2}
        page.mouse.move(point["x"], point["y"])
        tag = f'{"lower" if lower else "upper"}-{"reduced" if reduced else "normal"}'
        sample_change(tag + '-request-clears', request, floor, point)
        sample_change(tag + '-short-reply', lambda: set_lines(2), floor, point)
        page.screenshot(path=str(evidence / f'recap-floor-{tag}-two-lines.png'))
        grown = sample_change(tag + '-growth', lambda: set_lines(5), floor, point)
        assert grown > floor + 20, (tag, floor, grown, panel.inner_html())
        # The real Ask action can move on growth. Move to it once, then hold the
        # new stationary point for the repeated request and shorter response.
        ask = panel.get_by_role('button', name='Ask for a recap', exact=True).bounding_box()
        point = {"x": ask["x"] + ask["width"] / 2, "y": ask["y"] + ask["height"] / 2}
        page.mouse.move(point["x"], point["y"])
        sample_change(tag + '-repeat-request', request, grown, point)
        sample_change(tag + '-repeat-short-reply', lambda: set_lines(2), grown, point)
        leave()
        open_panel()
        natural = panel.bounding_box()["height"]
        if not reduced and not lower:
            page.screenshot(path=str(evidence / "recap-floor-returned-two-lines.png"))
        assert natural < floor - 10, (natural, floor)
        # A delayed response or no response cannot retain the old floor after exit.
        worker["recap"] = three
        refresh()
        page.wait_for_timeout(300)
        panel.hover()
        sample_change(tag + '-no-reply', request, floor)
        leave()
        open_panel()
        empty = panel.bounding_box()["height"]
        assert empty < natural - 10, (empty, natural)
        sample_change(tag + '-late-reply', lambda: set_lines(2))
        assert panel.bounding_box()["height"] < floor - 10
        # Retarget while the shared panel is already open. Its old floor must not
        # leak into the next Worker, even without an intervening hide.
        set_lines(5)
        page.wait_for_timeout(300)
        page.keyboard.press('Tab')
        page.locator('[data-ticket-id="recap_neighbor"]').focus()
        expect(panel.locator('.title')).to_contain_text('Other recap')
        page.wait_for_timeout(300)
        assert panel.bounding_box()["height"] < floor - 10
        proof["sessions"].append({"name": tag, "threeLines": floor, "largest": grown,
                                  "twoLinesAfterExit": natural, "emptyAfterExit": empty})
        leave()

    # Keyboard focus opens the same panel without pointer entry. It must shrink.
    page.emulate_media(reduced_motion="no-preference")
    worker["recap"] = "Initial recap"
    refresh()
    page.keyboard.press('Tab')
    row.focus()
    expect(panel).to_have_attribute('aria-hidden', 'false')
    set_lines(3)
    page.wait_for_timeout(300)
    before = panel.bounding_box()["height"]
    keyboard_request()
    refresh()
    expect(text).to_have_count(0)
    sample_change('keyboard-no-pointer', lambda: None)
    after = panel.bounding_box()["height"]
    assert after < before - 20
    assert any(after + 1 < frame["height"] < before - 1 for frame in proof["samples"][-1]["frames"]), proof["samples"][-1]
    proof["sessions"].append({"name": "keyboard", "before": before, "after": panel.bounding_box()["height"]})
    leave()

    # Enter during an ordinary shrink. The new pointer visit must hold the
    # visible intermediate height, rather than the smaller animation target.
    page.keyboard.press('Tab')
    row.focus()
    expect(panel).to_have_attribute('aria-hidden', 'false')
    set_lines(3)
    page.wait_for_timeout(300)
    full = panel.bounding_box()["height"]
    def pause_shrink():
        # Pause the browser-owned CSS transition at a known intermediate time.
        # A fixed delay can miss a short transition on a loaded machine.
        page.evaluate("""() => {
          window.__recapShrinkReady = new Promise((resolve, reject) => {
            const slot=document.querySelector('[data-rail-hover-panel] [data-recap-slot]');
            const started=performance.now();
            function sample(){
              const animation=slot.getAnimations().find(animation=>animation.transitionProperty==='height');
              if(animation && slot.style.height==='0px') {
                animation.pause();
                animation.currentTime=animation.effect.getTiming().duration/2;
                requestAnimationFrame(()=>resolve(slot.getBoundingClientRect().height));
              } else if(performance.now()-started>2000) reject(new Error('No ordinary recap shrink transition'));
              else requestAnimationFrame(sample);
            } requestAnimationFrame(sample);
          });
        }""")

    keyboard_request(pause_shrink)
    refresh()
    expect(text).to_have_count(0)
    page.evaluate('() => window.__recapShrinkReady')
    bounds = panel.bounding_box()
    point = {"x": bounds["x"] + 20, "y": bounds["y"] + 20}
    page.mouse.move(point["x"], point["y"])
    entered = panel.bounding_box()["height"]
    assert after + 1 < entered < full - 1, (after, entered, full)
    sample_change('enter-during-shrink', lambda: None, entered, point)
    proof["sessions"].append({"name": "enter-during-shrink", "full": full, "entered": entered})
    # The scroll dismissal hides the panel while the pointer remains on it.
    # Reopening must size to the empty recap, even when no reply ever arrives.
    page.locator('[data-active-work-scroll]').dispatch_event('scroll')
    expect(panel).to_have_attribute('aria-hidden', 'true')
    open_panel()
    assert panel.bounding_box()["height"] < entered - 1
    leave()

    # Real touch input (including its implicit pointerenter) must not acquire a floor.
    touch_context = browser.new_context(viewport={"width": 1280, "height": 700}, has_touch=True)
    touch_page = touch_context.new_page()
    touch_page.route('**/api/**', respond)
    worker["recap"] = three
    touch_page.goto(url + '#/workspace/utility/settings', wait_until='domcontentloaded')
    mounted_projection(touch_page)
    refresh(touch_page)
    touch_row = touch_page.locator('[data-ticket-id="recap_floor"]')
    expect(touch_row).to_be_visible()
    bounds = touch_row.bounding_box()
    session = touch_context.new_cdp_session(touch_page)
    session.send('Input.dispatchTouchEvent', {"type": "touchStart", "touchPoints": [{"x": bounds["x"] + 20, "y": bounds["y"] + 10}]})
    touch_panel = touch_page.locator('[data-rail-hover-panel]')
    expect(touch_panel).to_have_attribute('aria-hidden', 'false')
    expect(touch_panel.locator('.title')).to_contain_text('Recap floor')
    before = touch_panel.bounding_box()["height"]
    expect(touch_panel.get_by_role('button', name='Ask for a recap', exact=True)).to_have_attribute('aria-disabled', 'false')
    session.send('Input.dispatchTouchEvent', {"type": "touchEnd", "touchPoints": []})
    with touch_page.expect_response('**/api/recap-requests'):
        touch_panel.get_by_role('button', name='Ask for a recap', exact=True).tap()
    refresh(touch_page)
    expect(touch_panel.locator('[data-recap-text]')).to_have_count(0)
    touch_page.wait_for_timeout(380)
    after = touch_panel.bounding_box()["height"]
    assert after < before - 20, (before, after)
    proof["sessions"].append({"name": "touch", "before": before, "after": after})
    touch_page.screenshot(path=str(evidence / 'recap-floor-touch.png'))
    touch_context.close()
    fixture.pop("recap_worker")
    proof["seconds"] = round(time.perf_counter() - started, 3)
    (evidence / 'recap-floor-geometry.json').write_text(json.dumps(proof, indent=2))
    print('RECAP_FLOOR_PROOF ' + json.dumps({"seconds": proof["seconds"], "sessions": proof["sessions"]}), flush=True)
