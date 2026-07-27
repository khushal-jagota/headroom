from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page
from tests.e2e.harness import ServerHandle

WAIT_MS = 10_000

SCROLL_SURFACES = {
    ".markdown pre": "x",
    ".file-preview-document-body": "y",
    ".chat-thread": "y",
    ".chat-menu": "y",
    ".chat-image-previews": "x",
    ".board-workspace-left": "both",
    ".ticket-doc": "y",
}


def _mount_scrollbar_fixture(page: Page, base_url: str) -> None:
    long_code_line = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu "
        "nu xi omicron"
    )
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.set_content(
        f"""
        <!doctype html>
        <html>
          <head>
            <link rel="stylesheet" href="{base_url}/assets/tokens.css">
            <link rel="stylesheet" href="{base_url}/assets/app.css">
            <style>
              body {{ padding: 24px; }}
              .scroll-fixture {{ display: grid; gap: 24px; width: 760px; }}
              .probe-box {{ width: 180px; height: 96px; }}
              .wide-content {{ width: 520px; }}
              .tall-content {{ height: 260px; flex: none; }}
              .chat-thread {{ width: 220px; height: 120px; }}
              .chat-menu {{ width: 240px; height: 120px; }}
              .chat-image-previews {{ width: 180px; }}
              .chat-image-preview {{ flex-basis: 90px; }}
              .board-workspace-left {{ width: 220px; height: 120px; }}
              .ticket-doc {{ width: 220px; height: 120px; }}
            </style>
          </head>
          <body>
            <main class="scroll-fixture">
              <div class="markdown">
                <pre class="probe-box"><code>{long_code_line}</code></pre>
              </div>

              <div class="file-preview-document-body probe-box">
                <div class="tall-content">Markdown preview body</div>
              </div>

              <section class="chat-thread">
                <button type="button">Focusable chat row</button>
                <div class="tall-content">Chat history</div>
              </section>

              <div class="chat-menu" data-chat-menu>
                <button class="chat-menu-item" type="button">
                  <span class="chat-menu-name">/status</span>
                  <span class="chat-menu-desc">Show status</span>
                </button>
                <div class="tall-content">Command list</div>
              </div>

              <div class="chat-image-previews" data-chat-image-previews>
                <div class="chat-image-preview"></div>
                <div class="chat-image-preview"></div>
                <div class="chat-image-preview"></div>
              </div>

              <section class="board-workspace-left" aria-label="Workspace ticket tree">
                <div class="wide-content tall-content">Workspace rail</div>
              </section>

              <main class="ticket-doc">
                <div class="tall-content">Ticket document</div>
              </main>
            </main>
          </body>
        </html>
        """,
    )
    # Not "networkidle": this page has already opened the live-change stream, which stays
    # open for as long as the app runs, so the network never goes quiet and the wait can
    # only time out. The wait that matters is the next one — the stylesheet having actually
    # applied, which is the thing this test reads.
    page.wait_for_function(
        "() => getComputedStyle(document.querySelector('.chat-thread')).fontFamily !== ''",
        timeout=WAIT_MS,
    )


def _scroll_state(page: Page, selector: str) -> dict[str, Any]:
    state: dict[str, Any] = page.eval_on_selector(
        selector,
        """el => {
            const style = getComputedStyle(el);
            const thumb = getComputedStyle(el, "::-webkit-scrollbar-thumb");
            return {
              scrollbarColor: style.scrollbarColor,
              scrollbarGutter: style.scrollbarGutter,
              scrollbarWidth: style.scrollbarWidth,
              thumbBackground: thumb.backgroundColor,
              overflowX: style.overflowX,
              overflowY: style.overflowY,
              clientWidth: el.clientWidth,
              scrollWidth: el.scrollWidth,
              clientHeight: el.clientHeight,
              scrollHeight: el.scrollHeight,
            };
        }""",
    )
    return state


def _assert_scrollable(state: dict[str, Any], axis: str) -> None:
    if axis in ("x", "both"):
        assert state["overflowX"] == "auto", state
        assert state["scrollWidth"] > state["clientWidth"], state
    if axis in ("y", "both"):
        assert state["overflowY"] == "auto", state
        assert state["scrollHeight"] > state["clientHeight"], state


