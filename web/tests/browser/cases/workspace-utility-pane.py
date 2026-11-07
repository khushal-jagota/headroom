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

# Every layout writer these checks wait on runs from an observer, a timer that marks
# its element while pending, or an animation frame. Settled means none of those marks
# remain, no finite animation runs, and the named boxes and scroll offsets read the
# same on consecutive animation frames.
_SETTLED = """([token, selectors, frames]) => {
  const busy = document.querySelector('[data-rail-motion-busy], .rail-wash--leaving, [data-conversation-motion], '
    + '[data-conversation-dock-transition], [data-recap-slot][style*="overflow: hidden"]')
    || document.getAnimations().some(a => a.playState === 'running' && a.effect?.getTiming().iterations !== Infinity);
  const key = JSON.stringify(selectors.flatMap(selector => [...document.querySelectorAll(selector)].map(node => {
    const r = node.getBoundingClientRect();
    return [r.x, r.y, r.width, r.height, node.scrollTop, node.scrollHeight];
  })));
  const state = window[token] ??= {key: null, count: 0};
  state.count = !busy && key === state.key ? state.count + 1 : 0;
  state.key = key;
  return state.count >= frames;
}"""
_settle_tokens = iter(range(1 << 30))


def wait_settled(page, *selectors, frames=3):
    token = f"__settled{next(_settle_tokens)}"
    page.wait_for_function(_SETTLED, arg=[token, list(selectors), frames], polling="raf")
    page.evaluate("token => delete window[token]", token)


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
    artifact_owner = {"id": "t_fixture", "title": "Wide artifact owner", "worker_type": "coding",
                      "conversation_id": None, "locale": "vps"}
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py"))

    def respond(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        # The standalone artifact resolves its owner and launch settings. Keep these
        # exact: an unrelated ticket read must still fail this case's request guard.
        if route.request.method == "GET" and path == "/api/tickets" and parse_qs(parts.query) == {"detail": ["full"], "id": [artifact_owner["id"]]}:
            route.fulfill(json=artifact_owner)
            return
        if route.request.method == "GET" and path == "/api/tickets/t_fixture/conversation/start-values" and not parts.query:
            route.fulfill(json={"backend_key": "codex", "model": None, "reasoning_effort": None})
            return
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
            source["snapshot"]["records"].extend(
                {"kind": "goal", "id": goal["id"], "value": {key: goal[key] for key in (
                    "title", "holder_kind", "holder_id", "active", "completed", "created_at", "updated_at")}}
                for goal in card_fixture.get("goals", [])
            )
            view = json.loads(parse_qs(parts.query)["view"][0])
            route.fulfill(json=fixture["scoped_navigation_response"](source, view))
            return
        if any(path in (f"/api/tickets/{identity}/workspace", f"/api/tickets/{identity}/conversation/start-values")
               for identity in card_fixture.get("rail_only_tickets", [])):
            # This proof selects actual rail controls. The unrelated ticket pane
            # is deliberately refused, as this case refuses other unused sources.
            route.fulfill(status=503, json={"error": {"code": "fixture", "message": "Rail selection proof only"}})
            return
        if path == "/api/recap-requests" and card_fixture.get("recap_worker"):
            assert route.request.post_data_json == {"target": {"kind": "ticket", "id": card_fixture["recap_worker"]["id"]}}
            card_fixture["recap_worker"]["recap"] = ""
            card_fixture["recap_requests"] = card_fixture.get("recap_requests", 0) + 1
            route.fulfill(json={"fate": "started"})
            return
        if path.startswith("/api/goals/") and card_fixture.get("goals"):
            route.fulfill(json={"backend_key": "codex", "model": None, "reasoning_effort": None}
                          if path.endswith("/conversation/start-values") else card_fixture["goals"][0])
            return
        if path.startswith("/api/vps-metrics/"):
            metrics_calls.append(path)
            response = metrics.client.request(route.request.method, path + ("?" + parts.query if parts.query else ""))
            route.fulfill(status=response.status_code, content_type="application/json", body=response.content)
            return
        if path == "/api/resource-versions":
            keys = json.loads(parse_qs(parts.query)["keys"][0])
            assert isinstance(keys, list) and all(isinstance(key, str) for key in keys), keys
            # Fixture bodies change directly, without source revisions. A null
            # revision uses their existing guarded body reads for this exact scope.
            route.fulfill(json={"epoch": "fixture", "resources": [
                {"key": key, "revision": None} for key in keys
            ]})
            return
        if path == "/api/resource-body":
            # Versioned reads arrive wrapped. The screen only needs the body.
            key = parse_qs(parts.query).get("key", [""])[0]
            if key in {f"/api/tickets/{identity}/workspace" for identity in card_fixture.get("rail_only_tickets", [])}:
                route.fulfill(status=503, json={"error": {"code": "fixture", "message": "Rail selection proof only"}})
                return
            inner = {
                "/api/goals": {"goals": card_fixture.get("goals", []), "status_date": time.strftime("%Y-%m-%d"), "next_refresh_at": int(time.time()) + 86400},
                "/api/worker-types?supported=true": {"worker_types": manifests},
                "/api/worker-types": {"worker_types": manifests},
            }.get(key)
            # Whether a Chief composer reads its launch settings depends on how far the
            # screen loads, so this read is always served.
            if key == "/api/chief/conversation/start-values":
                inner = {"backend_key": "codex", "model": None, "reasoning_effort": None}
            if key == "/api/projects" and card_fixture.get("goals"):
                inner = {"projects": []}
            if key.startswith("/api/goals/") and card_fixture.get("goals"):
                inner = {"goal_id": card_fixture["goals"][0]["id"], "values": [], "charts": {}, "sources": [],
                         "status_date": time.strftime("%Y-%m-%d"), "next_refresh_at": int(time.time()) + 86400} if key.endswith("/readings") else card_fixture["goals"][0]
            if inner is None:
                unexpected.append(key)
                route.fulfill(status=503, json={"error": {"code": "fixture", "message": key}})
                return
            route.fulfill(json={"key": key, "epoch": "fixture", "revision": "1", "body": inner})
            return
        if path == "/api/updates":
            route.fulfill(json={"state": "updating" if fold["release_sha"] == "fixture-boot" else "up_to_date", "allowed_actions": [],
                                "activity": "unknown", "available": None, "attempt": None, "app_sha": fold["release_sha"], "detail": None,
                                "deadline_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)), "mac_details": None})
            return
        if path == "/api/goals":
            result = {"goals": [], "status_date": time.strftime("%Y-%m-%d"), "next_refresh_at": int(time.time()) + 86400}
        elif path == "/api/workers":
            result = {"workers": workers, "chief_of_staff": {"employee_id": "chief", "label": "Chief", "skill": skill, "launch_defaults": launch}}
        elif path == "/api/skills":
            result = {"skills": [skill]}
        elif path == "/api/worker-types":
            result = {"worker_types": manifests}
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
    invalid_height_keyframes = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # Responsive navigation also animates sidebar groups while their rail is hidden.
    # Chromium warns instead of throwing when that animation receives a nonfinite height.
    page.on("console", lambda message: invalid_height_keyframes.append(message.text)
            if "Invalid keyframe value" in message.text and "NaNpx" in message.text else None)
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
    pane.get_by_role("link", name="Notifications").click()
    page.wait_for_function("location.hash === '#/workspace/utility/settings/notifications?view=managers'")
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
    # Leaving the route is proved at the end of this host, under the browser clock.

    # Preview's own full-height document styles must remain inside the same pane.
    page.route("**/files/tickets/t_fixture/artifacts/wide.html", lambda route: route.fulfill(
        content_type="text/html", body="<html><body style='width:3000px;height:3000px'>Wide artifact</body></html>"))
    page.evaluate("location.hash='#/preview?source=ticket&ticket=t_fixture&path=artifacts%2Fwide.html'")
    preview = pane.locator('[data-file-preview-route]')
    expect(preview.locator('iframe')).to_be_visible()
    expect(preview.locator('[data-conversation-pane]')).to_have_attribute("data-conversation-state", "rest")
    expect(preview.get_by_placeholder("Message Wide artifact owner...", exact=True)).to_be_visible()
    expect(preview.locator('[data-artifact-owner-unavailable]')).to_have_count(0)
    # Nothing sits above the artifact's conversation: it starts at the top of its column.
    conversation_offset = preview.evaluate("node => { const column = node.querySelector('.conversation-column');"
                                           " return column.querySelector('[data-conversation-pane]').getBoundingClientRect().top - column.getBoundingClientRect().top; }")
    assert abs(conversation_offset) <= 1, conversation_offset
    assert rail.evaluate("node => node === window.__persistentRail")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    bounds = pane.bounding_box()
    assert bounds and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 701, bounds

    # At minimum width all five destinations (VPS lives in Settings), the Focus switch and
    # every label remain inside the rail.
    grip = page.get_by_role("separator", name="Resize workspace panel")
    grip.focus()
    for _ in range(20):
        page.keyboard.press("Shift+ArrowLeft")
    expect(grip).to_have_attribute("aria-valuenow", "232")
    expect(rail.locator('.utility')).to_have_count(5)
    focus_switch = page.locator(".rail-foot").get_by_role("switch", name="Focus", exact=True)
    expect(focus_switch).to_have_count(1)
    rail_bounds = page.locator('.workspace-rail').bounding_box()
    assert rail_bounds and abs(rail_bounds['width'] - 232) < 2, rail_bounds
    for name in ["Review", "Usage", "Ideas", "Goals", "Settings", "Focus"]:
        control = focus_switch if name == "Focus" else rail.get_by_label(name, exact=True)
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
    for name in ["Review", "Workspace", "More"]:
        link = page.locator('.workspace-mobile-nav').get_by_role("button" if name == "More" else "link", name=name, exact=name != "More")
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
    verify_selection_washes(page, url, card_fixture, evidence)
    capture_card_motion(browser, url, card_fixture, respond, evidence)
    verify_card_geometry(page, url, card_fixture, evidence)
    verify_rail_pagination(page, url, card_fixture, evidence)
    fold["release_sha"] = "fixture-boot"
    verify_recap_pointer_floor(browser, page, url, card_fixture, respond, evidence)
    verify_vps_route_leave(page, url, pane, rail, metrics_calls)
    verify_owner_updates(browser, url)
    verify_conversation_overlay(browser, url)

    assert not errors, errors
    assert not invalid_height_keyframes, invalid_height_keyframes
    unwanted = [path for path in unexpected if not path.startswith(("/api/navigation", "/api/changes", "/api/managers", "/api/projects", "/api/notifications/settings", "/api/conversation", "/api/workers/chief-of-staff"))]
    assert not unwanted, unwanted
    context.close()
    metrics.close()


def verify_vps_route_leave(page, url, pane, rail, metrics_calls):
    """The real App route owns the live page. Leaving it stops reads and lease renewals.

    The poll is a 2000 ms timer. The browser clock runs 2200 ms of timers without
    waiting for them. It is installed last on this host because it also replaces
    animation frames, which every earlier motion proof here samples in real time.
    """
    page.clock.install()
    page.goto(url + "#/vps", wait_until="domcontentloaded")
    # A reload starts the App, and so every timer it creates, under the installed clock.
    page.reload(wait_until="domcontentloaded")
    expect(pane.locator('[data-vps-metric="cpu"]')).to_contain_text("27")
    rail.evaluate("node => window.__persistentRail = node")
    page.evaluate("location.hash='#/settings'")
    expect(pane.locator('[data-settings]')).to_be_visible()
    before = len(metrics_calls)
    page.clock.run_for(2200)
    page.evaluate("dispatchEvent(new Event('focus')); dispatchEvent(new Event('online'))")
    page.clock.run_for(30)
    # A read started above reaches the route before this later one completes.
    page.evaluate("() => fetch('/api/meta').then(response => response.json())")
    assert len(metrics_calls) == before, metrics_calls[before:]
    page.evaluate("location.hash='#/vps'")
    expect(pane.locator('[data-vps-metric="cpu"]')).to_contain_text("27")
    assert '/api/vps-metrics/latest' in metrics_calls[before:]
    assert '/api/vps-metrics/activity' in metrics_calls[before:]
    assert rail.evaluate("node => node === window.__persistentRail")


def verify_settled_fold(page, url, fixture, evidence):
    """Real Svelte/layout/WAAPI proof that the controller's synthetic DOM cannot give.

    One sequential flow exercises delayed navigation publication and fresh scoped
    snapshots during folds. Geometry comes from animation frames, never a fake DOM.
    """
    started = time.perf_counter()
    page.goto(url + "#/workspace/utility/settings", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    head = page.locator(".settled-head")
    # The count is a plain numeral after the word (redesign), no brackets.
    expect(head.locator("[data-settled-count]")).to_have_text("3")
    expect(head).not_to_contain_text("(")
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
            // The final keyframe of the section's running height animation is where
            // the fold says it will come to rest.
            const target = section.getAnimations().map(a => a.effect?.getKeyframes().at(-1)?.height)
              .filter(height => typeof height === 'string').map(parseFloat).at(-1) ?? null;
            samples.push({ms:performance.now()-start, height:section.getBoundingClientRect().height, target,
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

    def settles(samples, opening=True):
        # How many frames land mid-fold depends on runner load, so this checks where
        # the fold settles, not how it moves. A stale animation target used to
        # disappear and jump to intrinsic height. Once no animation runs, the height
        # is the last target and stays there.
        heights = [sample["height"] for sample in samples]
        assert max(heights) - min(heights) > 20, samples
        target = None
        for sample in samples:
            if sample["sectionEffects"]:
                target = sample["target"] if sample["target"] is not None else target
            elif target is not None:
                assert abs(sample["height"] - target) <= 1, (target, sample, samples)
        assert target is not None and not samples[-1]["sectionEffects"], samples[-4:]
        assert abs(heights[-1] - heights[-3]) < 1, samples[-4:]
        assert all(sample["rowEffects"] == 0 for sample in samples), samples
        assert samples[-1]["expanded"] == ("true" if opening else "false"), samples[-1]

    # A settled window arrives within Glide, then grows while the fold still moves.
    start("late-within-glide-and-refresh", delay=100)
    page.wait_for_function("document.querySelectorAll('[data-settled-item]').length === 3")
    fixture["count"] = 7
    page.evaluate("window.__navigationDelay=0; void window.__refreshNavigation()")
    samples = finish()
    settles(samples)
    assert samples[-1]["rows"] == 7, samples[-1]
    page.screenshot(path=str(evidence / "settled-fold-open.png"))

    # The body survives until the close completes, even when the source changes.
    start("close-with-refresh", duration=650)
    # The source changes while the close is moving.
    page.wait_for_function("document.querySelector('.settled').getAnimations().length > 0")
    fixture["count"] = 5
    page.evaluate("void window.__refreshNavigation()")
    samples = finish()
    settles(samples, False)
    assert any(sample["bodyPresent"] and sample["height"] > samples[-1]["height"] + 2 for sample in samples)
    assert not samples[-1]["bodyPresent"], samples[-1]

    # Content arrives after the original Glide deadline. It still enters as a fold.
    start("late-after-glide", duration=1050, delay=450)
    samples = finish()
    settles(samples)
    assert samples[-1]["rows"] == 5, samples[-1]
    assert any(sample["ms"] > 450 and sample["sectionEffects"] for sample in samples), samples

    # Rapid close/open/close preserves the sampled starting height and latest intent.
    start("reversal", duration=850, toggles=[90, 180])
    samples = finish()
    settles(samples, False)
    assert not samples[-1]["bodyPresent"], samples[-1]

    # The production flex cap and internal scrollbar constrain a large settled page.
    fixture["count"] = 30
    start("capped-open", duration=850)
    samples = finish()
    settles(samples)
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
            # The clip opens on a still rail: the pointer's hover has left it.
            wait_settled(page, ".manager-card")

            def published(page=page):
                # The fixture refresh resolves after snapshot delivery. One frame lets
                # Svelte commit, so the motion controller has marked any travel.
                page.evaluate("async () => { await window.__refreshNavigation(); await new Promise(requestAnimationFrame); }")

            def played(page=page):
                # Each change plays to its end at ordinary speed before the next.
                published(page)
                wait_settled(page, ".manager-card")

            for approval in [True, False]:
                two["awaiting_approval"] = approval
                played()
            arriving = {**one, "id": "clip_arriving", "title": "A new Worker appears"}
            rows.append(arriving)
            played()
            arriving.update(stage="done", ticket_status="done")
            played()
            # Rapid changes each arrive while the previous one still moves.
            for approval in [True, False, True]:
                two["awaiting_approval"] = approval
                published()
            page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
            # The recording holds the final state over consecutive painted frames.
            wait_settled(page, ".manager-card")
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
    # The release happens on a trusted native scroll at capture phase, before the
    # production action receives that event.
    bounds = scroll.bounding_box()
    page.mouse.move(bounds['x']+1, bounds['y']+30)
    scroll.evaluate("""n => {
      window.__releaseOnNativeScroll=event=>{
        if(!event.isTrusted) return;
        n.removeEventListener('scroll',window.__releaseOnNativeScroll,true);
        const row=document.querySelector('[data-ticket-id="page_worker_17"]');
        window.__continuedScroll={top:row.getBoundingClientRect().top,scrollTop:n.scrollTop,
          trusted:event.isTrusted,ms:performance.now()};
        // Withhold this dispatch from the action: publication must sample
        // native movement itself, rather than relying on an onScroll call.
        event.stopImmediatePropagation();
        window.__navigationOrder.push({kind:'native-scroll-before-action',ms:performance.now(),top:n.scrollTop});
        window.__holdNavigation=false;window.__releaseNavigation();
      };
      n.addEventListener('scroll',window.__releaseOnNativeScroll,true);
    }""")
    page.mouse.wheel(0, 36)
    page.wait_for_function('window.__continuedScroll')
    continued = page.evaluate('window.__continuedScroll')
    assert continued['trusted'] and abs(continued['top']-request_top)>10, (request_top,continued)
    (evidence / 'delayed-publication-before-wait.json').write_text(json.dumps({
        'prior':prior,'continued':continued,'frames':page.evaluate('window.__navigationFrames'),
        'order':page.evaluate('window.__navigationOrder'),'held':page.evaluate('window.__heldNavigation.length'),
        'heldFrames':page.evaluate('window.__heldNavigationFrames'),
        'members':members('rail_tickets')},indent=2))
    page.mouse.move(900, 600)
    after_view = page_changed('rail_tickets', prior)
    assert abs(top(anchor)-continued['top']) < 1.5, (continued,top(anchor),after_view)
    policy = scroll.evaluate("n=>({value:n.style.getPropertyValue('overflow-anchor'),priority:n.style.getPropertyPriority('overflow-anchor')})")
    assert policy == {'value':'auto','priority':'important'}, policy
    order = page.evaluate('window.__navigationOrder')
    assert [entry['kind'] for entry in order][:2] == ['native-scroll-before-action','publication'], order
    proof.append({'name':'delayed-publication-native-scroll-before-action',
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
    detail["workflow"] = manifest["worker_types"][0]
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
            "/api/goals": {"goals": [], "status_date": time.strftime("%Y-%m-%d"), "next_refresh_at": int(time.time()) + 86400},
            "/api/workers/chief-of-staff/settings": {"employee_id": "chief", "label": "Chief", "recap": "The document keeps its full height beneath the conversation.", "skill": None, "launch_defaults": {"employee_backend": "codex", "employee_launch_model": None, "employee_launch_reasoning_effort": None}},
            "/api/chief/conversation/start-values": {"backend_key": "codex", "model": None, "reasoning_effort": None},
            "/api/tickets/t_overlay0/workspace": detail,
            "/api/tickets/t_overlay0/conversation/start-values": {"backend_key":"codex","model":None,"reasoning_effort":None},
            "/api/worker-types?supported=true": manifest,
            "/api/worker-types": manifest,
            "/api/updates": {"state": "up_to_date", "allowed_actions": [], "activity": "unknown", "available": None, "attempt": None, "app_sha": "fixture-boot", "detail": None, "deadline_at": None, "mac_details": None},
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
        def settle(page=page):
            # Reduced motion: each layout below lands through observers and frames.
            wait_settled(page, '[data-conversation-pane]', '.conversation-layer', '[data-conversation-thread]',
                         '[data-conversation-input]', '.ticket-doc, .manager-doc', '.ticket-page, .manager-page',
                         '.ticket-artifact-body')
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
            settle()
        expect(document.get_by_text(final_text, exact=True)).to_have_count(1)
        # Initial selection stays product-owned. Choose dock state for this proof.
        if pane.get_attribute('data-conversation-state') == 'opened':
            page.locator('[data-conversation-collapse]').click()
        # Open by the same controls as an owner. A viewport read follows each layout.
        box.click()
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        box.fill("Draft keeps its place.\n" * 6)
        settle()
        doc_before = document.bounding_box()
        dock_before = page.locator('.conversation-layer').bounding_box()
        # The page scrolls under the composer zone (redesign): the document runs on beneath the
        # dock, and its last line still scrolls clear of the dock (checked at the end below).
        assert doc_before['y'] + doc_before['height'] >= dock_before['y'] + dock_before['height'] - 1, (name, doc_before, dock_before)
        document.evaluate('e=>e.scrollTop=240')
        scroll_before = document.evaluate('e=>e.scrollTop')
        page.locator('[data-conversation-expand]').click()
        expect(pane).to_have_attribute('data-conversation-state', 'opened')
        settle()
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
        settle()
        assert box.input_value() == 'A shorter draft'
        assert_anchor('composer shrink')
        box.fill('Draft grows again.\n' * 6)
        settle()
        assert_anchor('composer growth')
        # Collapse cannot remove document scroll range or clamp the reader.
        document.evaluate('e=>e.scrollTop=e.scrollHeight')
        end_scroll = document.evaluate('e=>e.scrollTop')
        collapse_before = {'thread':transcript_bounds(),'anchor':anchor}
        page.locator('[data-conversation-collapse]').click()
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        settle()
        assert not thread.is_visible()
        assert document.evaluate('e=>e.scrollHeight') >= highwater
        assert document.evaluate('e=>e.scrollTop') == end_scroll
        # Dock state keeps the mounted reader and its full-conversation anchor.
        page.locator('[data-conversation-expand]').click()
        expect(pane).to_have_attribute('data-conversation-state', 'opened')
        settle()
        assert_anchor('return from dock')
        collapse_after = {'thread':transcript_bounds(),'anchor':anchor}
        page.locator('[data-conversation-collapse]').click()
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        settle()
        box.click()
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        final = document.get_by_text(final_text, exact=True)
        document.evaluate('e=>e.scrollTop=e.scrollHeight')
        settle()
        card_peek = pane.bounding_box()
        final_box = final.bounding_box()
        # Wheel over uncovered document space above the full-column card.
        uncovered_point = {'x':host['x'] + host['width']/2, 'y':(host['y'] + card_peek['y'])/2}
        assert page.evaluate('point=>Boolean(document.elementFromPoint(point.x,point.y)?.closest(".ticket-doc,.manager-doc"))', uncovered_point), (name, uncovered_point)
        page.mouse.move(uncovered_point['x'], uncovered_point['y'])
        page.evaluate("() => { window.__overlayWheel = false; addEventListener('wheel', () => { window.__overlayWheel = true; }, {capture: true, once: true, passive: true}); }")
        page.mouse.wheel(0, final_box['y'] + final_box['height'] - card_peek['y'] + 24)
        page.wait_for_function('window.__overlayWheel')
        settle()
        final_box = final.bounding_box()
        assert final_box['y'] >= host['y'], (name, final_box, host, page.evaluate('''([x,y])=>document.elementFromPoint(x,y)?.outerHTML.slice(0,500)''',[uncovered_point['x'],uncovered_point['y']]),document.evaluate('e=>e.scrollTop'))
        assert final_box['y'] + final_box['height'] <= card_peek['y'] + 1, (name, final_box, card_peek)
        page.screenshot(path=str(evidence / f'{name}-document-tail.png'), animations='disabled')
        # Simulated keyboard reduction plus displacement stays inside the host intersection.
        page.evaluate('window.__keyboardViewport(100, 420)')
        settle()
        card_keyboard = pane.bounding_box()
        assert card_keyboard['y'] >= max(host['y'], 100) - 1, (name, card_keyboard, host)
        assert card_keyboard['y'] + card_keyboard['height'] <= min(host['y'] + host['height'], 520) + 1, (name, card_keyboard, host)
        # Full conversation follows the displaced viewport without losing scrollback.
        page.locator('[data-conversation-expand]').click()
        expect(pane).to_have_attribute('data-conversation-state', 'opened')
        settle()
        assert_anchor('keyboard reduction and displacement')
        thread.evaluate('e=>e.scrollTop=e.scrollHeight')
        settle()
        expect(tail).to_be_in_viewport()
        keyboard_transcript = transcript_bounds()
        assert keyboard_transcript['tail']['bottom'] <= keyboard_transcript['bottom'] + 1, (name, keyboard_transcript)
        page.screenshot(path=str(evidence / f'{name}-keyboard.png'), animations='disabled')
        page.evaluate('window.__keyboardViewport(0, innerHeight)')
        document.evaluate('e=>e.scrollTop=0')
        settle()
        thread.evaluate('e=>e.scrollTop=e.scrollHeight')
        settle()
        page.screenshot(path=str(evidence / f'{name}.png'), animations='disabled')
        page.locator('[data-conversation-collapse]').click()
        expect(pane).to_have_attribute('data-conversation-state', 'rest')
        settle()
        artifact_proof = None
        if ticket_host:
            page.evaluate('window.__keyboardViewport(0, innerHeight)')
            settle()
            doc_scroll_before_artifact = document.evaluate('e=>e.scrollTop')
            page.evaluate("location.hash='#/workspace/t_overlay0?source=ticket&ticket=t_overlay0&path=overlay.html'")
            artifact = page.locator('[data-ticket-artifact]')
            frame = artifact.locator('iframe')
            expect(frame).to_be_visible()
            expect(artifact.locator('[data-ticket-artifact-close]')).to_be_in_viewport()
            frame_before = frame.bounding_box()
            page.locator('[data-conversation-expand]').click()
            expect(pane).to_have_attribute('data-conversation-state', 'opened')
            settle()
            assert frame.bounding_box() == frame_before
            page.locator('[data-conversation-collapse]').click()
            expect(pane).to_have_attribute('data-conversation-state', 'rest')
            settle()
            artifact_scroll = artifact.locator('.ticket-artifact-body')
            card = pane.bounding_box()
            frame_tail = frame.bounding_box()
            assert frame_tail['y'] + frame_tail['height'] <= card['y'] + 1, (frame_tail, card)
            assert frame_tail['height'] == frame_before['height']
            artifact.frame_locator('iframe').locator('#end').evaluate('e=>e.ownerDocument.defaultView.scrollTo(0,e.ownerDocument.documentElement.scrollHeight)')
            artifact.locator('iframe').element_handle().content_frame().wait_for_function(
                '() => scrollY + innerHeight >= document.documentElement.scrollHeight - 1')
            artifact_end = artifact.frame_locator('iframe').locator('#end').bounding_box()
            assert artifact_end['y'] + artifact_end['height'] <= card['y'] + 1, (artifact_end,card)
            assert artifact_end['y'] >= artifact_scroll.bounding_box()['y'], (artifact_end,artifact_scroll.bounding_box())
            page.screenshot(path=str(evidence / 'narrow-ticket-artifact-tail.png'), animations='disabled')
            artifact_scroll.evaluate('e=>e.scrollTop=0')
            settle()
            expect(artifact.locator('[data-ticket-artifact-refresh]')).to_be_in_viewport()
            page.screenshot(path=str(evidence / 'narrow-ticket-artifact.png'), animations='disabled')
            artifact_proof = {'frameOriginal':frame_before,'frameTail':frame_tail,'finalParagraph':artifact_end,'scrollHeight':artifact_scroll.evaluate('e=>e.scrollHeight'),'clientHeight':artifact_scroll.evaluate('e=>e.clientHeight')}
            artifact.locator('[data-ticket-artifact-close]').click()
            expect(artifact).to_have_count(0)
            assert document.evaluate('e=>e.scrollTop') == doc_scroll_before_artifact
        proof['hosts'].append({'name':name,'host':host,'titleChannel':title,'document':doc_open,'openedCard':card_open,'scrollTop':scroll_before,'retainedScrollHeight':highwater,'documentTail':final_box,'transcriptOpened':transcript_open,'scrollbackAnchor':anchor,'anchorChecks':anchor_proof,'collapse':{'before':collapse_before,'after':collapse_after},'keyboardCard':card_keyboard,'keyboardTranscript':keyboard_transcript,'artifact':artifact_proof})
        draft_before_dismissal = box.input_value()
        doc_before_dismissal = document.evaluate('e=>e.scrollTop')
        # A plain part of the document above the card: a link there would open another page.
        point = page.evaluate('''([host, top]) => {
          for (let y = host.y + 8; y < top - 8; y += 8) for (const x of [host.x + host.width / 2, host.x + 24]) {
            const e = document.elementFromPoint(x, y);
            if (e?.closest('.ticket-doc,.manager-doc') && !e.closest('a,button,input,textarea,[role=button]')) return {x, y};
          }
          return null;
        }''', [host, pane.bounding_box()['y']])
        hit = point is not None
        assert hit, name
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

    def status_geometry(word="Updating…"):
        status = page.locator(".status-card:visible")
        expect(status).to_contain_text(word)
        bounds = status.bounding_box()
        rail_bounds = rail.bounding_box()
        viewport = page.viewport_size
        assert bounds and rail_bounds and viewport
        assert bounds["x"] >= rail_bounds["x"] and bounds["x"] + bounds["width"] <= rail_bounds["x"] + rail_bounds["width"] + 1, bounds
        assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= viewport["height"], bounds
        # Desktop: between the active work and Settled. Phone: above the bottom bar, below both.
        box = active.bounding_box()
        assert box and box["y"] + box["height"] <= bounds["y"] + 1, (box, bounds)
        settled_head = head.bounding_box()
        assert settled_head
        if viewport["width"] > 720:
            assert bounds["y"] + bounds["height"] <= settled_head["y"] + 1, (settled_head, bounds)
        else:
            assert settled_head["y"] + settled_head["height"] <= bounds["y"] + 1, (settled_head, bounds)
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
            expect(head.locator("[data-settled-count]")).to_have_text(str(count))
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
                    # The active list keeps its gutter on both edges so its insets match; the
                    # Settled list in the dock gives its gutter back (redesign). Neither scrolls
                    # sideways, whatever its scrollbar takes.
                    assert values["overflowY"] == "auto", proof[-1]
                    assert values["gutter"] == ("stable both-edges" if scroller == active else "auto"), proof[-1]
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
    page.evaluate("window.__ownerUpdatesSignal(); dispatchEvent(new Event('pageshow'))")
    status_samples = []
    for state, word in [("ready", "Update complete"), ("composing", "Update complete")]:
        if state == "composing":
            page.evaluate("document.dispatchEvent(new CustomEvent('panels:composition-state',{detail:{active:true}}))")
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
            assert status.locator(".status-action").count() == 1
            values = []
            for scroller, selector in [(active, ".manager-card:not(.chief) .settle"), (settled, ".reopen")]:
                dimensions = geometry(scroller)
                assert dimensions["scrollWidth"] == dimensions["clientWidth"] and dimensions["scrollLeft"] == 0, dimensions
                assert dimensions["overflowY"] == "auto", dimensions
                assert dimensions["gutter"] == ("stable both-edges" if scroller == active else "auto"), dimensions
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
    # Composition no longer holds a requested refresh. Keep both geometry modes
    # actionable, then cross the real reload boundary before later fixture work.
    page.evaluate("window.__beforeStatusRefreshDocument = document")
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.locator("[data-shell-refresh]:visible").click()
    assert page.evaluate("document !== window.__beforeStatusRefreshDocument")
    (evidence / "rail-overflow-status-geometry.json").write_text(json.dumps(status_samples, indent=2))
    (evidence / "rail-overflow-geometry.json").write_text(json.dumps({
        "seconds": round(time.perf_counter() - started, 3), "newHosts": 0, "samples": proof,
    }, indent=2))


def verify_owner_updates(browser, url):
    """One update card from press to done, across native dialogs and a lost connection.

    The existing App host proves the retained card DOM at desktop and phone sizes,
    native dialogs, the rail placement, page dimming and closed-stream recovery.
    The update server is a double of the contract shape: its rules have their own tests.
    A synthetic SHA change proves browser refresh safety, not service deployment.
    """
    started = time.perf_counter()
    backend = OwnerUpdatesFixture()
    navigation = runpy.run_path(str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py"))
    source = navigation["navigation_response"]({}, {"chief_of_staff": {"label": "Chief", "conversation_id": "bounded-reader"}})
    posts = []
    documents = []
    navigation_health = {"mode": "current", "generation": 1, "reason": None}
    errors = []
    context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    context.add_init_script("localStorage.setItem('panels.workspace.rail-width', '300')")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    evidence = Path(os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR", "/tmp/panels-owner-updates")) / "owner-updates"
    evidence.mkdir(parents=True, exist_ok=True)

    def respond(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        if path.startswith("/api/updates") or path == "/api/meta":
            body = route.request.post_data_json if route.request.method != "GET" else None
            if route.request.method != "GET":
                posts.append({"path": path, "body": body, "app_sha": route.request.headers.get("x-panels-app-sha")})
            status, answer = backend.respond(route.request.method, path, body)
            route.fulfill(status=status, json=answer)
            return
        if path == "/api/navigation/view/snapshot":
            view = json.loads(parse_qs(parts.query)["view"][0])
            authority = {**source["authority"], "availability": {
                "state": "current" if navigation_health["mode"] == "current" else "unavailable",
                "generation": str(navigation_health["generation"]), "reason": navigation_health["reason"],
            }, "permit": {**source["authority"]["permit"], "generation": str(navigation_health["generation"])} if navigation_health["mode"] == "current" else None}
            projected = {**source, "mode": navigation_health["mode"], "authority": authority}
            route.fulfill(json=navigation["scoped_navigation_response"](projected, view))
            return
        key = parse_qs(parts.query).get("key", [path])[0] if path == "/api/resource-body" else path
        values = {
            "/api/goals": {"goals": [], "status_date": time.strftime("%Y-%m-%d"), "next_refresh_at": int(time.time()) + 86400},
            "/api/workers/chief-of-staff/settings": {"employee_id": "chief", "label": "Chief", "recap": "Owner updates", "skill": None, "launch_defaults": {"employee_backend": "codex", "employee_launch_model": None, "employee_launch_reasoning_effort": None}},
            "/api/chief/conversation/start-values": {"backend_key": "codex", "model": None, "reasoning_effort": None},
            "/api/worker-types?supported=true": {"worker_types": []},
            "/api/feedback/count": {"open_count": 0},
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
    action = card.locator(".status-action")
    info = card.get_by_role("button", name="Update details", exact=True)
    dialog = page.locator("[data-update-dialog]:is([open])")
    box = page.locator("[data-conversation-input]")
    dimmed = page.locator(".board-workspace-right")

    def signal():
        with page.expect_response(lambda response: urlsplit(response.url).path == "/api/updates" and response.status == 200):
            page.evaluate("window.__ownerUpdatesSignal()")

    def capture(name):
        for size, viewport in (("desktop", {"width": 1280, "height": 900}),
                               ("phone", {"width": 390, "height": 844})):
            page.set_viewport_size(viewport)
            expect(card).to_be_visible()
            page.screenshot(animations="disabled", path=str(evidence / f"{name}-{size}.png"))
        page.set_viewport_size({"width": 1280, "height": 900})

    def retained():
        assert page.evaluate("window.__updateCards.every(node => node.isConnected && [...document.querySelectorAll('.status-card')].includes(node))")

    def no_restarting_text():
        assert "Restarting" not in page.evaluate("document.body.innerText")

    try:
        page.goto(url + "?overlay=1&owner-updates=1#/workspace/chief-of-staff", wait_until="domcontentloaded")
        expect(box).to_be_visible()
        expect(card).to_contain_text("Update available")
        page.evaluate("window.__updateCards = [...document.querySelectorAll('.status-card')]")
        assert page.evaluate("window.__updateCards.length") == 2
        # Desktop: the card sits between the active work and Settled, not in the rail foot.
        placement = card.evaluate("""node => {
          const box = node.getBoundingClientRect();
          const active = document.querySelector('[data-active-work-scroll]').getBoundingClientRect();
          const settled = document.querySelector('.settled-head').getBoundingClientRect();
          return {inFoot: Boolean(node.closest('.rail-foot')), belowActive: active.bottom <= box.top + 1, aboveSettled: box.bottom <= settled.top + 1};
        }""")
        assert placement == {"inFoot": False, "belowActive": True, "aboveSettled": True}, placement
        capture("available")

        # The Mac's diagnostics are not a separate block in the shell.
        backend.mac_details = {"identity": None, "connection_state": "disconnected", "last_contact_at_ms": 1000, "refusal": None, "update": None}
        signal()
        expect(page.locator("[data-mac-status]")).to_have_count(0)

        backend.activity = "busy"
        signal()
        action.click()
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("A new version is ready for Panels and the Mac. Panels restarts, then you refresh to finish.")
        expect(dialog).to_contain_text("Work is running. Updating now interrupts it.")
        assert len(posts) == 0
        page.keyboard.press("Escape")
        expect(action).to_be_focused()
        action.click()
        page.screenshot(path=str(evidence / "offer-dialog.png"))
        dialog.get_by_role("button", name="When work finishes", exact=True).click()
        expect(card).to_contain_text("Update waits for work to finish")
        assert posts[-1] == {"path": "/api/updates/install", "body": {"app_sha": "b" * 40, "mode": "idle", "confirm_activity": False}, "app_sha": "a" * 40}
        retained()
        capture("waiting")
        action.click()
        expect(dialog).to_contain_text("Panels updates as soon as running work finishes.")
        dialog.get_by_role("button", name="Cancel update", exact=True).click()
        expect(card).to_contain_text("Update available")
        assert posts[-1]["path"] == "/api/updates/attempts/attempt-1/cancel"
        retained()

        # Queue, then promote the same attempt. The dialog closes at once.
        action.click()
        dialog.get_by_role("button", name="When work finishes", exact=True).click()
        expect(card).to_contain_text("Update waits for work to finish")
        action.click()
        dialog.get_by_role("button", name="Update now", exact=True).click()
        expect(dialog).to_have_count(0)
        expect(card).to_contain_text("Updating…")
        expect(card.locator(".status-track")).to_be_visible()
        assert posts[-1] == {"path": "/api/updates/attempts/attempt-2/now", "body": {"confirm_activity": True}, "app_sha": "a" * 40}
        retained()
        capture("updating")

        # A failure has Try again on the card and plain facts behind the info button.
        backend.fail_install("The new version did not pass its health check, so Panels went back to the previous version and its data.")
        signal()
        expect(card).to_contain_text("Update didn't install")
        expect(action).to_have_text("Try again")
        info.click()
        expect(dialog).to_contain_text("The new version did not pass its health check")
        expect(dialog).to_contain_text("aaaaaaaa (previous)")
        expect(dialog).to_contain_text("Not changed")
        dialog.get_by_role("button", name="Close", exact=True).click()
        retained()
        capture("failed")
        action.click()
        expect(card).to_contain_text("Updating…")
        assert posts[-1]["path"] == "/api/updates/attempts/attempt-2/retry"
        retained()

        # The restart drops the connection. Before the deadline that is still Updating….
        page.evaluate("window.__ownerUpdatesDisconnect()")
        expect(dimmed).to_have_css("opacity", "0.5")
        expect(card).to_contain_text("Updating…")
        no_restarting_text()
        page.wait_for_timeout(3100)
        expect(card).to_contain_text("Updating…")
        retained()
        capture("updating-disconnected")
        backend.finish_install()
        page.evaluate("window.__ownerUpdatesOffline = false")
        expect(card).to_have_attribute("data-connection-state", "connected", timeout=7000)
        expect(dimmed).to_have_css("opacity", "1")
        # The new server answers while the Mac still updates. The page is behind, and it is still one update.
        expect(card).to_contain_text("Updating…")
        no_restarting_text()

        # The Mac cannot be reached. This page is behind and cannot write, so it refreshes first.
        backend.mac_details = {"identity": None, "connection_state": "disconnected", "last_contact_at_ms": int(time.time() * 1000) - 14 * 60_000,
                               "refusal": None, "update": None}
        backend.mac_outcome("mac_unreachable")
        signal()
        expect(card).to_contain_text("Update complete")
        retained()
        capture("complete")
        draft = "Keep this unsent draft across the revision change."
        box.fill(draft)
        page.evaluate("window.__beforeUpdateDocument = document; window.__disconnectNavigation()")
        health = card.locator("[data-navigation-health]")
        expect(health).to_have_attribute("data-navigation-health", "reconnecting")
        assert not health.evaluate("node => Boolean(node.closest('button'))")
        health.click()
        assert page.evaluate("document === window.__beforeUpdateDocument")
        page.locator("[data-shell-refresh]:visible").click()
        page.wait_for_function("document !== window.__beforeUpdateDocument")
        expect(box).to_be_visible()
        assert documents == ["a" * 40, "b" * 40]

        # After Refresh the Mac failure the server kept shows. Its details sit behind the info button only.
        expect(card).to_contain_text("Can't reach the Mac")
        page.evaluate("window.__updateCards = [...document.querySelectorAll('.status-card')]")
        expect(card).not_to_contain_text("Last contact")
        info.click()
        expect(dialog).to_contain_text("14 minutes ago")
        expect(dialog).to_contain_text("bbbbbbbb (updated)")
        dialog.get_by_role("button", name="Try again", exact=True).click()
        expect(card).to_contain_text("Updating…")
        assert posts[-1]["path"] == "/api/updates/attempts/attempt-3/retry-mac"
        retained()

        backend.mac_outcome("mac_failed", "The new Mac runtime did not start in 60 seconds, so the Mac went back to its previous version.")
        signal()
        expect(card).to_contain_text("Mac didn't update")
        retained()
        capture("mac-failed")
        action.click()
        expect(card).to_contain_text("Updating…")
        assert posts[-1]["path"] == "/api/updates/attempts/attempt-3/retry-mac"
        retained()

        # The Mac is current and this page is current: the card leaves.
        backend.mac_outcome("up_to_date")
        signal()
        expect(card).to_have_count(0)

        # No update runs, so a lost connection immediately offers Try now.
        page.evaluate("window.__ownerUpdatesDisconnect()")
        expect(card).to_contain_text("Can't reach Panels")
        expect(dimmed).to_have_css("opacity", "0.5")
        capture("unreachable")
        page.evaluate("window.__ownerUpdatesOffline = false")
        action.click()
        expect(card).to_have_count(0)
        expect(dimmed).to_have_css("opacity", "1")
        assert errors == [], errors
        (evidence / "proof.json").write_text(json.dumps({
            "seconds": round(time.perf_counter() - started, 3), "newHosts": 1,
            "requests": posts, "documentVersions": documents,
            "boundary": "Full App and native dialogs -> contract double of /api/updates and its action routes",
            "cardIdentity": "Both desktop and phone card nodes retained from Update available through waiting, updating, failure, retry, lost connection and complete, and after Refresh through the Mac failures",
        }, indent=2))
    finally:
        context.close()


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

    def panel_settled(target_page=page):
        # The panel's fade and the slot's height transition have ended, and the slot's
        # settle timer has released its overflow.
        wait_settled(target_page, '[data-rail-hover-panel]', '[data-recap-slot]')

    def open_panel():
        row.hover()
        expect(panel).to_have_attribute("aria-hidden", "false")
        panel_settled()
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
        panel_settled()
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
        panel_settled()
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
        panel_settled()
        page.keyboard.press('Tab')
        page.locator('[data-ticket-id="recap_neighbor"]').focus()
        expect(panel.locator('.title')).to_contain_text('Other recap')
        panel_settled()
        assert panel.bounding_box()["height"] < floor - 10
        proof["sessions"].append({"name": tag, "threeLines": floor, "largest": grown,
                                  "twoLinesAfterExit": natural, "emptyAfterExit": empty})
        leave()

    # Sidebar Tab skips the shared preview's recap action in both directions.
    # Existing pointer requests above prove that the excluded action still works.
    sidebar_rows = page.locator('[data-ticket-id="recap_floor"], [data-ticket-id="recap_neighbor"]')
    expect(sidebar_rows).to_have_count(2)
    first_row, second_row = sidebar_rows.nth(0), sidebar_rows.nth(1)
    page.keyboard.press('Shift')
    first_row.focus()
    expect(panel).to_have_attribute('aria-hidden', 'false')
    expect(panel.locator('[data-request-recap]')).to_have_attribute(
        'data-request-recap', 'ticket:' + first_row.get_attribute('data-ticket-id'))
    first_row.press('Tab')
    expect(second_row).to_be_focused()
    expect(panel.locator('[data-request-recap]')).to_have_attribute(
        'data-request-recap', 'ticket:' + second_row.get_attribute('data-ticket-id'))
    second_row.press('Shift+Tab')
    expect(first_row).to_be_focused()
    leave()

    # Keyboard focus opens the same panel without pointer entry. It must shrink.
    page.emulate_media(reduced_motion="no-preference")
    worker["recap"] = "Initial recap"
    refresh()
    page.keyboard.press('Tab')
    row.focus()
    expect(panel).to_have_attribute('aria-hidden', 'false')
    set_lines(3)
    panel_settled()
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
    panel_settled()
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
    # The person's own scrolling of the rail hides the panel while the pointer
    # remains on it. Reopening must size to the empty recap, even when no reply
    # ever arrives. A bare scroll event no longer dismisses it, because layout and
    # data refreshes fire those too.
    page.locator('[data-active-work-scroll]').dispatch_event('wheel')
    expect(panel).to_have_attribute('aria-hidden', 'true')
    open_panel()
    assert panel.bounding_box()["height"] < entered - 1
    leave()

    fixture.pop("recap_worker")
    proof["seconds"] = round(time.perf_counter() - started, 3)
    (evidence / 'recap-floor-geometry.json').write_text(json.dumps(proof, indent=2))
    print('RECAP_FLOOR_PROOF ' + json.dumps({"seconds": proof["seconds"], "sessions": proof["sessions"]}), flush=True)


def verify_selection_washes(page, url, fixture, evidence):
    """Real layout/WAAPI is necessary to prove washes follow transformed rows.

    Reuse the App host and mocked navigation source. No backend or new host.
    Existing cases cover popup content/delays, touch hold, and phone navigation.
    """
    started = time.perf_counter()
    one = {"id": "wash_one", "manager_id": "wash_a", "title": "Build the selected rail row",
           "worker_type": "coding", "stage": "needs_implementation", "ticket_status": "agent"}
    two = {**one, "id": "wash_two", "title": "Review the matching geometry", "awaiting_approval": True}
    fixture.update(enabled=True, rail_only_tickets=["wash_one", "wash_two"], board={"managers": [
        {"id": "wash_a", "title": "Build the two-tier selection washes in the Workspace rail", "project": "Panels", "project_id": "wash_project", "created_at": 1, "tickets": [one, two]},
        {"id": "wash_b", "title": "A second selected group", "created_at": 2, "tickets": []},
        {"id": "wash_settled", "title": "A settled selection", "created_at": 3, "settled": True, "tickets": []},
    ]}, chief={"label": "Chief of Staff"})
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.emulate_media(reduced_motion="no-preference")
    page.mouse.move(1300, 950)
    page.goto(url + "#/workspace/utility/settings", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    row = page.locator('.worker-row[data-ticket-id="wash_one"]')
    expect(row).to_be_visible()
    page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")

    def selection_settled(selector):
        # The selected control carries the route, and every wash and ghost has landed.
        expect(page.locator(selector)).to_have_attribute("aria-current", "page")
        wait_settled(page, selector, ".rail-wash")

    # Warm the intended selected route before any frame-level checks.
    page.locator('[data-manager="wash_a"] .item-head').click()
    page.mouse.move(1300, 950)
    selection_settled('[data-manager="wash_a"] .item-head')
    page.evaluate("""() => {
      window.__washProof=[];
      window.__washSample=selector=>{
        const target=document.querySelector(selector);
        if(!target)return {missing:true};
        const rect=n=>{if(!n)return null;const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
        const host=target.closest('.active-work-scroll,.settled-body,.rail-utilities');
        const group=target.closest('.manager-card');
        const item=host.querySelector('.rail-wash--item:not(.rail-wash--leaving)');
        // Only the open item is lit: no layer marks the Manager the item sits in (owner).
        const context=host.querySelector('.rail-wash--group');
        return {target:rect(target.closest('.settled-row')??target),group:rect(group),item:rect(item),
          itemHidden:item.hidden,contextPresent:!!context,
          targetBackground:getComputedStyle(target).backgroundColor,targetOutline:getComputedStyle(target).outlineStyle,
          itemBackground:getComputedStyle(item).backgroundColor,itemOpacity:+getComputedStyle(item).opacity,
          reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,
          motionWash:getComputedStyle(host).getPropertyValue('--motion-wash'),
          moving:!!target.closest('.rail-moving,.rail-travelling,.rail-travelling-card'),
          effects:document.getAnimations().filter(a=>a.effect?.target?.classList?.contains('rail-wash')&&a.playState==='running').length};
      };
      window.__washCapture=selector=>{
        const frames=[],start=performance.now();
        window.__washProof.push({selector,frames});
        window.__washDone=false;
        const take=()=>{frames.push({ms:performance.now()-start,...window.__washSample(selector)});
          if(performance.now()-start<720)requestAnimationFrame(()=>setTimeout(take,0));else window.__washDone=true;};
        // Sample after frame callbacks, so the wash controller paints before measurement.
        requestAnimationFrame(()=>setTimeout(take,0));
      };
    }""")
    samples = []

    def sample(selector):
        return page.evaluate("selector=>window.__washSample(selector)", selector)

    def aligned(frame, grouped=True):
        assert not frame["itemHidden"], frame
        target, wash = frame["target"], frame["item"]
        assert abs(target["y"] - wash["y"]) < 1.6 and abs(target["height"] - wash["height"]) < 1.6, frame
        if grouped:
            group = frame["group"]
            assert abs(wash["x"] - group["x"] - 4) < 1.6, frame
            assert abs(wash["width"] - group["width"] + 8) < 1.6, frame
        assert not frame["contextPresent"], frame
        assert frame["itemBackground"] == "rgb(53, 53, 53)", frame

    def capture(name, selector, change):
        page.evaluate("selector=>window.__washCapture(selector)", selector)
        change()
        # The midpoint frame: the recorder has sampled the first 70 ms of the change.
        page.wait_for_function("window.__washProof.at(-1).frames.at(-1)?.ms >= 70")
        page.screenshot(path=str(evidence / f"selection-wash-{name}-midpoint.png"))
        page.wait_for_function("window.__washDone")
        frames = page.evaluate("window.__washProof.at(-1).frames")
        samples.append({"name": name, "frames": frames})
        (evidence / 'selection-wash-frames.json').write_text(json.dumps(samples, indent=2))
        return frames

    selector = '.worker-row[data-ticket-id="wash_one"]'
    for width in [232, 520]:
        grip = page.get_by_role('separator', name='Resize workspace panel')
        grip.focus()
        for _ in range(20):
            page.keyboard.press('Shift+ArrowLeft' if width == 232 else 'Shift+ArrowRight')
        expect(grip).to_have_attribute('aria-valuenow', str(width))
        row.click()
        page.mouse.move(1300, 950)
        selection_settled(selector)
        frame = sample(selector)
        aligned(frame)
        page.screenshot(path=str(evidence / f'selection-wash-rail-{width}.png'))

    frames = capture('within-group', '.worker-row[data-ticket-id="wash_two"]', lambda: page.locator('.worker-row[data-ticket-id="wash_two"]').evaluate('node=>node.click()'))
    travel = [f['item']['y'] for f in frames if not f.get('missing')]
    assert max(travel) - min(travel) > 5, frames
    assert len({round(y, 1) for y in travel}) > 3, frames
    assert any(100 <= f['ms'] < 200 and abs(f['item']['y'] - f['target']['y']) > 1 for f in frames), frames
    assert all(abs(f['item']['y'] - f['target']['y']) < 1.6 for f in frames if f['ms'] >= 350), frames
    aligned(frames[-1])
    frames = capture('deepen', '[data-manager="wash_b"] .item-head', lambda: page.locator('[data-manager="wash_b"] .item-head').evaluate('node=>node.click()'))
    assert not any(f['contextPresent'] for f in frames), frames
    assert any(f['itemOpacity'] < .95 for f in frames), frames
    page.screenshot(path=str(evidence / 'selection-wash-deepen-final.png'))

    # The selected row changes bucket while its real WAAPI transform moves it.
    row.evaluate('node=>node.click()')
    selection_settled(selector)
    page.mouse.move(1300, 950)
    def move_bucket():
        one['awaiting_approval'] = True
        page.evaluate('void window.__refreshNavigation()')
    frames = capture('selected-row-bucket-move', selector, move_bucket)
    moving = [f for f in frames if f.get('moving')]
    assert len(moving) > 3, frames
    assert max(f['target']['y'] for f in moving) - min(f['target']['y'] for f in moving) > 5, frames
    for frame in moving:
        aligned(frame)
        # The moving row must not paint an opaque surface over its wash.
        assert frame['targetBackground'] == 'rgba(0, 0, 0, 0)' and frame['targetOutline'] == 'none', frame
    aligned(frames[-1])
    page.screenshot(path=str(evidence / 'selection-wash-bucket-final.png'))

    # Decorative space does not navigate. Only the complete head identity selects.
    before = page.evaluate('location.hash')
    page.locator('[data-manager="wash_a"] .bucket-label').first.click()
    assert page.evaluate('location.hash') == before
    page.locator('[data-manager="wash_a"] .manager-eyebrow > span').click()
    expect(page.locator('[data-manager="wash_a"] .item-head')).to_have_attribute('aria-current', 'page')
    page.mouse.move(1300, 950)
    row.hover()
    expect(page.locator('[data-manager="wash_a"] .settle')).to_have_css('opacity', '0')
    hover = page.locator('.active-work-scroll > .rail-wash--hover:not(.rail-wash--leaving)')
    expect(hover).not_to_have_attribute('hidden', '')
    row.click()
    # The fills are see-through: a click on the hovered row lights it at once and never shows the
    # hover fill (or a fading copy of it) under the selected one, which would flash brighter.
    page.evaluate('async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }')
    lit = page.evaluate("""() => { const host=document.querySelector('.active-work-scroll'); const item=host.querySelector(':scope > .rail-wash--item:not(.rail-wash--leaving)');
      return {hover:[...host.querySelectorAll(':scope > .rail-wash--hover')].filter(n=>!n.hidden).length, itemOpacity:+getComputedStyle(item).opacity, itemHidden:item.hidden}; }""")
    assert lit == {"hover": 0, "itemOpacity": 1, "itemHidden": False}, lit
    page.mouse.move(1300, 950)
    selection_settled(selector)
    expect(hover).to_have_attribute('hidden', '')
    page.keyboard.press("Tab")
    row.focus()
    page.evaluate('async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }')
    ring = page.locator('.active-work-scroll > .rail-wash--focus')
    expect(ring).not_to_have_attribute('hidden', '')
    expect(row).to_have_css('outline-style', 'none')
    ring_box, item_box = ring.bounding_box(), sample(selector)['item']
    assert ring_box and all(abs(ring_box[key] - item_box[key]) < 1.6 for key in ['x', 'y', 'width', 'height']), (ring_box, item_box)
    page.screenshot(path=str(evidence / 'selection-wash-keyboard.png'))

    # Goals use the same selected-row geometry inside Chief, before its Workers.
    goal = {"id": "wash_goal", "title": "Ship the rail", "active": True, "completed": False,
            "kind": "binary", "holder_kind": "chief", "holder_id": None, "created_at": 1, "updated_at": 1,
            "project_id": None, "why": "", "deadline": None, "primary_chart": None,
            "primary_checklist_block_id": None, "revision": 0, "status": "neutral",
            "status_date": time.strftime("%Y-%m-%d"), "next_refresh_at": int(time.time()) + 86400,
            "employee_backend": "codex", "employee_launch_model": "", "employee_launch_reasoning_effort": None,
            "page": {"kind": "headroom.page", "version": 1, "title": "Approach", "summary": "", "blocks": []}}
    fixture["goals"] = [goal]
    sampler = page.evaluate("window.__washSample.toString()")
    page.reload(wait_until="domcontentloaded")
    page.evaluate("code=>window.__washSample=eval('('+code+')')", sampler)
    goal_row = page.locator('[data-goal-id="wash_goal"]')
    expect(goal_row).to_be_visible()
    with page.expect_response(lambda response: urlsplit(response.url).path == "/api/resource-body"
                              and parse_qs(urlsplit(response.url).query).get("key") == ["/api/chief/conversation/start-values"]) as launch:
        goal_row.click()
    assert launch.value.ok
    page.mouse.move(1300, 950)
    selection_settled('[data-goal-id="wash_goal"]')
    aligned(sample('[data-goal-id="wash_goal"]'))
    fixture["goals"] = []

    # Ungrouped selections have the same one fill.
    page.locator('.settled-head').click()
    page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
    settled_selector = '[data-settled-item="wash_settled"] .settled-title'
    page.locator(settled_selector).click()
    page.mouse.move(1300, 950)
    selection_settled(settled_selector)
    aligned(sample(settled_selector), grouped=False)
    page.locator('.rail-utilities [aria-label="Settings"]').click()
    selection_settled('.rail-utilities [aria-label="Settings"]')
    aligned(sample('.rail-utilities [aria-label="Settings"]'), grouped=False)

    # Rapid reversal cannot leave a stale target or fading ghost behind.
    page.evaluate("""async () => {
      document.querySelector('.worker-row[data-ticket-id="wash_one"]').click();
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
      document.querySelector('[data-manager="wash_b"] .item-head').click();
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
      document.querySelector('.worker-row[data-ticket-id="wash_two"]').click();
    }""")
    selection_settled('.worker-row[data-ticket-id="wash_two"]')
    aligned(sample('.worker-row[data-ticket-id="wash_two"]'))
    expect(page.locator('.rail-wash--leaving')).to_have_count(0)
    page.emulate_media(reduced_motion='reduce')
    row.evaluate('node=>node.click()')
    page.evaluate('() => new Promise(requestAnimationFrame)')
    frame = sample(selector)
    aligned(frame)
    assert frame['effects'] == 0, frame
    page.screenshot(path=str(evidence / 'selection-wash-reduced.png'))

    # The hover wash is the box of the row that responds. A header's padding and a settled
    # row's padding are inside it, and the bubble's own inset is outside it. A Manager's only
    # goal is its own goal row (redesign), so it is washed, lit and clicked as that row.
    # Reduced motion lets the hover wash land in one frame.
    fixture["goals"] = [{**goal, "id": "wash_sole", "title": "Ship the second group", "holder_kind": "manager", "holder_id": "wash_b"}]
    page.reload(wait_until="domcontentloaded")
    sole_row = '[data-manager="wash_b"] .goals-rail a.goal[data-goal-id="wash_sole"]'
    expect(page.locator(sole_row)).to_be_visible()
    page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")

    def hover_at(row_selector, host_selector, where):
        page.mouse.move(*where(page.locator(row_selector).bounding_box()))
        return page.evaluate("""async ([row, host]) => {
          await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
          const node=document.querySelector(row), wash=document.querySelector(host+' > .rail-wash--hover:not(.rail-wash--leaving)');
          const box=n=>{const r=n.getBoundingClientRect();return [r.x,r.y,r.width,r.height];};
          return {hover:node.matches(':hover'), hidden:wash.hidden, row:box(node), wash:box(wash)};
        }""", [row_selector, host_selector])

    def responds(state):
        assert state["hover"] and not state["hidden"], state
        assert all(abs(a - b) < 1.6 for a, b in zip(state["row"], state["wash"], strict=True)), state

    # The header spans the bubble less its 4px inset on each side and ends 4px past the name's
    # line. Without this the header could shrink back to its content and the wash with it.
    for manager in ["wash_a", "wash_b"]:
        header, card, line = page.evaluate("""manager => {
          const card=document.querySelector(`[data-manager="${manager}"]`);
          const box=n=>{const r=n.getBoundingClientRect();return [r.x,r.y,r.width,r.bottom];};
          const head=card.querySelector('.item-head'), nudge=parseFloat(getComputedStyle(head).top)||0;
          const line=box(head); line[3]-=nudge;
          return [box(card.querySelector('.identity')), box(card), line];
        }""", manager)
        expected = [card[0] + 4, header[1], card[2] - 8, line[3] + 4]
        assert all(abs(a - b) < 1.6 for a, b in zip(header, expected, strict=True)), (manager, header, expected)
        assert 4 - 0.6 <= header[1] - card[1] <= 7 + 0.6, (manager, header, card)

    def bottom_edge(box):
        return box["x"] + box["width"] / 2, box["y"] + box["height"] - 2

    def left_edge(box):
        return box["x"] + 2, box["y"] + box["height"] / 2

    def middle(box):
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    responds(hover_at('[data-manager="wash_a"] .identity', '.active-work-scroll', bottom_edge))
    responds(hover_at('[data-manager="wash_b"] .identity', '.active-work-scroll', left_edge))
    responds(hover_at('[data-manager="wash_b"] .identity', '.active-work-scroll', bottom_edge))
    responds(hover_at(sole_row, '.active-work-scroll', left_edge))
    responds(hover_at(sole_row, '.active-work-scroll', middle))
    outside = hover_at('[data-manager="wash_b"] .identity', '.active-work-scroll', lambda box: (box["x"] + box["width"] / 2, box["y"] - 2))
    assert not outside["hover"] and outside["hidden"], outside
    page.mouse.click(*left_edge(page.locator(sole_row).bounding_box()))
    expect(page.locator(sole_row)).to_have_attribute("aria-current", "page")
    if page.locator('.settled-head').get_attribute("aria-expanded") == "false":
        page.locator('.settled-head').click()
    page.wait_for_function("!document.querySelector('[data-rail-motion-busy]')")
    settled_row = '[data-settled-item="wash_settled"] .settled-row'
    responds(hover_at(settled_row, '.settled-body', left_edge))
    page.mouse.click(*left_edge(page.locator(settled_row).bounding_box()))
    expect(page.locator('[data-settled-item="wash_settled"] .settled-title')).to_have_attribute("aria-current", "page")
    page.screenshot(path=str(evidence / 'hover-wash-rows.png'))
    fixture["goals"] = []
    page.emulate_media(reduced_motion='no-preference')
    (evidence / 'selection-wash-proof.json').write_text(json.dumps({
        'seconds': round(time.perf_counter() - started, 3), 'newHosts': 0,
        'boundary': 'Real App layout and WAAPI with existing mocked navigation source',
        'samples': samples,
    }, indent=2))
