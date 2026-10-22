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
            "fields": [],
        }
        for worker_type in worker_types
    ]
    unexpected = []
    metrics = MetricsFixture()
    metrics.append()
    metrics_calls = []

    fold = {"enabled": False, "count": 3, "overflow": False, "active_count": 1, "release_sha": "fixture-boot"}
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py"))

    def respond(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        if fold["enabled"] and path == "/api/navigation/view/snapshot":
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
            source = fixture["navigation_response"](board, {"chief_of_staff": {"label": "Chief"}})
            for record in source["snapshot"]["records"]:
                if record["kind"] == "manager":
                    record["value"]["updated_at"] = record["value"]["created_at"]
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
                "/api/deployment-status": {"state": "preparing", "deployed_sha": None, "target_sha": None, "outcome": None, "detail": None, "valid_until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))},
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
        elif path == "/api/deployment-interruptions":
            result = {"accounts": []}
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
        # A user-owned Ticket starts opened. Explicitly choose peeked for this proof.
        if pane.get_attribute('data-conversation-state') == 'opened':
            page.locator('[data-conversation-collapse]').click()
        # Open by the same controls as an owner. A viewport read follows each layout.
        box.click()
        expect(pane).to_have_attribute('data-conversation-state', 'peeked')
        page.wait_for_timeout(250)
        doc_before = document.bounding_box()
        document.evaluate('e=>e.scrollTop=240')
        scroll_before = document.evaluate('e=>e.scrollTop')
        box.fill("Draft keeps its place.\n" * 6)
        page.wait_for_timeout(250)
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
        thread.evaluate('e=>e.scrollTop=e.scrollHeight')
        page.wait_for_timeout(100)
        tail = page.get_by_text('Owner answer 2000.', exact=False).last
        expect(tail).to_be_in_viewport()
        def transcript_bounds(page=page):
            return page.evaluate('''() => {const e=document.querySelector('[data-conversation-thread]'),r=e.getBoundingClientRect();const rows=[...e.querySelectorAll('[data-conversation-row]')];const tail=rows.at(-1)?.getBoundingClientRect();return {top:r.top,bottom:r.bottom,scrollTop:e.scrollTop,scrollHeight:e.scrollHeight,clientHeight:e.clientHeight,tail:tail?{top:tail.top,bottom:tail.bottom}:null};}''')
        transcript_open = transcript_bounds()
        assert transcript_open['tail']['bottom'] <= transcript_open['bottom'] + 1, (name, transcript_open)
        highwater = document.evaluate('e=>e.scrollHeight')
        # A scrollback reader keeps the same content while the composer changes.
        thread.evaluate('e=>e.scrollTop=Math.max(0,e.scrollHeight-e.clientHeight-500)')
        page.wait_for_timeout(100)
        anchor = thread.evaluate('''e=>{const r=e.getBoundingClientRect();const row=[...e.querySelectorAll('[data-conversation-row]')].find(n=>n.getBoundingClientRect().bottom>r.top+10);return row?{key:row.closest('[data-conversation-view-key]')?.getAttribute('data-conversation-view-key'),text:row.textContent,top:row.getBoundingClientRect().top-r.top}:null;}''')
        assert anchor and anchor['key'], (name, anchor)
        def assert_anchor(label, thread=thread, anchor=anchor, name=name):
            measured = thread.evaluate('''(e,key)=>{const row=[...e.querySelectorAll('[data-conversation-view-key]')].find(n=>n.dataset.conversationViewKey===key);const content=row?.querySelector('[data-conversation-row]')??row;return content?{text:content.textContent,top:content.getBoundingClientRect().top-e.getBoundingClientRect().top}:null;}''', anchor['key'])
            assert measured and measured['text'] == anchor['text'] and abs(measured['top'] - anchor['top']) <= 2, (name,label,anchor,measured)
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
        page.locator('[data-conversation-collapse]').click()
        page.wait_for_timeout(250)
        assert document.evaluate('e=>e.scrollHeight') >= highwater
        assert document.evaluate('e=>e.scrollTop') == end_scroll
        assert_anchor('collapse to peeked')
        box.click()
        page.wait_for_timeout(250)
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
            assert artifact_scroll.evaluate('e=>e.scrollHeight>e.clientHeight')
            artifact_scroll.evaluate('e=>e.scrollTop=e.scrollHeight')
            page.wait_for_timeout(100)
            card = pane.bounding_box()
            artifact_scroll.evaluate('(e,bottom)=>{const frame=e.querySelector("iframe");e.scrollTop+=frame.getBoundingClientRect().bottom-bottom;}', card['y'] - 24)
            page.wait_for_timeout(100)
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
        proof['hosts'].append({'name':name,'host':host,'titleChannel':title,'document':doc_open,'openedCard':card_open,'scrollTop':scroll_before,'retainedScrollHeight':highwater,'documentTail':final_box,'transcriptOpened':transcript_open,'scrollbackAnchor':anchor,'keyboardCard':card_keyboard,'keyboardTranscript':keyboard_transcript,'artifact':artifact_proof})
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
    for state, word in [("ready", "New version ready"), ("held", "Update applies when this message is safe")]:
        if state == "held":
            page.evaluate("document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:true}}))")
            page.locator("button.status-card:visible").click()
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
