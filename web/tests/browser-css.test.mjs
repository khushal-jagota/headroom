/**
 * Browser-computed CSS contracts that do not need the Panels application server.
 *
 * A real Chromium CSS engine covers custom properties, pseudo-elements, interaction
 * states, pointer media queries, forced colors, and overflow geometry. A small static
 * server supplies the repository-owned stylesheets; no application route or API is used.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const available = address.port;
  await new Promise((resolve) => server.close(resolve));
  return available;
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`static asset server did not become ready: ${url}`);
}

const browserAssertions = String.raw`
from playwright.sync_api import sync_playwright
import sys

BASE_URL = sys.argv[1]
BRAND_TOKENS = {
    "--accent-bright": "#9aadd2",
    "--accent-surface": "#222a38",
    "--accent-text": "#dce6f8",
    "--accent-ink": "#111318",
}
SEMANTIC_TOKENS = {
    "--accent-done": "#7fa564",
    "--accent-error": "#d85d5d",
}
PRIORITY_TOKENS = {
    "--priority-p0-fill": "#7d2c26",
    "--priority-p0-ink": "#ffe0db",
    "--priority-p1-fill": "#512725",
    "--priority-p1-ink": "#ecccc7",
    "--priority-p2-fill": "#54331f",
    "--priority-p2-ink": "#ecd8c6",
    "--priority-p3-fill": "#534323",
    "--priority-p3-ink": "#e9e4c6",
}
SCROLL_SURFACES = {
    ".markdown pre": "x",
    ".file-preview-document-body": "y",
    ".chat-thread": "y",
    ".chat-menu": "y",
    ".chat-image-previews": "x",
    ".board-workspace-left": "both",
    ".ticket-doc": "y",
}


def mount(page, body, extra_style=""):
    page.goto(BASE_URL + "/assets/tokens.css", wait_until="domcontentloaded")
    page.set_content(f"""
      <!doctype html>
      <html>
        <head>
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <link rel="stylesheet" href="{BASE_URL}/assets/tokens.css">
          <link rel="stylesheet" href="{BASE_URL}/assets/app.css">
          <style>{extra_style}</style>
        </head>
        <body>{body}</body>
      </html>
    """)
    page.wait_for_function(
        "() => getComputedStyle(document.documentElement)"
        ".getPropertyValue('--accent-bright').trim() !== ''"
    )


def colors(page, selector):
    return page.eval_on_selector(selector, """element => {
      const style = getComputedStyle(element);
      return {
        color: style.color,
        backgroundColor: style.backgroundColor,
        borderColor: style.borderColor,
        outlineColor: style.outlineColor,
        filter: style.filter,
      };
    }""")


def assert_brand(browser, mobile):
    context = browser.new_context(
        has_touch=mobile,
        is_mobile=mobile,
        viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800},
    )
    try:
        page = context.new_page()
        mount(page, """
          <span class="nav-badge">2</span>
          <span class="ticket-stage-run ticket-stage-run--attention">awaiting approval</span>
          <span class="chip chip--pending-proposal">pending proposal</span>
          <div class="approval-proposal-shell">
            <button class="button button--primary" type="button">Approve</button>
          </div>
          <div class="markdown"><a href="#comparison">Open comparison</a></div>
          <button class="ticket-row ticket-row--active" type="button">Selected ticket</button>
          <span class="stage-mark stage-mark--current-waiting"></span>
          <span class="stage-mark stage-mark--completed"></span>
          <span class="stage-mark stage-mark--errored"></span>
          <span class="priority-tile priority-tile--p0">P0</span>
          <span class="priority-tile priority-tile--p1">P1</span>
          <span class="priority-tile priority-tile--p2">P2</span>
          <span class="priority-tile priority-tile--p3">P3</span>
        """)
        names = [*BRAND_TOKENS, *SEMANTIC_TOKENS, *PRIORITY_TOKENS]
        tokens = page.evaluate("""names => {
          const style = getComputedStyle(document.documentElement);
          return Object.fromEntries(names.map(name => [
            name, style.getPropertyValue(name).trim().toLowerCase()
          ]));
        }""", names)
        assert {name: tokens[name] for name in BRAND_TOKENS} == BRAND_TOKENS
        assert {name: tokens[name] for name in SEMANTIC_TOKENS} == SEMANTIC_TOKENS
        assert {name: tokens[name] for name in PRIORITY_TOKENS} == PRIORITY_TOKENS
        assert colors(page, ".nav-badge") == {
            "color": "rgb(17, 19, 24)",
            "backgroundColor": "rgb(154, 173, 210)",
            "borderColor": "rgb(17, 19, 24)",
            "outlineColor": "rgb(17, 19, 24)",
            "filter": "none",
        }
        approve = page.locator(".approval-proposal-shell .button--primary")
        assert colors(page, ".button--primary")["backgroundColor"] == "rgb(154, 173, 210)"
        assert colors(page, ".button--primary")["color"] == "rgb(17, 19, 24)"
        approve.hover()
        assert colors(page, ".button--primary")["filter"] == "brightness(1.08)"
        approve.focus()
        assert colors(page, ".button--primary")["outlineColor"] == "rgb(154, 173, 210)"
        assert colors(page, ".markdown a")["color"] == "rgb(154, 173, 210)"
        assert colors(page, ".ticket-stage-run--attention")["color"] == "rgb(154, 173, 210)"
        pending = colors(page, ".chip--pending-proposal")
        assert pending["backgroundColor"] == "rgb(34, 42, 56)"
        assert pending["color"] == "rgb(220, 230, 248)"
        assert pending["borderColor"] == "rgb(154, 173, 210)"
        assert colors(page, ".ticket-row--active")["backgroundColor"] == "rgb(38, 34, 28)"
        assert colors(page, ".stage-mark--current-waiting")["borderColor"] == "rgb(154, 173, 210)"
        assert colors(page, ".stage-mark--completed")["backgroundColor"] == "rgb(127, 165, 100)"
        assert colors(page, ".stage-mark--errored")["backgroundColor"] == "rgb(216, 93, 93)"
        for priority, expected in {
            "p0": ("rgb(125, 44, 38)", "rgb(255, 224, 219)"),
            "p1": ("rgb(81, 39, 37)", "rgb(236, 204, 199)"),
            "p2": ("rgb(84, 51, 31)", "rgb(236, 216, 198)"),
            "p3": ("rgb(83, 67, 35)", "rgb(233, 228, 198)"),
        }.items():
            tile = colors(page, f".priority-tile--{priority}")
            assert (tile["backgroundColor"], tile["color"]) == expected
        assert page.viewport_size == (
            {"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800}
        )
    finally:
        context.close()


def scroll_state(page, selector):
    return page.eval_on_selector(selector, """el => {
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
    }""")


def transparent_scrollbar(state):
    return (
        state["scrollbarColor"] == "rgba(0, 0, 0, 0) rgba(0, 0, 0, 0)"
        and state["thumbBackground"] == "rgba(0, 0, 0, 0)"
    )


def assert_scrollable(state, axis):
    if axis in ("x", "both"):
        assert state["overflowX"] == "auto", state
        assert state["scrollWidth"] > state["clientWidth"], state
    if axis in ("y", "both"):
        assert state["overflowY"] == "auto", state
        assert state["scrollHeight"] > state["clientHeight"], state


def mount_scrollbars(page):
    mount(page, """
      <main class="scroll-fixture">
        <div class="markdown">
          <pre class="probe-box"><code>alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron</code></pre>
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
        <main class="ticket-doc"><div class="tall-content">Ticket document</div></main>
      </main>
    """, """
      body { padding: 24px; }
      .scroll-fixture { display: grid; gap: 24px; width: 760px; }
      .probe-box { width: 180px; height: 96px; }
      .wide-content { width: 520px; }
      .tall-content { height: 260px; flex: none; }
      .chat-thread { width: 220px; height: 120px; }
      .chat-menu { width: 240px; height: 120px; }
      .chat-image-previews { width: 180px; }
      .chat-image-preview { flex-basis: 90px; }
      .board-workspace-left { width: 220px; height: 120px; }
      .ticket-doc { width: 220px; height: 120px; }
    """)


def assert_scrollbars(browser):
    context = browser.new_context()
    try:
        page = context.new_page()
        mount_scrollbars(page)
        assert page.evaluate("matchMedia('(hover: hover)').matches") is True
        assert page.evaluate("matchMedia('(pointer: fine)').matches") is True
        for selector, axis in SCROLL_SURFACES.items():
            state = scroll_state(page, selector)
            assert state["scrollbarGutter"] == "stable", (selector, state)
            assert state["scrollbarWidth"] == "thin", (selector, state)
            assert transparent_scrollbar(state), (selector, state)
            assert_scrollable(state, axis)
        page.locator(".markdown pre").hover()
        assert not transparent_scrollbar(scroll_state(page, ".markdown pre"))
        page.mouse.move(0, 0)
        page.locator(".chat-thread button").focus()
        assert not transparent_scrollbar(scroll_state(page, ".chat-thread"))
        box = page.locator(".chat-image-previews").bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + 8, box["y"] + 8)
        page.mouse.down()
        try:
            assert not transparent_scrollbar(scroll_state(page, ".chat-image-previews"))
        finally:
            page.mouse.up()
    finally:
        context.close()

    touch = browser.new_context(
        has_touch=True, is_mobile=True, viewport={"width": 390, "height": 844}
    )
    try:
        page = touch.new_page()
        mount_scrollbars(page)
        assert page.evaluate("matchMedia('(hover: none)').matches") is True
        assert page.evaluate("matchMedia('(pointer: coarse)').matches") is True
        for selector, axis in SCROLL_SURFACES.items():
            state = scroll_state(page, selector)
            assert state["scrollbarGutter"] == "stable", (selector, state)
            assert state["scrollbarWidth"] == "thin", (selector, state)
            assert not transparent_scrollbar(state), (selector, state)
            assert_scrollable(state, axis)
    finally:
        touch.close()

    forced = browser.new_context(forced_colors="active")
    try:
        page = forced.new_page()
        mount_scrollbars(page)
        assert page.evaluate("matchMedia('(forced-colors: active)').matches") is True
        assert page.evaluate("matchMedia('(hover: hover)').matches") is True
        assert page.evaluate("matchMedia('(pointer: fine)').matches") is True
        for selector, axis in SCROLL_SURFACES.items():
            state = scroll_state(page, selector)
            assert state["scrollbarGutter"] == "stable", (selector, state)
            assert state["scrollbarWidth"] == "thin", (selector, state)
            assert state["scrollbarColor"] == "auto", (selector, state)
            assert state["thumbBackground"] != "rgba(0, 0, 0, 0)", (selector, state)
            assert_scrollable(state, axis)
        page.locator(".markdown pre").hover()
        assert scroll_state(page, ".markdown pre")["scrollbarColor"] == "auto"
        page.mouse.move(0, 0)
        page.locator(".chat-thread button").focus()
        assert scroll_state(page, ".chat-thread")["scrollbarColor"] == "auto"
        box = page.locator(".chat-image-previews").bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + 8, box["y"] + 8)
        page.mouse.down()
        try:
            assert scroll_state(page, ".chat-image-previews")["scrollbarColor"] == "auto"
        finally:
            page.mouse.up()
    finally:
        forced.close()


def assert_document_boundaries(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        page = context.new_page()
        mount(page, """
          <article class="file-preview--embedded" data-file-preview-kind="html">
            <div class="file-preview-document">
              <header class="file-preview-document-header">HTML preview</header>
              <div class="file-preview-document-body">HTML body</div>
            </div>
          </article>
          <article class="file-preview--embedded" data-file-preview-kind="markdown">
            <div class="file-preview-document">
              <header class="file-preview-document-header">Markdown preview</header>
              <div class="file-preview-document-body">Markdown body</div>
            </div>
          </article>
        """)
        state = page.evaluate("""() => {
          const html = document.querySelector('[data-file-preview-kind="html"] .file-preview-document');
          const markdown = document.querySelector('[data-file-preview-kind="markdown"] .file-preview-document');
          const header = html.querySelector('.file-preview-document-header');
          const body = html.querySelector('.file-preview-document-body');
          const markdownBody = markdown.querySelector('.file-preview-document-body');
          const htmlStyle = getComputedStyle(html);
          const headerStyle = getComputedStyle(header);
          const bodyStyle = getComputedStyle(body);
          const markdownBodyStyle = getComputedStyle(markdownBody);
          return {
            borderTopWidth: htmlStyle.borderTopWidth,
            borderColor: htmlStyle.borderColor,
            borderRadius: htmlStyle.borderRadius,
            overflow: htmlStyle.overflow,
            divider: headerStyle.borderBottomWidth,
            htmlSurface: bodyStyle.backgroundColor,
            markdownSurface: markdownBodyStyle.backgroundColor,
          };
        }""")
        assert state["borderTopWidth"] == "1px", state
        assert state["borderColor"] == "rgba(230, 210, 175, 0.11)", state
        assert state["borderRadius"] == "8px", state
        assert state["overflow"] == "hidden", state
        assert state["divider"] == "1px", state
        assert state["htmlSurface"] == "rgb(36, 33, 27)", state
        assert state["markdownSurface"] == "rgb(20, 18, 16)", state
    finally:
        context.close()


def assert_mobile_content_containment(browser):
    context = browser.new_context(
        has_touch=True, is_mobile=True, viewport={"width": 280, "height": 800}
    )
    try:
        page = context.new_page()
        mount(page, """
          <section class="ticket-screen">
            <div class="ticket-page">
              <main class="ticket-doc">
                <div class="ticket-col">
                  <div class="markdown">
                    <table>
                      <thead><tr><th>Field</th><th>Long value</th><th>Another value</th></tr></thead>
                      <tbody><tr><td>one</td><td>an intentionally wide table value</td><td>another intentionally wide value</td></tr></tbody>
                    </table>
                  </div>
                  <details class="disclosure disclosure--stage" open>
                    <summary class="disclosure-summary">
                      <span class="stage-mark"></span>
                      <span class="disclosure-stage-name">implementation</span>
                      <span class="ticket-stage-run">awaiting approval <button class="ticket-stage-run-action">Release</button></span>
                    </summary>
                  </details>
                </div>
              </main>
            </div>
          </section>
        """, """
          body { margin: 0; overflow-x: hidden; }
          .ticket-screen, .ticket-page { width: 100%; height: 240px; }
          .ticket-doc { flex: none; width: 100%; height: 240px; }
          .ticket-col { padding: 16px; }
          .markdown table th, .markdown table td { white-space: nowrap; }
        """)
        geometry = page.evaluate("""() => {
          const table = document.querySelector('.markdown table');
          const doc = document.querySelector('.ticket-doc');
          const run = document.querySelector('.ticket-stage-run');
          const name = document.querySelector('.disclosure-stage-name');
          return {
            documentOverflow: document.documentElement.scrollWidth - innerWidth,
            ticketOverflow: doc.scrollWidth - doc.clientWidth,
            tableOverflow: table.scrollWidth - table.clientWidth,
            tableOverflowX: getComputedStyle(table).overflowX,
            stageRunTop: run.getBoundingClientRect().top,
            stageNameBottom: name.getBoundingClientRect().bottom,
          };
        }""")
        assert geometry["documentOverflow"] <= 0, geometry
        assert geometry["ticketOverflow"] <= 0, geometry
        assert geometry["tableOverflow"] > 0, geometry
        assert geometry["tableOverflowX"] == "auto", geometry
        assert geometry["stageRunTop"] >= geometry["stageNameBottom"], geometry
    finally:
        context.close()


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        assert_brand(browser, False)
        assert_brand(browser, True)
        assert_scrollbars(browser)
        assert_document_boundaries(browser)
        assert_mobile_content_containment(browser)
    finally:
        browser.close()

print("browser CSS assertions passed")
`;

const port = await availablePort();
const baseUrl = `http://127.0.0.1:${port}`;
const staticServer = spawn(
  join(repositoryRoot, ".venv", "bin", "python"),
  ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", repositoryRoot],
  { cwd: repositoryRoot, stdio: "ignore" },
);

try {
  await waitUntilReady(`${baseUrl}/assets/tokens.css`);
  const browserProbe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserAssertions, baseUrl],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "";
  browserProbe.stdout.on("data", (chunk) => { output += chunk; });
  browserProbe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => browserProbe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /browser CSS assertions passed/);
} finally {
  staticServer.kill();
}

console.log("browser-css.test.mjs: all assertions passed");