def _is_transparent_scrollbar(state: dict[str, Any]) -> bool:
    is_transparent: bool = (
        state["scrollbarColor"] == "rgba(0, 0, 0, 0) rgba(0, 0, 0, 0)"
        and state["thumbBackground"] == "rgba(0, 0, 0, 0)"
    )
    return is_transparent


def _assert_native_forced_colors_scrollbar(selector: str, state: dict[str, Any]) -> None:
    assert state["scrollbarColor"] == "auto", (selector, state)
    assert state["thumbBackground"] != "rgba(0, 0, 0, 0)", (selector, state)


def test_scroll_containers_share_stable_fine_pointer_treatment(
    server: ServerHandle, context_factory: Callable[[], BrowserContext]
) -> None:
    page = context_factory().new_page()
    _mount_scrollbar_fixture(page, server.base)

    assert page.evaluate("matchMedia('(hover: hover)').matches") is True
    assert page.evaluate("matchMedia('(pointer: fine)').matches") is True

    for selector, axis in SCROLL_SURFACES.items():
        state = _scroll_state(page, selector)
        assert state["scrollbarGutter"] == "stable", (selector, state)
        assert state["scrollbarWidth"] == "thin", (selector, state)
        assert _is_transparent_scrollbar(state), (selector, state)
        _assert_scrollable(state, axis)

    page.locator(".markdown pre").hover()
    pre_hover = _scroll_state(page, ".markdown pre")
    assert not _is_transparent_scrollbar(pre_hover), pre_hover

    page.mouse.move(0, 0)
    page.locator(".chat-thread button").focus()
    thread_focus = _scroll_state(page, ".chat-thread")
    assert not _is_transparent_scrollbar(thread_focus), thread_focus

    image_previews = page.locator(".chat-image-previews")
    box = image_previews.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + 8, box["y"] + 8)
    page.mouse.down()
    try:
        active_state = _scroll_state(page, ".chat-image-previews")
        assert not _is_transparent_scrollbar(active_state), active_state
    finally:
        page.mouse.up()


def test_scroll_containers_keep_visible_baseline_in_touch_context(
    browser: Browser, server: ServerHandle
) -> None:
    context = browser.new_context(
        has_touch=True,
        is_mobile=True,
        viewport={"width": 390, "height": 844},
    )
    try:
        page = context.new_page()
        _mount_scrollbar_fixture(page, server.base)

        assert page.evaluate("matchMedia('(hover: none)').matches") is True
        assert page.evaluate("matchMedia('(pointer: coarse)').matches") is True

        for selector, axis in SCROLL_SURFACES.items():
            state = _scroll_state(page, selector)
            assert state["scrollbarGutter"] == "stable", (selector, state)
            assert state["scrollbarWidth"] == "thin", (selector, state)
            assert not _is_transparent_scrollbar(state), (selector, state)
            _assert_scrollable(state, axis)
    finally:
        context.close()


def test_scroll_containers_keep_native_baseline_in_forced_colors_fine_pointer(
    browser: Browser, server: ServerHandle
) -> None:
    context = browser.new_context(forced_colors="active")
    try:
        page = context.new_page()
        _mount_scrollbar_fixture(page, server.base)

        assert page.evaluate("matchMedia('(forced-colors: active)').matches") is True
        assert page.evaluate("matchMedia('(hover: hover)').matches") is True
        assert page.evaluate("matchMedia('(pointer: fine)').matches") is True

        for selector, axis in SCROLL_SURFACES.items():
            state = _scroll_state(page, selector)
            assert state["scrollbarGutter"] == "stable", (selector, state)
            assert state["scrollbarWidth"] == "thin", (selector, state)
            _assert_native_forced_colors_scrollbar(selector, state)
            _assert_scrollable(state, axis)

        page.locator(".markdown pre").hover()
        pre_hover = _scroll_state(page, ".markdown pre")
        _assert_native_forced_colors_scrollbar(".markdown pre", pre_hover)

        page.mouse.move(0, 0)
        page.locator(".chat-thread button").focus()
        thread_focus = _scroll_state(page, ".chat-thread")
        _assert_native_forced_colors_scrollbar(".chat-thread", thread_focus)

        image_previews = page.locator(".chat-image-previews")
        box = image_previews.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + 8, box["y"] + 8)
        page.mouse.down()
        try:
            active_state = _scroll_state(page, ".chat-image-previews")
            _assert_native_forced_colors_scrollbar(
                ".chat-image-previews", active_state
            )
        finally:
            page.mouse.up()
    finally:
        context.close()
