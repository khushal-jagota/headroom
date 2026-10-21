"""Record the conversation viewport in the running application.

Usage: record.py SCENARIO OUTPUT_DIRECTORY [BASE_URL]

Scenarios:
  scrollback  scroll back through history with real wheel gestures, and measure
              what moves under the reader in the quiet window after each gesture
  latest      scroll back, then press the control that goes to the latest message
  navigation  type into the composer, then lose the navigation reads for a moment

Drives a real Chromium against the running application and writes the probe's
timeline plus a measured report into OUTPUT_DIRECTORY.
"""

import json
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
PROBE = (HERE / "probe.js").read_text()
DRAFT = "DRAFT-KEEP-THIS-TEXT-" + "x" * 40

READ_STATE = """() => {
  const t = document.querySelector('[data-conversation-thread]');
  const i = document.querySelector('[data-conversation-input]');
  return {
    present: !!t,
    scrollTop: t && Math.round(t.scrollTop),
    scrollHeight: t && Math.round(t.scrollHeight),
    clientHeight: t && Math.round(t.clientHeight),
    fromBottom: t && Math.round(t.scrollHeight - t.clientHeight - t.scrollTop),
    typed: i ? i.value.length : null,
    jump: !!document.querySelector('.chat-jump'),
    navError: !!document.querySelector('[data-chief-conversation-error]')
  };
}"""


def settle(page, seconds):
    page.wait_for_timeout(int(seconds * 1000))


def open_conversation(page, base, address):
    page.goto(base + "/" + address, wait_until="load")
    page.wait_for_selector("[data-conversation-input]", timeout=60000)
    settle(page, 4)
    page.click("[data-conversation-input]")
    page.wait_for_selector("[data-conversation-thread]", timeout=60000)
    settle(page, 2)
    if page.query_selector("[data-conversation-expand]") is not None:
        page.click("[data-conversation-expand]")
    page.wait_for_selector(
        "[data-conversation-pane][data-conversation-state='opened']", timeout=30000
    )
    settle(page, 8)
    page.evaluate("window.__probe.mark('opened')")


def hover_thread(page):
    box = page.eval_on_selector(
        "[data-conversation-thread]",
        (
            "n => { const r = n.getBoundingClientRect(); return {x: r.x + r.width / 2, y: "
            "r.y + r.height / 2}; }"
        ),
    )
    page.mouse.move(box["x"], box["y"])


def quiet_window(page, label, seconds=2.5):
    """Nothing the reader does happens here. Anything that moves, moved by itself."""
    page.evaluate(f"window.__probe.mark('quiet-start-{label}')")
    before = page.evaluate(READ_STATE)
    settle(page, seconds)
    page.evaluate(f"window.__probe.mark('quiet-end-{label}')")
    after = page.evaluate(READ_STATE)
    return {"label": label, "before": before, "after": after}


def quiet_drift(log, label):
    """The reading anchor's own movement inside one quiet window."""
    start = next(
        (
            i
            for i, e in enumerate(log)
            if e["type"] == "mark" and e.get("name") == f"quiet-start-{label}"
        ),
        None,
    )
    end = next(
        (
            i
            for i, e in enumerate(log)
            if e["type"] == "mark" and e.get("name") == f"quiet-end-{label}"
        ),
        None,
    )
    if start is None or end is None:
        return None
    window = [e for e in log[start:end] if e["type"] == "sample"]
    offsets = [e["anchorOffset"] for e in window if e.get("anchorOffset") is not None]
    heights = [e["scrollHeight"] for e in window if e.get("scrollHeight") is not None]
    lost = any(e.get("anchorHere") is False for e in window)
    return {
        "anchor_moved_pixels": None if len(offsets) < 2 else max(offsets) - min(offsets),
        "content_height_change_pixels": None if len(heights) < 2 else max(heights) - min(heights),
        "anchor_destroyed": lost,
        "samples": len(window),
    }


def row_churn(log):
    grew = [e for e in log if e["type"] == "row" and e.get("was") is not None]
    return {
        "row_height_changes": len(grew),
        "largest_row_growth_pixels": max((e["height"] - e["was"] for e in grew), default=0),
        "rows_that_grew_from_placeholder": sum(
            1 for e in grew if e["was"] <= 45 and e["height"] > 100
        ),
        "total_absolute_row_change_pixels": sum(abs(e["height"] - e["was"]) for e in grew),
    }


