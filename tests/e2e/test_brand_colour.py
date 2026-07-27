from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

WAIT_MS = 10_000
EXPECTED_BRAND_TOKENS = {
    "--accent-bright": "#9aadd2",
    "--accent-surface": "#222a38",
    "--accent-text": "#dce6f8",
    "--accent-ink": "#111318",
}
EXPECTED_SEMANTIC_TOKENS = {
    "--accent-done": "#7fa564",
    "--accent-error": "#d85d5d",
}


def _mount_brand_fixture(page: Page, base_url: str) -> None:
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.set_content(
        f"""
        <!doctype html>
        <html>
          <head>
            <link rel="stylesheet" href="{base_url}/assets/tokens.css">
            <link rel="stylesheet" href="{base_url}/assets/app.css">
          </head>
          <body>
            <span class="nav-badge">2</span>
            <span class="ticket-status-display ticket-status-display--attention">
              <span class="ticket-status-dot"></span>awaiting approval
            </span>
            <span class="chip chip--pending-proposal">pending proposal</span>
            <div class="approval-proposal-shell">
              <button class="button button--primary" type="button">Approve</button>
            </div>
            <div class="markdown"><a href="#comparison">Open comparison</a></div>
            <button class="list-row list-row--board active" type="button">Selected ticket</button>
            <span class="stage-mark stage-mark--current-waiting"></span>
            <span class="stage-mark stage-mark--completed"></span>
            <span class="stage-mark stage-mark--errored"></span>
          </body>
        </html>
        """,
    )
    # Not "networkidle": this page has already opened the live-change stream, which stays
    # open for as long as the app runs, so the network never goes quiet and the wait can
    # only time out. The wait that matters is the next one — the stylesheet having actually
    # applied, which is the thing this test reads.
    page.wait_for_function(
        """() => getComputedStyle(document.documentElement)
            .getPropertyValue('--accent-bright').trim() !== ''""",
        timeout=WAIT_MS,
    )


def _custom_properties(page: Page, names: list[str]) -> dict[str, str]:
    return page.evaluate(
        """names => {
            const style = getComputedStyle(document.documentElement);
            return Object.fromEntries(names.map(name => [
              name,
              style.getPropertyValue(name).trim().toLowerCase(),
            ]));
        }""",
        names,
    )


def _colors(page: Page, selector: str) -> dict[str, str]:
    return page.eval_on_selector(
        selector,
        """element => {
            const style = getComputedStyle(element);
            return {
              color: style.color,
              backgroundColor: style.backgroundColor,
              borderColor: style.borderColor,
              outlineColor: style.outlineColor,
              filter: style.filter,
            };
        }""",
    )


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "mobile"])
def test_soft_steel_brand_tokens_reach_representative_states(
    browser: Browser, server, mobile: bool
) -> None:
    context = browser.new_context(
        has_touch=mobile,
        is_mobile=mobile,
        viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800},
    )
    try:
        page = context.new_page()
        _mount_brand_fixture(page, server.base)

        tokens = _custom_properties(
            page,
            [*EXPECTED_BRAND_TOKENS, *EXPECTED_SEMANTIC_TOKENS],
        )
        assert {name: tokens[name] for name in EXPECTED_BRAND_TOKENS} == EXPECTED_BRAND_TOKENS
        assert {name: tokens[name] for name in EXPECTED_SEMANTIC_TOKENS} == EXPECTED_SEMANTIC_TOKENS

        assert _colors(page, ".nav-badge") == {
            "color": "rgb(17, 19, 24)",
            "backgroundColor": "rgb(154, 173, 210)",
            "borderColor": "rgb(17, 19, 24)",
            "outlineColor": "rgb(17, 19, 24)",
            "filter": "none",
        }

        approve = page.locator(".approval-proposal-shell .button--primary")
        assert (
            _colors(page, ".approval-proposal-shell .button--primary")["backgroundColor"]
            == "rgb(154, 173, 210)"
        )
        assert (
            _colors(page, ".approval-proposal-shell .button--primary")["color"] == "rgb(17, 19, 24)"
        )
        approve.hover()
        assert (
            _colors(page, ".approval-proposal-shell .button--primary")["filter"]
            == "brightness(1.08)"
        )
        approve.focus()
        assert (
            _colors(page, ".approval-proposal-shell .button--primary")["outlineColor"]
            == "rgb(154, 173, 210)"
        )

        assert _colors(page, ".markdown a")["color"] == "rgb(154, 173, 210)"
        assert _colors(page, ".ticket-status-display--attention")["color"] == "rgb(154, 173, 210)"
        pending = _colors(page, ".chip--pending-proposal")
        assert pending["backgroundColor"] == "rgb(34, 42, 56)"
        assert pending["color"] == "rgb(220, 230, 248)"
        assert pending["borderColor"] == "rgb(154, 173, 210)"

        assert _colors(page, ".list-row--board.active")["backgroundColor"] == "rgb(38, 34, 28)"
        assert _colors(page, ".stage-mark--current-waiting")["borderColor"] == "rgb(154, 173, 210)"
        assert _colors(page, ".stage-mark--completed")["backgroundColor"] == "rgb(127, 165, 100)"
        assert _colors(page, ".stage-mark--errored")["backgroundColor"] == "rgb(216, 93, 93)"
        assert page.viewport_size == (
            {"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800}
        )
    finally:
        context.close()
