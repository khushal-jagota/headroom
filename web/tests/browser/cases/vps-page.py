# ruff: noqa: E501
"""One shared browser host exercises the mounted page against real retained-data routes.

The intercepted responses come from FastAPI TestClient and Store, not handwritten
wire JSON. Fault injection is restricted to read failure, an expired page, and a
held history response. This case needs Svelte lifecycle, focus and real layout.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "support"))
from vps_metrics_fixture import MetricsFixture  # noqa: E402

EVIDENCE = Path(os.environ.get("PANELS_VPS_ARTIFACT_DIR", str(Path(tempfile.gettempdir()) / "panels-vps-screenshots")))


def run(browser, urls):
    fixture = MetricsFixture()
    for index in range(30):
        fixture.append(cpu=27 if index == 29 else 18 + index % 10)
    context = browser.new_context(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce")
    page = context.new_page()
    calls = []
    errors = []
    held = []
    held_latest = []
    cancelled = []
    mode = {"fail": False, "expire": False, "hold": False, "hold_latest": False}
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("requestfailed", lambda request: cancelled.append(request.url))
    page.clock.install(time=datetime.fromtimestamp(fixture.now, UTC))
    page.clock.pause_at(datetime.fromtimestamp(fixture.now, UTC))

    def respond(route):
        parts = urlsplit(route.request.url)
        path = parts.path
        query = parse_qs(parts.query)
        calls.append((path, query))
        if mode["hold_latest"] and path.endswith("/latest"):
            held_latest.append(route)
            return
        if mode["fail"] and path.endswith("/latest"):
            route.fulfill(status=503, json={"detail": "Controlled read failure"})
            return
        if mode["hold"] and path.endswith("/history") and query.get("resolution") == ["minute"]:
            held.append(route)
            return
        target = path + ("?" + parts.query if parts.query else "")
        if mode["expire"] and path.endswith("/processes") and query.get("offset") == ["250"]:
            mode["expire"] = False
            # An actual expired-snapshot response, followed by a new retained snapshot.
            response = fixture.client.get("/api/vps-metrics/processes?sequence=999999")
            fixture.append(cpu=29, rows=3)
        else:
            response = fixture.client.request(route.request.method, target)
        route.fulfill(status=response.status_code, content_type="application/json", body=response.content)

    page.route("**/api/vps-metrics/**", respond)
    root = page.locator('[data-screen="vps"]')
    cpu = page.locator('[data-vps-metric="cpu"]')
    rows = page.locator('[data-vps-row="processes"]')

    def settle():
        expect(root).to_be_visible()
        expect(rows).to_have_count(2)
        expect(root.locator('[data-vps-metric]')).to_have_count(6)
        page.wait_for_function("document.querySelectorAll('[data-vps-metric] svg').length > 0")

    def tick(milliseconds=2100):
        page.clock.fast_forward(milliseconds)

    def shot(name):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(EVIDENCE / name), full_page=True, animations="disabled")

    try:
        page.goto(urls[0], wait_until="domcontentloaded")
        settle()
        expect(cpu).to_contain_text("27")
        expect(rows.filter(has_text="python")).to_contain_text("20.0% CPU")
        expect(rows.filter(has_text="python")).to_contain_text("400")
        expect(root.get_by_text("Panels", exact=True)).to_be_visible()
        assert "L" in cpu.locator(".trend-line").first.get_attribute("d")
        shot("vps-wide.png")
        for width, name in [(1000, "vps-medium.png"), (390, "vps-compact.png")]:
            page.set_viewport_size({"width": width, "height": 1100})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            shot(name)
        page.set_viewport_size({"width": 1440, "height": 1100})

        tick(5000)
        expect(root).to_have_attribute("data-current", "false")
        expect(root).to_contain_text("no feed")
        expect(cpu).to_contain_text("27")

        fixture.append(cpu=90, overrides={"memory_available_bytes": 3e9, "load_1": 5, "pressure.io.some.avg10": 3})
        tick()
        expect(cpu).to_have_attribute("data-state", "raised")
        shot("vps-raised.png")
        fixture.append(cpu=95, overrides={"memory_available_bytes": 1e9, "load_1": 7, "pressure.io.some.avg10": 20, "pressure.cpu.some.avg10": 60})
        tick()
        expect(cpu).to_have_attribute("data-state", "wrong")
        shot("vps-wrong.png")

        # Current nulls hide an old retained trend and retain their reason.
        fixture.append(cpu=None, partial=True)
        tick()
        expect(cpu).to_contain_text("CPU counter unavailable")
        expect(cpu.locator("svg")).to_have_count(0)
        expect(rows.filter(has_text="python")).to_contain_text("partial")
        shot("vps-partial-null.png")

        # A read failure immediately dims and retains the previous evidence.
        mode["fail"] = True
        tick()
        expect(root).to_contain_text("no feed")
        expect(root.get_by_text("Panels", exact=True)).to_be_visible()
        shot("vps-no-feed.png")
        mode["fail"] = False
        fixture.append()
        tick()
        expect(cpu).to_contain_text("27")

        # Two whole, adjacent 15-day requests per signal reach the actual API.
        month = root.locator('[data-vps-window="30d"]')
        month.focus()
        month.click()
        expect(month).to_be_focused()
        page.wait_for_function("document.querySelector('[data-vps-window=\"30d\"]').getAttribute('aria-pressed') === 'true'")
        expect(root.locator('[data-vps-metric="cpu"] svg')).to_have_count(1)
        coarse = [query for path, query in calls if path.endswith('/history') and query.get('resolution') == ['coarse'] and query.get('signal') == ['cpu_percent']]
        assert len(coarse) == 2, coarse
        assert float(coarse[0]['end'][0]) == float(coarse[1]['start'][0]), coarse
        assert all(float(query['end'][0]) - float(query['start'][0]) == 1296000 for query in coarse), coarse
        shot("vps-long-window.png")

        # Window cancellation must prevent a late old generation from replacing 5m.
        mode["hold"] = True
        root.locator('[data-vps-window="24h"]').click()
        page.wait_for_timeout(30)
        assert held, "Minute history did not start"
        root.locator('[data-vps-window="5m"]').click()
        mode["hold"] = False
        for route in held:
            try:
                route.fulfill(json={"unexpected": "cancelled history must never apply"})
            except Exception:
                pass  # Chromium can discard the aborted request before delivery.
        held.clear()
        expect(cpu.locator("svg")).to_have_count(1)
        expect(root.locator('[data-vps-window="5m"]')).to_have_attribute("aria-pressed", "true")

        # Immutable pagination cannot leak an incomplete 251-process snapshot.
        page.evaluate("""() => {
          window.__processSnapshots = [];
          window.__processObserver = new MutationObserver(() => {
            window.__processSnapshots.push([...document.querySelectorAll('[data-vps-row="processes"]')].map(row => row.textContent).join('|'));
          });
          window.__processObserver.observe(document.querySelector('[data-screen="vps"]'), {subtree:true,childList:true,characterData:true});
        }""")
        fixture.append(rows=251)
        mode["expire"] = True
        tick()
        expect(cpu).to_contain_text("29")
        expect(rows.filter(has_text="node")).to_contain_text("×1")
        process_calls = [query for path, query in calls if path.endswith('/processes')]
        pinned = [query for query in process_calls if query.get('offset') == ['250']]
        assert pinned and pinned[-1].get('sequence'), process_calls
        assert not mode['expire'], process_calls
        assert page.evaluate("!window.__processSnapshots.some(snapshot => snapshot.includes('×248') || snapshot.includes('×249'))")
        page.evaluate("window.__processObserver.disconnect()")

        # A hidden pane stops both reads and activity. Resume renews both.
        mode['hold_latest'] = True
        tick()
        page.wait_for_timeout(20)
        assert held_latest
        page.evaluate("window.__vpsVisible(false)")
        page.wait_for_timeout(20)
        assert any('/api/vps-metrics/latest' in request for request in cancelled), cancelled
        mode['hold_latest'] = False
        for route in held_latest:
            try:
                route.fulfill(json={"unexpected": "cancelled latest must never apply"})
            except Exception:
                pass
        held_latest.clear()
        page.wait_for_timeout(20)
        before = len(calls)
        tick(10000)
        page.evaluate("dispatchEvent(new Event('focus')); dispatchEvent(new Event('online'))")
        page.wait_for_timeout(20)
        assert len(calls) == before, calls[before:]
        page.evaluate("window.__vpsVisible(true)")
        expect(cpu).to_contain_text("29")
        page.wait_for_timeout(20)
        assert any(path.endswith('/activity') for path, _ in calls[before:])
        assert any(path.endswith('/latest') for path, _ in calls[before:])

        # Browser visibility is independent of pane visibility.
        page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'}); document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(20)
        before = len(calls)
        tick(10000)
        assert len(calls) == before, calls[before:]
        page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'}); document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(20)
        assert len(calls) > before
        page.evaluate("window.__vpsMount(false)")
        expect(root).to_have_count(0)
        before = len(calls)
        tick(10000)
        assert len(calls) == before, calls[before:]
        # A failed first read has no retained values. Initial placeholders alone animate.
        mode['hold_latest'] = True
        page.evaluate("window.__vpsMount(true)")
        expect(root).to_have_attribute("aria-busy", "true")
        expect(root).to_contain_text("Reading the machine")
        shot("vps-initial-placeholder.png")
        mode['hold_latest'] = False
        for route in held_latest:
            route.fulfill(status=503, json={"detail": "Controlled first read failure"})
        held_latest.clear()
        expect(root).to_have_attribute("aria-busy", "false")
        expect(root).to_contain_text("no feed")
        expect(cpu).to_contain_text("Unavailable")
        expect(rows).to_have_count(0)
        shot("vps-first-read-unavailable.png")
        assert not errors, errors
    finally:
        context.close()
        fixture.close()
