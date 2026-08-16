"""Measure the painted gaps in the status-headered Ticket groups.

A painted gap is the space between visible ink. It is measured between the text
rectangles of two elements, not between their element boxes, and text inside a shut
<details> is excluded because it paints nothing.

This is a measuring instrument for Ticket t_t5r35v7v, not a test. Run it against an
unchanged build first and check the numbers against the design ticket's evidence page
before trusting an after run.

Usage:
    python scripts/measure_status_gaps.py --base-url http://127.0.0.1:8791 \
        --item si_wvrxb4hu
"""

from __future__ import annotations

import argparse
import json

from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 1440, "height": 1200}

# Returns the union of the client rectangles of every text node that actually paints.
# A node inside a shut <details> is skipped: the browser still reports a rectangle for
# it in some layouts, and it is not ink.
INK_RECT = """
(element) => {
  if (!element) return null;
  // Text inside a shut <details> paints only when it is in that same details' own
  // <summary>. Checking for any ancestor summary is not enough: a nested group's own
  // summary is still hidden when an outer block is shut.
  const shut = (node) => {
    let child = node;
    for (let p = node.parentElement; p; child = p, p = p.parentElement) {
      if (p.tagName === 'DETAILS' && !p.open && child.tagName !== 'SUMMARY') return true;
    }
    return false;
  };
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let top = Infinity, bottom = -Infinity;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.textContent.trim()) continue;
    if (shut(n)) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) {
      if (r.width === 0 && r.height === 0) continue;
      top = Math.min(top, r.top);
      bottom = Math.max(bottom, r.bottom);
    }
  }
  if (top === Infinity) return null;
  return { top, bottom };
}
"""

TYPE_OF = """
(element) => {
  if (!element) return null;
  const s = getComputedStyle(element);
  return {
    family: s.fontFamily.split(',')[0].replace(/["']/g, ''),
    size: s.fontSize,
    weight: s.fontWeight,
  };
}
"""


ROW_TO_ROW = """
(root) => {
  // The first status group anywhere on the page that has two rows to measure between.
  const groups = [...root.querySelectorAll('.sprint-workspace-status')];
  for (const group of groups) {
    if (!group.open) continue;
    const rows = group.querySelectorAll('.ticket-row');
    if (rows.length < 2) continue;
    const inkOf = (el) => {
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let top = Infinity, bottom = -Infinity;
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (!n.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const r of range.getClientRects()) {
          if (r.width === 0 && r.height === 0) continue;
          top = Math.min(top, r.top);
          bottom = Math.max(bottom, r.bottom);
        }
      }
      return top === Infinity ? null : { top, bottom };
    };
    const a = inkOf(rows[0]), b = inkOf(rows[1]);
    if (a && b) return Math.round((b.top - a.bottom) * 10) / 10;
  }
  return null;
}
"""


def ink(page, selector: str, index: int = 0):
    handle = page.locator(selector).nth(index)
    if handle.count() == 0:
        return None
    return handle.evaluate(INK_RECT)


def gap(page, first: str, second: str, first_index: int = 0, second_index: int = 0):
    """Painted gap from the bottom of the first element's ink to the top of the second's."""
    a = ink(page, first, first_index)
    b = ink(page, second, second_index)
    if a is None or b is None:
        return None
    return round(b["top"] - a["bottom"], 1)


def open_page(page, base_url: str, item_id: str) -> None:
    """The app routes on the hash, and its change stream means networkidle never fires."""
    page.goto(f"{base_url}/#/sprint?item={item_id}", wait_until="load")
    page.wait_for_selector(".sprint-workspace-status", timeout=20000)
    page.wait_for_timeout(600)


def open_rail(page, base_url: str) -> None:
    """Open the Workspace rail on its Sprint Item view, with an Item that has two groups."""
    page.goto(f"{base_url}/#/workspace", wait_until="load")
    page.wait_for_selector('[data-workspace-view="items"]', timeout=20000)
    page.click('[data-workspace-view="items"]')
    page.wait_for_timeout(1000)
    heads = page.locator(".board-workspace-item-head")
    for index in range(heads.count()):
        heads.nth(index).click()
        page.wait_for_timeout(700)
        if page.locator(".disclosure--workspace-bucket--nested").count() >= 2:
            return
        heads.nth(index).click()
        page.wait_for_timeout(400)
    raise SystemExit("No rail Item has two groups; group-to-group cannot be measured.")


TODAY = '[data-workspace-section="today"]'
ARTIFACTS = '[data-workspace-section="artifacts"]'
REMAINING = '[data-workspace-section="remaining"]'


