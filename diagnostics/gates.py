"""The owner's three symptoms, measured under real wheel input.

Usage: gates.py OUTPUT_DIRECTORY LABEL [BASE_URL]

Every gesture here is dispatched as wheel events with realistic deltas and
intervals. Nothing assigns scrollTop. A probe that drives the scroll position
directly reaches states a hand cannot, and measures itself rather than the product.
"""

import json
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
PROBE = (HERE / "probe.js").read_text()
DRAFT = "DRAFT-KEEP-THIS-TEXT-" + "x" * 40

STATE = (
    "() => {\n"
    "  const t = document.querySelector('[data-conversation-thread]');\n"
    "  const i = document.querySelector('[data-conversation-input]');\n"
    "  if (t === null) return {present: false, typed: i ? i.value.length : null,\n"
    "                          rows: "
    "document.querySelectorAll('[data-conversation-view-key]').length};\n"
    "  const spacer = document.querySelector('[data-conversation-reserved-space]');\n"
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
    "  const top = t.getBoundingClientRect().top;\n"
    "  const lastBottom = last ? Math.round(last.getBoundingClientRect().bottom - top + "
    "t.scrollTop) : null;\n"
    "  return {\n"
    "    present: true,\n"
    "    scrollTop: Math.round(t.scrollTop), scrollHeight: Math.round(t.scrollHeight),\n"
    "    clientHeight: Math.round(t.clientHeight),\n"
    "    fromBottom: Math.round(t.scrollHeight - t.clientHeight - t.scrollTop),\n"
    "    blankBelowNewestLine: lastBottom === null ? null : Math.round(t.scrollHeight - "
    "lastBottom),\n"
    "    typed: i ? i.value.length : null,\n"
    "    rows: t.querySelectorAll('[data-conversation-view-key]').length,\n"
    "    filledRows: [...t.querySelectorAll('[data-conversation-view-key]')].filter(n => "
    "n.children.length > 0).length,\n"
    "    jump: !!document.querySelector('.chat-jump')\n"
    "  };\n"
    "}"
)


def settle(page, seconds):
    page.wait_for_timeout(int(seconds * 1000))


def open_conversation(page, base):
    start = time.monotonic()
    page.goto(base + "/#/workspace/chief-of-staff", wait_until="load")
    page.wait_for_selector("[data-conversation-input]", timeout=90000)
    settle(page, 4)
    page.click("[data-conversation-input]")
    page.wait_for_selector("[data-conversation-thread]", timeout=90000)
    first_thread = time.monotonic() - start
    settle(page, 2)
    if page.query_selector("[data-conversation-expand]") is not None:
        page.click("[data-conversation-expand]")
    page.wait_for_selector(
        "[data-conversation-pane][data-conversation-state='opened']", timeout=60000
    )
    settle(page, 9)
    page.evaluate("window.__probe.mark('opened')")
    return first_thread


def hover(page):
    box = page.eval_on_selector(
        "[data-conversation-thread]",
        (
            "n => { const r = n.getBoundingClientRect(); return {x: r.x + r.width / 2, y: "
            "r.y + r.height / 2}; }"
        ),
    )
    page.mouse.move(box["x"], box["y"])


def flick(page, direction, ticks=16, gap=50):
    # Put the pointer back over the thread first. A click moves it, and a wheel
    # delivered anywhere else is answered by a different scroller - which measures
    # the probe rather than the product.
    hover(page)
    for _ in range(ticks):
        page.mouse.wheel(0, direction * 420)
        page.wait_for_timeout(gap)


def inertia(page, direction):
    """What a trackpad still delivers after the fingers have left it."""
    hover(page)
    for delta in (240, 160, 110, 70, 40):
        page.mouse.wheel(0, direction * delta)
        page.wait_for_timeout(30)


