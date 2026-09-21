/** A send the server never answered leaves the thread, and the record settles it.
 *
 * Three things only a real pane can show. The copy draws above the composer instead of
 * under every row. The two actions on it work without any held prompt behind them. And a
 * conversation holding one reads the record again the moment the server is reachable,
 * because most of the time the record has had the answer all along.
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
import { scratchDirectory } from "./support/scratch.mjs";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-unanswered-send-"));
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `unanswered-send-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `unanswered-send-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `unanswered-send-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import LiveConversation from "../src/components/conversation/LiveConversation.svelte";
  import { connectionStatus } from "../src/lib/changeStream";

  let theServerAnswers = $state(false);
  let theSendHangs = $state(false);
  (window as any).__theServerAnswers = (answers: boolean) => (theServerAnswers = answers);
  (window as any).__theSendHangs = (hangs: boolean) => (theSendHangs = hangs);
  // What the app-wide connection says. In the real app the change stream sets this.
  (window as any).__setServerReachable = (reachable: boolean) =>
    connectionStatus.set(reachable ? "connected" : "reconnecting");

  async function sendMessage(body: any): Promise<any> {
    (window as any).__sends = [...((window as any).__sends ?? []), body];
    if (theSendHangs) return new Promise(() => undefined);
    if (!theServerAnswers) throw new Error("the connection went away");
    return { conversation_id: "conversation-one", fate: "started" };
  }
</script>

<LiveConversation
  conversationId="conversation-one"
  persistenceKey="unanswered-send-fixture"
  label="Worker"
  conversationState="opened"
  {sendMessage}
/>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    String.raw`const snapshot = {
  conversation_id: "conversation-one",
  backend_key: "codex",
  supports_steer: true,
  model: "gpt-test",
  reasoning_effort: null,
  workspace_folder: "/tmp",
  access: "full",
  role_text: null,
  identity_environment_variable_names: [],
  latest_sequence: 0,
  owner_read_through_sequence: 0,
  is_running: false,
  held_prompts: [],
  pending_permission_ask: null,
  pending_user_input: null,
  composer_catalog: []
};

(globalThis as any).__recordReads = 0;
let theServerIsDown = window.sessionStorage.getItem("server-is-down") === "yes";
(globalThis as any).__theServerIsDown = (down: boolean) => {
  theServerIsDown = down;
  window.sessionStorage.setItem("server-is-down", down ? "yes" : "no");
};
(globalThis as any).fetch = async (input: RequestInfo | URL) => {
  const url = String(input);
  if (theServerIsDown) throw new TypeError("Failed to fetch");
  if (/conversations\/conversation-one\/events\?after=\d+$/.test(url)) {
    (globalThis as any).__recordReads += 1;
    return Response.json({ events: [] });
  }
  if (/conversations\/conversation-one$/.test(url)) return Response.json(snapshot);
  if (/owner-read$/.test(url)) return Response.json({ owner_read_through_sequence: 0 });
  throw new Error("unexpected fetch " + url);
};

class FakeEventSource {
  static opened = 0;
  constructor(url: string) {
    if (/conversations\/conversation-one\/tail/.test(url)) FakeEventSource.opened += 1;
  }
  addEventListener(): void {}
  close(): void {}
}

(globalThis as any).EventSource = FakeEventSource;
(globalThis as any).__tailOpened = () => FakeEventSource.opened;

async function start(): Promise<void> {
  const { mount } = await import("svelte");
  const { default: Host } = await import("./${hostPath.split("/").at(-1)}");
  mount(Host, { target: document.getElementById("app")! });
}
void start();
`,
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

INPUT = "[data-conversation-input]"

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="domcontentloaded")
    page.wait_for_function("window.__tailOpened?.() === 1")

    # A send the server never answers stops claiming to be on its way, and it leaves the
    # thread rather than sitting under every row in it.
    page.locator(INPUT).fill("did this arrive")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sends?.length === 1")
    sent_id = page.evaluate("window.__sends[0].sender_message_id")
    page.locator("[data-conversation-held-stack]").wait_for()
    assert "no answer came" in page.locator("[data-conversation-held-stack]").inner_text()
    assert page.locator("[data-conversation-outgoing='" + sent_id + "']").count() == 0

    # Neither of the record's own promotions is offered, because there is no held prompt.
    assert page.locator('[data-conversation-held-promote="send_now"]').count() == 0
    assert page.locator('[data-conversation-held-promote="steer"]').count() == 0

    # The server coming back is the cue to ask the record again. Nothing here guesses the
    # answer: the read is what settles it, and this pane only decides when to look.
    reads_before = page.evaluate("window.__recordReads")
    page.evaluate("window.__setServerReachable(true)")
    page.wait_for_function(
        "window.__recordReads > " + str(reads_before)
    )

    # Send again is a fresh message with its own name, and it takes the uncertain copy
    # away only once the send is away.
    page.evaluate("window.__theServerAnswers(true)")
    page.locator("[data-conversation-held-send-again='" + sent_id + "']").click()
    page.wait_for_function("window.__sends?.length === 2")
    assert page.evaluate("window.__sends[1].sender_message_id") != sent_id
    assert page.evaluate("window.__sends[1].content[0].text") == "did this arrive"
    page.wait_for_function(
        "!document.querySelector(\"[data-conversation-held-stack]\")?.innerText?.includes('no answer came')"
    )

    # Stopping a copy from being drawn needs no held prompt either.
    page.evaluate("window.__theServerAnswers(false)")
    page.locator(INPUT).fill("and this one")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sends?.length === 3")
    second_id = page.evaluate("window.__sends[2].sender_message_id")
    page.locator("[data-conversation-held-stop-drawing='" + second_id + "']").click()
    page.wait_for_function(
        "!document.querySelector(\"[data-conversation-held-stack]\")?.innerText?.includes('no answer came')"
    )

    # A send still on the wire when the page goes away, coming back to a server that is
    # also away. It says nothing at first, because at that point nothing is known.
    page.evaluate("window.__theSendHangs(true)")
    page.locator(INPUT).fill("in flight when the page went")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sends?.length === 4")
    page.evaluate("window.__theServerIsDown(true)")
    page.reload(wait_until="domcontentloaded")
    page.get_by_text("in flight when the page went", exact=True).wait_for()
    assert page.locator("[data-conversation-held-stack]").count() == 0

    # The server comes back. Nothing asked it yet, so nothing has been told — and then the
    # read happens, the record turns out not to have the message, and only now is it true
    # to say that nobody ever said whether it arrived.
    page.evaluate("window.__theServerIsDown(false)")
    page.evaluate("window.__setServerReachable(true)")
    page.locator("[data-conversation-held-stack]").wait_for()
    assert "no answer came" in page.locator("[data-conversation-held-stack]").inner_text()
    browser.close()

print("live-conversation-unanswered-send-browser.test.mjs: all assertions passed")
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
  assert.match(output, /live-conversation-unanswered-send-browser\.test\.mjs: all assertions passed/);
  console.log("live-conversation-unanswered-send-browser.test.mjs: all assertions passed");
} finally {
  serverProcess?.kill();
  await rm(hostPath, { force: true });
  await rm(mainPath, { force: true });
  await rm(indexPath, { force: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
}

async function availablePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.on("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The static server is still coming up.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("the component host never became ready");
}