def set_open(page, selector: str, state: bool) -> None:
    page.eval_on_selector_all(
        selector, "(list, state) => list.forEach((d) => (d.open = state))", state
    )
    page.wait_for_timeout(250)


def measure_page(page, base_url: str, item_id: str) -> list[dict]:
    """Every seam is measured in a state that is stated, not inherited from the app."""
    rows = []

    def add(name, value, target):
        rows.append({"seam": name, "painted": value, "target": target})

    # The ladder inside a block. Every status group in the today block is open, so its
    # rows paint.
    open_page(page, base_url, item_id)
    set_open(page, f"{TODAY} .sprint-workspace-status", True)
    add(
        "status header to its first row",
        gap(page, f"{TODAY} .sprint-workspace-status > summary",
            f"{TODAY} .sprint-workspace-status .ticket-row"),
        12,
    )
    add(
        "row to row",
        page.locator(".sprint-item-column").evaluate(ROW_TO_ROW),
        18,
    )
    add(
        "status group to status group",
        gap(page, f"{TODAY} .sprint-workspace-status:nth-of-type(1) .ticket-row:last-child",
            f"{TODAY} .sprint-workspace-status:nth-of-type(2) > summary"),
        35,
    )

    # A block label to the first thing under it. The block is open, or its body paints
    # nothing. Artifacts begins with a plain row, Remaining Tickets with a group header:
    # the two cases the rule has to make equal.
    set_open(page, ARTIFACTS, True)
    set_open(page, REMAINING, True)
    # No Sprint Item in the data has an artifact, so the plain-row case is measured
    # against the block's empty line. It pads itself by --space-2, exactly as an
    # artifact row and a Ticket row do, so it stands for a plain row here.
    add(
        "block label to its first plain row (Artifacts, empty line)",
        gap(page, f"{ARTIFACTS} > summary",
            f"{ARTIFACTS} .sprint-workspace-artifact, {ARTIFACTS} .sprint-workspace-empty"),
        12,
    )
    add(
        "block label to its first status header (Remaining)",
        gap(page, f"{REMAINING} > summary",
            f"{REMAINING} .sprint-workspace-status > summary"),
        12,
    )

    # The block seam. The block above ends in a Ticket row, which pads itself, or in a
    # shut group header, which does not. One value has to come out of both.
    open_page(page, base_url, item_id)
    set_open(page, f"{TODAY} .sprint-workspace-status", True)
    set_open(page, ARTIFACTS, False)
    add(
        "block to block, block above ended in a row",
        gap(page, TODAY, f"{ARTIFACTS} > summary"),
        62,
    )

    set_open(page, f"{TODAY} .sprint-workspace-status", False)
    add(
        "block to block, block above ended in a shut group header",
        gap(page, TODAY, f"{ARTIFACTS} > summary"),
        62,
    )

    return rows


def measure_type(page, base_url: str, item_id: str) -> dict:
    open_page(page, base_url, item_id)
    page_header = page.locator(".sprint-workspace-status > summary").first.evaluate(TYPE_OF)
    open_rail(page, base_url)
    sidebar_header = page.locator(
        ".disclosure--workspace-bucket--nested .board-workspace-bucket-label"
    ).first.evaluate(TYPE_OF)
    return {"page status header": page_header, "sidebar status header": sidebar_header}


def measure_sidebar(page, base_url: str) -> list[dict]:
    open_rail(page, base_url)
    return [
        {
            "seam": "sidebar Item title to its first group",
            "painted": gap(
                page,
                ".board-workspace-item--selected .board-workspace-item-title",
                ".board-workspace-item-groups .board-workspace-bucket-label",
            ),
            "target": 27,
        },
        {
            "seam": "sidebar group to group",
            "painted": gap(
                page,
                ".disclosure--workspace-bucket--nested",
                ".disclosure--workspace-bucket--nested + .disclosure--workspace-bucket--nested"
                " .board-workspace-bucket-label",
            ),
            "target": 27,
        },
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--label", default="run")
    parser.add_argument("--screenshot-dir")
    args = parser.parse_args()

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        result = {
            "label": args.label,
            "type": measure_type(page, args.base_url, args.item),
            "page": measure_page(page, args.base_url, args.item),
            "sidebar": measure_sidebar(page, args.base_url),
        }
        if args.screenshot_dir:
            open_page(page, args.base_url, args.item)
            page.locator(".sprint-item-column").screenshot(
                path=f"{args.screenshot_dir}/page-{args.label}.png"
            )
            open_rail(page, args.base_url)
            page.locator(".board-workspace-left").first.screenshot(
                path=f"{args.screenshot_dir}/sidebar-{args.label}.png"
            )
        browser.close()

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
