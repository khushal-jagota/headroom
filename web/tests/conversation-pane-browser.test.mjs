/**
 * Browser-only conversation layer contracts.
 *
 * This builds a small Svelte host around the production ConversationPane, then serves
 * only those static assets. No Panels server or API participates. The host supplies the
 * page-owned outside-dismiss gesture that TicketRoute gives the pane in production.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-conversation-layer-"));
const hostPath = join(webRoot, "tests", `.conversation-layer-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.conversation-layer-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.conversation-layer-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import ConversationPane from "../src/components/conversation/ConversationPane.svelte";
  import type { ConversationState } from "../src/lib/conversation/conversationState";

  const words = (index: number) =>
    ("Line " + index + ": the browser keeps this distinct paragraph anchored while the conversation changes height. ").repeat(3);
  let rows = $state<any[]>(
    Array.from({ length: 28 }, (_, index) => ({
      key: "message-" + index,
      kind: "agent_message",
      sequence: index + 1,
      createdAt: 1_000 + index,
      content: [{ piece: "text", text: words(index) }]
    }))
  );
  let nextRowIndex = 28;
  let running = $state(false);
  let conversationState = $state<ConversationState>("rest");

  function dismissConversation(): void {
    conversationState = "rest";
  }

  function dismissBesidePane(event: MouseEvent): void {
    const pressed = event.target;
    if (!(pressed instanceof Element)) return;
    if (pressed.closest("[data-conversation-pane]") !== null) return;
    dismissConversation();
  }

  (window as any).__appendConversationLine = () => {
    const index = nextRowIndex++;
    rows = [
      ...rows,
      {
        key: "message-" + index,
        kind: "agent_message",
        sequence: index + 1,
        createdAt: 1_000 + index,
        content: [{ piece: "text", text: "Newest appended line " + index }]
      }
    ];
  };
  (window as any).__dropFirstConversationLines = (count: number) => {
    rows = rows.slice(count);
  };
  (window as any).__growFirstConversationLine = () => {
    rows = rows.map((row, index) =>
      index === 0
        ? {
            ...row,
            content: [{
              piece: "text",
              text: ("This existing line became much taller above the reader. ").repeat(45)
            }]
          }
        : row
    );
  };
  (window as any).__rememberConversationInput = () => {
    (window as any).__conversationInput = document.querySelector("[data-conversation-input]");
  };
  (window as any).__conversationInputSurvived = () =>
    (window as any).__conversationInput === document.querySelector("[data-conversation-input]");
  (window as any).__showActivePlan = () => {
    rows = [
      ...rows,
      {
        key: "active-prompt",
        kind: "prompt",
        sequence: 1_000,
        createdAt: 2_000,
        content: [{ piece: "text", text: "Continue" }],
        senderLabel: "owner",
        mode: "run_when_free",
        sentAtUnixMilliseconds: 2_000
      },
      {
        key: "active-plan",
        kind: "plan_updated",
        sequence: 1_001,
        createdAt: 2_001,
        entries: [
          { text: "Inspect", status: "completed" },
          { text: "Implement", status: "in_progress" },
          { text: "Verify", status: "pending" }
        ]
      }
    ];
    running = true;
  };
</script>

<main class="fixture-ticket">
  <section class="fixture-document">
    <button type="button" data-ticket-behind onclick={dismissConversation}>Ticket content</button>
  </section>
  <section
    class="ticket-conversation-layer"
    data-conversation-layer-host
    onclickcapture={dismissBesidePane}
  >
    <div class="ticket-conversation-column">
      <ConversationPane
        bind:conversationState
        conversationId="browser-fixture"
        label="Worker"
        conversationExists
        {rows}
        {running}
        outgoingMessages={[]}
        ownSenderLabel="owner"
        showRunPicker={false}
        onSend={async () => true}
      />
    </div>
  </section>
</main>

<style>
  :global(html), :global(body), :global(#app) {
    height: 100%;
    margin: 0;
  }
  .fixture-ticket {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    height: 720px;
    padding: 16px;
  }
  .fixture-document {
    flex: 1;
    min-height: 0;
  }
  .fixture-document button {
    position: fixed;
    inset: 4px auto auto 4px;
    z-index: 3;
  }
  .ticket-conversation-layer {
    align-items: center;
  }
  .ticket-conversation-column {
    box-sizing: border-box;
    width: min(680px, calc(100% - 96px));
  }
</style>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    `import "../../assets/tokens.css";\nimport "../../assets/app.css";\nimport { mount } from "svelte";\nimport Host from "./${hostPath.split("/").at(-1)}";\nmount(Host, { target: document.getElementById("app")! });\n`,
    "utf8"
  );
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath
      .split("/")
      .at(-1)}"></script></body></html>`,
    "utf8"
  );

  await build({
    root: webRoot,
    base: "./",
    configFile: false,
    logLevel: "silent",
    plugins: [svelte()],
    build: {
      emptyOutDir: true,
      outDir: temporaryDirectory,
      rollupOptions: { input: { index: indexPath } }
    }
  });

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", temporaryDirectory],
    { cwd: repositoryRoot, stdio: "ignore" }
  );
  const builtIndex = (await readdir(temporaryDirectory, { recursive: true })).find((path) =>
    path.endsWith(".html")
  );
  assert.ok(builtIndex, "the component build must emit an HTML entry");
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
import sys

PANE = "[data-conversation-pane]"
INPUT = "[data-conversation-input]"
THREAD = "[data-conversation-thread]"

def state(page, expected):
    page.wait_for_selector(f'{PANE}[data-conversation-state="{expected}"]')

def draft(page):
    return page.locator(INPUT).evaluate("""box => ({
        text: box.value,
        start: box.selectionStart,
        end: box.selectionEnd
    })""")

def first_visible_line(page):
    return page.locator(THREAD).evaluate("""thread => {
        const top = thread.getBoundingClientRect().top;
        const rows = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')];
        const row = rows.find(candidate => candidate.getBoundingClientRect().bottom > top + 1);
        return row ? {
          text: row.textContent.trim(),
          top: Math.round(row.getBoundingClientRect().top - top),
          scrollTop: Math.round(thread.scrollTop)
        } : null;
    }""")

def wait_for_anchor(page, held):
    page.wait_for_function("""held => {
      const thread = document.querySelector('[data-conversation-thread]');
      const row = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')]
        .find(candidate => candidate.textContent.trim() === held.text);
      if (!row) return false;
      const top = Math.round(row.getBoundingClientRect().top - thread.getBoundingClientRect().top);
      return Math.abs(top - held.top) <= 3;
    }""", arg=held)
    restored = first_visible_line(page)
    assert restored is not None
    assert restored["text"] == held["text"]
    assert abs(restored["top"] - held["top"]) <= 3, (held, restored)
    return restored

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1000, "height": 760})
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="domcontentloaded")

    # Rest, peeked, and opened are three heights of one mounted conversation.
    state(page, "rest")
    assert page.locator("[data-conversation-rest-bar]").is_visible()
    assert "Line 27:" in page.locator("[data-conversation-rest-line]").inner_text()
    assert page.locator(THREAD).count() == 1
    assert not page.locator(THREAD).is_visible()

    page.locator(INPUT).click()
    state(page, "peeked")
    assert page.locator(THREAD).is_visible()
    assert page.locator("[data-conversation-rest-bar]").count() == 0

    page.locator(INPUT).fill("the draft stays exactly here")
    page.locator(INPUT).evaluate("box => box.setSelectionRange(9, 9)")
    page.evaluate("window.__rememberConversationInput()")
    expected_draft = {"text": "the draft stays exactly here", "start": 9, "end": 9}

    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    assert draft(page) == expected_draft, (draft(page), expected_draft)
    assert page.evaluate("window.__conversationInputSurvived()") is True

    # Ticket content dismisses opened in one step without replacing or clearing the draft.
    page.locator("[data-ticket-behind]").click()
    state(page, "rest")
    assert draft(page) == expected_draft, (draft(page), expected_draft)
    assert page.evaluate("window.__conversationInputSurvived()") is True

    # A press beside the centered pane has the same dismissal contract.
    page.locator(INPUT).click()
    expected_draft = draft(page)
    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    layer = page.locator("[data-conversation-layer-host]").bounding_box()
    pane = page.locator(PANE).bounding_box()
    assert layer is not None and pane is not None
    page.mouse.click(layer["x"] + 2, layer["y"] + layer["height"] / 2)
    state(page, "rest")
    assert draft(page) == expected_draft, (draft(page), expected_draft)

    # A reader's anchor survives a height change.
    page.locator(INPUT).click()
    state(page, "peeked")
    thread = page.locator(THREAD)
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      return thread && thread.scrollHeight > thread.clientHeight && thread.scrollTop > 0;
    }""")
    thread.hover()
    page.mouse.wheel(0, -550)
    page.wait_for_timeout(50)
    anchored = first_visible_line(page)
    assert anchored is not None
    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    restored = first_visible_line(page)
    assert restored is not None
    assert restored["text"] == anchored["text"]
    assert abs(restored["top"] - anchored["top"]) <= 3, (anchored, restored)

    # Removing a screenful above the reader moves the thread, not their anchored line.
    page.evaluate("window.__dropFirstConversationLines(6)")
    page.wait_for_function("""() =>
      document.querySelectorAll('[data-conversation-row="agent_message"]').length === 22
    """)
    after_removal = wait_for_anchor(page, anchored)
    assert after_removal["scrollTop"] < anchored["scrollTop"], (anchored, after_removal)

    # An existing row growing above the reader produces the equal opposite correction.
    page.evaluate("window.__growFirstConversationLine()")
    page.get_by_text("This existing line became much taller above the reader.", exact=False).wait_for()
    after_growth = wait_for_anchor(page, after_removal)
    assert after_growth["scrollTop"] > after_removal["scrollTop"], (after_removal, after_growth)

    # Latest resumes following, and a newly appended line stays in sight.
    page.locator('button[aria-label="Jump to latest message"]').click()
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      return thread.scrollHeight - thread.scrollTop - thread.clientHeight <= 2;
    }""")
    before = thread.evaluate("node => node.scrollTop")
    page.evaluate("window.__appendConversationLine()")
    page.get_by_text("Newest appended line 28", exact=True).wait_for()
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      const newest = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')].at(-1);
      return newest.getBoundingClientRect().bottom <= thread.getBoundingClientRect().bottom + 1;
    }""")
    assert thread.evaluate("node => node.scrollTop") >= before

    # Opened adds one spacing token after the task strip. Peeked stays flush.
    page.locator("[data-ticket-behind]").click()
    state(page, "rest")
    page.evaluate("window.__showActivePlan()")
    page.locator(INPUT).click()
    state(page, "peeked")
    strip = page.locator("[data-conversation-task-strip]")
    composer = page.locator("[data-conversation-composer]")
    strip_box = strip.bounding_box()
    composer_box = composer.bounding_box()
    assert strip_box is not None and composer_box is not None
    assert round(strip_box["height"]) == 34
    assert round(composer_box["y"] - strip_box["y"] - strip_box["height"]) == 0

    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    strip_box = strip.bounding_box()
    composer_box = composer.bounding_box()
    assert strip_box is not None and composer_box is not None
    assert round(strip_box["height"]) == 34
    assert round(composer_box["y"] - strip_box["y"] - strip_box["height"]) == 8

    browser.close()

print("conversation-pane-browser.test.mjs: all assertions passed")
`;
  const probe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserScript, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] }
  );
  let output = "";
  probe.stdout.on("data", (chunk) => { output += chunk; });
  probe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => probe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /conversation-pane-browser\.test\.mjs: all assertions passed/);
  console.log("conversation-pane-browser.test.mjs: all assertions passed");
} finally {
  serverProcess?.kill();
  await rm(hostPath, { force: true });
  await rm(mainPath, { force: true });
  await rm(indexPath, { force: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
}

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`static component host did not become ready: ${url}`);
}
