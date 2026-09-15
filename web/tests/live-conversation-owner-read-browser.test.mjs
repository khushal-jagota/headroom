/**
 * LiveConversation must ask the top document about focus when a transcript row arrives.
 * A preview iframe can take focus without producing a top-window blur event, so the
 * attention state captured by listeners alone is not sufficient.
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
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-live-owner-read-"));
const hostPath = join(webRoot, "tests", `.live-owner-read-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.live-owner-read-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.live-owner-read-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import LiveConversation from "../src/components/conversation/LiveConversation.svelte";

  async function sendMessage(): Promise<any> {
    return { conversation_id: "focus-fixture", fate: "started" };
  }
</script>

<iframe title="Preview" srcdoc="<!doctype html><button>Preview body</button>"></iframe>
<LiveConversation
  conversationId="focus-fixture"
  persistenceKey="focus-fixture"
  label="Worker"
  conversationState="opened"
  {sendMessage}
/>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    String.raw`class FakeEventSource {
  static latest: FakeEventSource | null = null;
  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

  constructor(_url: string) {
    FakeEventSource.latest = this;
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: string, data: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: JSON.stringify(data) }));
    }
  }

  close(): void {}
}

(globalThis as any).EventSource = FakeEventSource;
(globalThis as any).__emitConversationRow = (row: unknown) =>
  FakeEventSource.latest?.emit("conversation-event", row);

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

SNAPSHOT = {
    "conversation_id": "focus-fixture",
    "backend_key": "codex",
    "supports_steer": True,
    "model": "gpt-test",
    "reasoning_effort": None,
    "workspace_folder": "/tmp",
    "access": "full",
    "role_text": None,
    "identity_environment_variable_names": [],
    "latest_sequence": 2,
    "owner_read_through_sequence": 0,
    "is_running": False,
    "held_prompts": [],
    "pending_permission_ask": None,
    "pending_user_input": None,
    "composer_catalog": [],
}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.set_default_timeout(5_000)
    owner_reads = []

    def api(route):
        request = route.request
        if request.url.endswith("/events?after=0"):
            route.fulfill(json={"events": []})
        elif request.url.endswith("/owner-read"):
            owner_reads.append(request.post_data_json["through_sequence"])
            route.fulfill(json={"owner_read_through_sequence": owner_reads[-1]})
        elif request.url.endswith("/conversations/focus-fixture"):
            route.fulfill(json=SNAPSHOT)
        else:
            route.abort()

    page.route("**/api/conversation/**", api)
    page.goto(sys.argv[1], wait_until="domcontentloaded")
    page.locator('[title="Preview"]').content_frame.get_by_role("button").click()
    # Headless Chromium keeps reporting top-document focus after the frame click. Model
    # the browser state that reproduces the defect without publishing a window event.
    page.evaluate("""Object.defineProperty(document, "hasFocus", {
      configurable: true,
      value: () => false
    })""")

    page.evaluate("""window.__emitConversationRow({
      conversation_id: "focus-fixture",
      sequence: 1,
      kind: "agent_message",
      payload: { text: "arrived while the preview had focus" },
      created_at: 1
    })""")
    page.wait_for_timeout(100)
    assert owner_reads == [], owner_reads

    page.evaluate("""Object.defineProperty(document, "hasFocus", {
      configurable: true,
      value: () => true
    })""")
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("document.hasFocus()")
    page.wait_for_timeout(100)
    # The focus event must retry the row that arrived while focus was elsewhere.
    # No later transcript row exists to move the component's read effect.
    assert owner_reads == [1], owner_reads
    browser.close()

print("live-conversation-owner-read-browser.test.mjs: all assertions passed")
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
  assert.match(output, /live-conversation-owner-read-browser\.test\.mjs: all assertions passed/);
  console.log("live-conversation-owner-read-browser.test.mjs: all assertions passed");
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