def main():
    out = pathlib.Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    label = sys.argv[2]
    base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8831"
    report = {"label": label, "base": base, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(PROBE)
        page = context.new_page()
        report["seconds_to_first_thread"] = round(open_conversation(page, base), 2)
        report["at_open"] = page.evaluate(STATE)

        page.click("[data-conversation-input]")
        page.type("[data-conversation-input]", DRAFT, delay=10)
        settle(page, 1)
        hover(page)

        # 1. Going back through history, fast, the way a hand does it.
        up = []
        for index in range(3):
            before = page.evaluate(STATE)
            flick(page, -1)
            inertia(page, -1)
            settle(page, 3)
            after = page.evaluate(STATE)
            up.append(
                {
                    "round": index,
                    "asked_for": 16 * 420 + 620,
                    "delivered": before["scrollTop"] - after["scrollTop"],
                    "content_grew": after["scrollHeight"] - before["scrollHeight"],
                    "fromBottom": after["fromBottom"],
                }
            )
        report["back_through_history"] = up

        # 2. Coming back down to the newest line, fast.
        down = []
        for index in range(3):
            before = page.evaluate(STATE)
            flick(page, 1)
            inertia(page, 1)
            settle(page, 3)
            after = page.evaluate(STATE)
            down.append(
                {
                    "round": index,
                    "asked_for": 16 * 420 + 620,
                    "delivered": after["scrollTop"] - before["scrollTop"],
                    "content_grew": after["scrollHeight"] - before["scrollHeight"],
                    "fromBottom": after["fromBottom"],
                }
            )
        report["down_to_the_newest_line"] = down

        # 3. Latest. A hand flicks, the momentum decays, the hand reaches the control
        #    and presses it. That is the order a trackpad produces, so it is the order
        #    measured. Pressing while wheel events are still arriving is kept as a
        #    separate, labelled case: it is not something a hand can do.
        presses = []
        for index in range(6):
            momentum_after_the_press = index % 2 == 0
            flick(page, -1, ticks=14, gap=80)
            if not momentum_after_the_press:
                inertia(page, -1)
                settle(page, 0.25)
            settle(page, 2.5)
            before = page.evaluate(STATE)
            if page.query_selector(".chat-jump") is None:
                presses.append({"press": index, "note": "no control offered", "before": before})
                continue
            page.click(".chat-jump")
            if momentum_after_the_press:
                inertia(page, -1)
            settle(page, 6)
            after = page.evaluate(STATE)
            presses.append(
                {
                    "press": index,
                    "case": "wheel still arriving after the press (not a hand)"
                    if momentum_after_the_press
                    else "momentum, then the press",
                    "before_fromBottom": before["fromBottom"],
                    "after_fromBottom": after["fromBottom"],
                    "control_still_offered": after["jump"],
                    "landed": after["fromBottom"] <= 2,
                }
            )
        report["latest"] = presses

        log = page.evaluate("window.__probe.dump()")
        report["structure"] = {
            "transcript_destroyed": sum(
                1 for e in log if e["type"] == "unmount" and e.get("kind") == "transcript"
            ),
            "thread_destroyed": sum(
                1 for e in log if e["type"] == "unmount" and e.get("kind") == "thread"
            ),
            "composer_destroyed": sum(
                1 for e in log if e["type"] == "unmount" and e.get("kind") == "composer-input"
            ),
            "snapshots": sum(
                1 for e in log if e["type"] == "sse-event" and e.get("name") == "snapshot"
            ),
            "history_page_reads": sum(
                1 for e in log if e["type"] == "fetch-start" and "/view/pages" in e.get("url", "")
            ),
        }
        rows = [e for e in log if e["type"] == "row" and e.get("was") is not None]
        report["rows"] = {
            "height_changes": len(rows),
            "from_a_placeholder": sum(1 for e in rows if e["was"] <= 45 and e["height"] > 100),
            "total_absolute_change": sum(abs(e["height"] - e["was"]) for e in rows),
        }
        report["final"] = page.evaluate(STATE)
        page.screenshot(path=str(out / f"{label}.png"))
        (out / f"{label}-timeline.json").write_text(json.dumps(log))
        (out / f"{label}-report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != "latest"}, indent=2))
        print("latest:", json.dumps(report["latest"], indent=2))
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
