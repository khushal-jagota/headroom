import json
import time

from playwright.sync_api import sync_playwright

FRAMES = (
    "(() => {\n"
    "  window.__frames = [];\n"
    "  let last = performance.now();\n"
    "  const loop = () => { const now = performance.now(); "
    "window.__frames.push(Math.round(now - last)); last = now; requestAnimationFrame(loop); "
    "};\n"
    "  requestAnimationFrame(loop);\n"
    "})();"
)

MEASURE = """() => {
  const t = document.querySelector('[data-conversation-thread]');
  const items = [...document.querySelectorAll('[data-conversation-view-key]')];
  const filled = items.filter(n => n.children.length > 0);
  return {retained: items.length, mounted: filled.length,
          scrollHeight: t && Math.round(t.scrollHeight),
          clientHeight: t && Math.round(t.clientHeight),
          dom_nodes: document.querySelectorAll('*').length};
}"""


def frames(pg):
    f = pg.evaluate("window.__frames.filter(x => x > 0)")
    f.sort()
    if not f:
        return None
    return {
        "frames": len(f),
        "median_ms": f[len(f) // 2],
        "p95_ms": f[int(len(f) * 0.95)],
        "worst_ms": f[-1],
        "over_50ms": sum(1 for x in f if x > 50),
    }


out = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for height in (900, 2000, 4000):
        c = b.new_context(viewport={"width": 1440, "height": height})
        pg = c.new_page()
        pg.goto("http://127.0.0.1:8767/#/workspace/chief-of-staff", wait_until="load")
        pg.wait_for_selector("[data-conversation-input]", timeout=60000)
        pg.wait_for_timeout(4000)
        pg.click("[data-conversation-input]")
        start = time.monotonic()
        pg.wait_for_selector("[data-conversation-thread]", timeout=60000)
        first = time.monotonic() - start
        pg.wait_for_timeout(2000)
        if pg.query_selector("[data-conversation-expand]"):
            pg.click("[data-conversation-expand]")
        pg.wait_for_timeout(9000)
        at_open = pg.evaluate(MEASURE)
        pg.evaluate(FRAMES)
        box = pg.eval_on_selector(
            "[data-conversation-thread]",
            "n=>{const r=n.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}}",
        )
        pg.mouse.move(box["x"], box["y"])
        for _ in range(20):
            pg.mouse.wheel(0, -420)
            pg.wait_for_timeout(110)
        pg.wait_for_timeout(2500)
        out.append(
            {
                "viewport_height": height,
                "seconds_to_first_thread": round(first, 2),
                "at_open": at_open,
                "after_gesture": pg.evaluate(MEASURE),
                "frames": frames(pg),
            }
        )
        c.close()
    b.close()
print(json.dumps(out, indent=2))