def structure(log):
    def count(event, kind):
        return sum(1 for e in log if e["type"] == event and e.get("kind") == kind)

    return {
        "thread_destroyed": count("unmount", "thread"),
        "thread_created": count("mount", "thread"),
        "composer_destroyed": count("unmount", "composer-input"),
        "composer_created": count("mount", "composer-input"),
        "transcript_destroyed": count("unmount", "transcript"),
        "transcript_created": count("mount", "transcript"),
        "live_stream_opens": sum(1 for e in log if e["type"] == "sse-open"),
        "live_stream_closes": sum(1 for e in log if e["type"] == "sse-close"),
        "snapshots": sum(
            1 for e in log if e["type"] == "sse-event" and e.get("name") == "snapshot"
        ),
        "history_page_reads": sum(
            1 for e in log if e["type"] == "fetch-start" and "/view/pages" in e.get("url", "")
        ),
    }


ANCHOR_READ = """() => {
  const t = document.querySelector('[data-conversation-thread]');
  if (t === null || window.__probeState === undefined) return null;
  const a = window.__probeState();
  return a === null ? null : {offset: a.offset, here: a.here, scrollTop: Math.round(t.scrollTop),
                              scrollHeight: Math.round(t.scrollHeight)};
}"""


def scenario_scrollback(page, report):
    """One wheel tick at a time, so the reader's own movement is a known number.

    A tick of 420 pixels upward must move the content under the reader's eye down by
    420 pixels, or by whatever is left before the top. Anything else is a skip.
    """
    hover_thread(page)
    page.evaluate("window.__probe.mark('scrollback-start')")
    page.evaluate("window.__probeAnchor()")
    ticks = []
    for index in range(40):
        before = page.evaluate(ANCHOR_READ)
        if before is None or not before["here"]:
            page.evaluate("window.__probeAnchor()")
            before = page.evaluate(ANCHOR_READ)
            if before is None:
                break
        page.mouse.wheel(0, -420)
        page.wait_for_timeout(260)
        after = page.evaluate(ANCHOR_READ)
        if after is None:
            break
        if not after["here"]:
            ticks.append({"tick": index, "anchor_destroyed": True, "scrollTop": after["scrollTop"]})
            page.evaluate("window.__probeAnchor()")
            continue
        asked = min(420, before["scrollTop"])
        moved = after["offset"] - before["offset"]
        ticks.append(
            {
                "tick": index,
                "asked_for": asked,
                "content_moved": round(moved),
                "skip": round(moved - asked),
                "scrollTop": after["scrollTop"],
                "scrollHeight": after["scrollHeight"],
            }
        )
    page.evaluate("window.__probe.mark('scrollback-end')")
    measured = [t for t in ticks if "skip" in t]
    report["ticks"] = ticks
    report["measured"] = {
        "ticks_measured": len(measured),
        "ticks_with_anchor_destroyed": sum(1 for t in ticks if t.get("anchor_destroyed")),
        "worst_skip_pixels": max((abs(t["skip"]) for t in measured), default=None),
        "ticks_over_2_pixels": sum(1 for t in measured if abs(t["skip"]) > 2),
        "ticks_over_100_pixels": sum(1 for t in measured if abs(t["skip"]) > 100),
        "total_unwanted_movement_pixels": sum(abs(t["skip"]) for t in measured),
    }


def scenario_latest(page, report):
    hover_thread(page)
    attempts = []
    for round_ in range(3):
        for _ in range(16):
            page.mouse.wheel(0, -420)
            page.wait_for_timeout(80)
        settle(page, 2.5)
        before = page.evaluate(READ_STATE)
        page.evaluate(f"window.__probe.mark('latest-press-{round_}')")
        if page.query_selector(".chat-jump") is not None:
            page.click(".chat-jump")
            settle(page, 6)
        after = page.evaluate(READ_STATE)
        attempts.append({"round": round_, "before": before, "after": after})
    report["latest_attempts"] = attempts


def scenario_navigation(page, report):
    page.evaluate("window.__probeAnchor()")
    before = page.evaluate(READ_STATE)
    composer_before = page.evaluate(

            "() => { const i = document.querySelector('[data-conversation-input]'); return "
            "i ? i.value : null; }"

    )
    page.evaluate("window.__probe.mark('navigation-lost')")
    # One brief network blip, the kind a browser on another device meets every day.
    page.context.set_offline(True)
    settle(page, 10)
    during = page.evaluate(READ_STATE)
    page.context.set_offline(False)
    page.evaluate("window.__probe.mark('navigation-back')")
    settle(page, 12)
    after = page.evaluate(READ_STATE)
    report["navigation"] = {
        "typed_before": composer_before,
        "before": before,
        "during_outage": during,
        "after_recovery": after,
        "typed_after": page.evaluate(

                "() => { const i = document.querySelector('[data-conversation-input]'); "
                "return i ? i.value : null; }"

        ),
    }


