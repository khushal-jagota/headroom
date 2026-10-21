import json

from playwright.sync_api import sync_playwright

STATE = (
    "() => {\n"
    "  const t = document.querySelector('[data-conversation-thread]');\n"
    "  const content = t.querySelector('[data-conversation-content]');\n"
    "  const spacer = document.querySelector('[data-conversation-reserved-space]');\n"
    "  // Everything that takes up room, the way the viewport's own reader finds it.\n"
    "  const laid = [];\n"
    "  const consider = el => {\n"
    "    if (spacer && el === spacer) return;\n"
    "    if (el.hasAttribute('data-conversation-content')) { for (const k of el.children) "
    "consider(k); return; }\n"
    "    if (el.getClientRects().length > 0) { laid.push(el); return; }\n"
    "    for (const k of el.children) consider(k);\n"
    "  };\n"
    "  for (const k of t.children) consider(k);\n"
    "  const last = laid[laid.length - 1];\n"
    "  const threadTop = t.getBoundingClientRect().top;\n"
    "  const lastBottom = last ? Math.round(last.getBoundingClientRect().bottom - threadTop "
    "+ t.scrollTop) : null;\n"
    "  return {\n"
    "    scrollTop: Math.round(t.scrollTop), scrollHeight: Math.round(t.scrollHeight),\n"
    "    clientHeight: Math.round(t.clientHeight),\n"
    "    fromBottom: Math.round(t.scrollHeight - t.clientHeight - t.scrollTop),\n"
    "    newest_line_bottom: lastBottom,\n"
    "    blank_below_newest_line: lastBottom === null ? null : Math.round(t.scrollHeight - "
    "lastBottom),\n"
    "    reserved_spacer_height: spacer ? Math.round(spacer.getBoundingClientRect().height) "
    ": null,\n"
    "    jump: !!document.querySelector('.chat-jump')\n"
    "  };\n"
    "}"
)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    c = b.new_context(viewport={"width": 1440, "height": 900})
    pg = c.new_page()
    pg.goto("http://127.0.0.1:8767/#/workspace/chief-of-staff", wait_until="load")
    pg.wait_for_selector("[data-conversation-input]", timeout=60000)
    pg.wait_for_timeout(4000)
    pg.click("[data-conversation-input]")
    pg.wait_for_selector("[data-conversation-thread]", timeout=60000)
    pg.wait_for_timeout(2000)
    if pg.query_selector("[data-conversation-expand]"):
        pg.click("[data-conversation-expand]")
    pg.wait_for_timeout(9000)
    out = {"at_open": pg.evaluate(STATE)}
    box = pg.eval_on_selector(
        "[data-conversation-thread]",
        "n=>{const r=n.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}}",
    )
    pg.mouse.move(box["x"], box["y"])
    # Go up a long way, slowly, so every row is measured on the way.
    for _ in range(30):
        pg.mouse.wheel(0, -420)
        pg.wait_for_timeout(200)
    pg.wait_for_timeout(2500)
    out["top_of_travel"] = pg.evaluate(STATE)

    # Now the case the owner cannot use: travel back down toward the newest line.
    down = []
    for i in range(30):
        before = pg.evaluate(STATE)
        pg.mouse.wheel(0, 420)
        pg.wait_for_timeout(200)
        after = pg.evaluate(STATE)
        down.append(
            {
                "tick": i,
                "asked": min(420, before["fromBottom"]),
                "delivered": after["scrollTop"] - before["scrollTop"],
                "fromBottom": after["fromBottom"],
                "scrollHeight": after["scrollHeight"],
            }
        )
    out["down"] = down
    out["after_down"] = pg.evaluate(STATE)
    asked = sum(d["asked"] for d in down)
    got = out["after_down"]["scrollTop"] - out["top_of_travel"]["scrollTop"]
    out["downward_summary"] = {
        "asked_total": asked,
        "delivered_total": got,
        "share_delivered": round(got / asked, 3) if asked else None,
        "still_from_bottom": out["after_down"]["fromBottom"],
    }
    # And fast downward travel, the way a hand flicks it.
    pg.evaluate("document.querySelector('[data-conversation-thread]').scrollTop = 0")
    pg.wait_for_timeout(2500)
    start = pg.evaluate(STATE)
    for _ in range(30):
        pg.mouse.wheel(0, 420)
        pg.wait_for_timeout(50)
    pg.wait_for_timeout(4000)
    out["fast_down"] = {"before": start, "after": pg.evaluate(STATE)}
    print(json.dumps(out, indent=2))
    c.close()
    b.close()