def scenario_snapback(page, report):
    """Does the thread stay where the reader put it, or come back by itself?"""
    hover_thread(page)
    page.evaluate("window.__probe.mark('snapback-start')")
    steps = []
    for index in range(14):
        at_start = page.evaluate("document.querySelector('[data-conversation-thread]').scrollTop")
        page.mouse.wheel(0, -420)
        page.wait_for_timeout(40)
        right_after = page.evaluate(
            "document.querySelector('[data-conversation-thread]').scrollTop"
        )
        page.wait_for_timeout(900)
        settled = page.evaluate("document.querySelector('[data-conversation-thread]').scrollTop")
        steps.append(
            {
                "step": index,
                "before": round(at_start),
                "right_after_the_wheel": round(right_after),
                "after_it_settled": round(settled),
                "the_wheel_moved_it": round(right_after - at_start),
                "it_moved_back_by": round(settled - right_after),
            }
        )
    page.evaluate("window.__probe.mark('snapback-end')")
    report["steps"] = steps
    report["measured"] = {
        "steps_that_came_back": sum(1 for x in steps if x["it_moved_back_by"] > 2),
        "worst_return_pixels": max((x["it_moved_back_by"] for x in steps), default=0),
        "asked_for_total": sum(-420 for _ in steps),
        "kept_total": round(steps[-1]["after_it_settled"] - steps[0]["before"]) if steps else None,
    }


def scenario_flick(page, report):
    """A fast gesture, the way a trackpad delivers one. Then the hand stops."""
    hover_thread(page)
    rounds = []
    for index in range(3):
        page.evaluate(f"window.__probe.mark('flick-{index}')")
        before = page.evaluate(READ_STATE)
        for _ in range(16):
            page.mouse.wheel(0, -420)
            page.wait_for_timeout(50)
        straight_after = page.evaluate(READ_STATE)
        settle(page, 4)
        settled = page.evaluate(READ_STATE)
        rounds.append(
            {
                "round": index,
                "asked_for_pixels": 16 * 420,
                "before_scrollTop": before["scrollTop"],
                "straight_after_scrollTop": straight_after["scrollTop"],
                "settled_scrollTop": settled["scrollTop"],
                "moved_back_after_the_hand_stopped": settled["scrollTop"]
                - straight_after["scrollTop"],
                "kept_in_the_end": settled["scrollTop"] - before["scrollTop"],
                "content_height_before": before["scrollHeight"],
                "content_height_after": settled["scrollHeight"],
                "jump_offered": settled["jump"],
            }
        )
        # Put the reader back at the newest line for the next round.
        if page.query_selector(".chat-jump") is not None:
            page.click(".chat-jump")
            settle(page, 4)
    report["flicks"] = rounds


SCENARIOS = {
    "scrollback": scenario_scrollback,
    "flick": scenario_flick,
    "snapback": scenario_snapback,
    "latest": scenario_latest,
    "navigation": scenario_navigation,
}


def main():
    scenario = sys.argv[1]
    out = pathlib.Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8767"
    address = "#/workspace/chief-of-staff"

    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script(PROBE)
        page = context.new_page()
        open_conversation(page, base, address)

        page.click("[data-conversation-input]")
        page.type("[data-conversation-input]", DRAFT, delay=10)
        settle(page, 1)
        page.evaluate("window.__probe.mark('typed')")

        report = {
            "scenario": scenario,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "base": base,
            "address": address,
            "at_rest": page.evaluate(READ_STATE),
        }
        SCENARIOS[scenario](page, report)
        settle(page, 2)
        report["final"] = page.evaluate(READ_STATE)

        log = page.evaluate("window.__probe.dump()")
        report["probe_error"] = page.evaluate("window.__probe.error")
        report["structure"] = structure(log)
        report["rows"] = row_churn(log)
        page.screenshot(path=str(out / f"{scenario}.png"))
        (out / f"{scenario}-timeline.json").write_text(json.dumps(log))
        (out / f"{scenario}-report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
