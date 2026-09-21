/** A closed conversation tail cannot write into the conversation adopted after it. */
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
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-live-switch-"));
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `live-switch-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `live-switch-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `live-switch-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import LiveConversation from "../src/components/conversation/LiveConversation.svelte";

  let conversationId = $state("conversation-a");
  (window as any).__switchConversation = (id: string) => (conversationId = id);

  async function sendMessage(): Promise<any> {
    return { conversation_id: conversationId, fate: "started" };
  }

  async function newConversation(): Promise<void> {
    conversationId = null;
  }
</script>

<LiveConversation
  {conversationId}
  persistenceKey="switch-fixture"
  label="Worker"
  conversationState="opened"
  {sendMessage}
  onNewConversation={newConversation}
/>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    String.raw`const snapshots: Record<string, any> = Object.fromEntries(
  ["conversation-a", "conversation-b"].map((conversationId) => [conversationId, {
    conversation_id: conversationId,
    backend_key: "codex",
    supports_steer: true,
    model: "gpt-test",
    reasoning_effort: null,
    workspace_folder: "/tmp",
    access: "full",
    role_text: null,
    identity_environment_variable_names: [],
    latest_sequence: 4,
    owner_read_through_sequence: 0,
    is_running: false,
    held_prompts: [],
    pending_permission_ask: null,
    pending_user_input: null,
    composer_catalog: []
  }])
);

(globalThis as any).__ownerReads = [];
let delayNextASnapshot = false;
let delayedASnapshotRequested = false;
let releaseDelayedASnapshot: (() => void) | null = null;
const recordedBRows: unknown[] = [];
(globalThis as any).__delayNextASnapshot = () => (delayNextASnapshot = true);
(globalThis as any).__delayedASnapshotRequested = () => delayedASnapshotRequested;
(globalThis as any).__releaseDelayedASnapshot = () => releaseDelayedASnapshot?.();
(globalThis as any).fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const ownerReadMatch = url.match(/conversations\/(conversation-[ab])\/owner-read$/);
  if (ownerReadMatch) {
    const body = JSON.parse(String(init?.body));
    (globalThis as any).__ownerReads.push({
      conversationId: ownerReadMatch[1],
      sequence: body.through_sequence
    });
    return Response.json({ owner_read_through_sequence: body.through_sequence });
  }
  const eventsMatch = url.match(/conversations\/(conversation-[ab])\/events\?after=\d+$/);
  if (eventsMatch) {
    return Response.json({
      events: eventsMatch[1] === "conversation-b"
        ? recordedBRows
        : []
    });
  }
  const snapshotMatch = url.match(/conversations\/(conversation-[ab])$/);
  if (snapshotMatch) {
    if (snapshotMatch[1] === "conversation-a" && delayNextASnapshot) {
      delayNextASnapshot = false;
      delayedASnapshotRequested = true;
      await new Promise<void>((resolve) => (releaseDelayedASnapshot = resolve));
    }
    return Response.json(snapshots[snapshotMatch[1]]);
  }
  throw new Error("unexpected fetch " + url);
};

class FakeEventSource {
  static sources = new Map<string, FakeEventSource>();
  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

  constructor(url: string) {
    const conversationId = url.match(/conversations\/(conversation-[ab])\/tail/)?.[1];
    if (conversationId) FakeEventSource.sources.set(conversationId, this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: string, data: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: JSON.stringify(data) }));
    }
  }

  // Deliberately keep queued callbacks callable after close. This models a browser event
  // that was already dispatched when the pane switched conversations.
  close(): void {}
}

(globalThis as any).EventSource = FakeEventSource;
(globalThis as any).__tailExists = (id: string) => FakeEventSource.sources.has(id);
(globalThis as any).__emitConversationRow = (id: string, row: unknown) => {
  if (id === "conversation-b") recordedBRows.push(row);
  FakeEventSource.sources.get(id)?.emit("conversation-event", row);
};

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

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 900, "height": 700})
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="domcontentloaded")
    page.wait_for_function("window.__tailExists?.('conversation-a')")
    lens_toggle = page.locator("[data-conversation-lens-toggle]")
    lens_toggle.click()
    assert lens_toggle.inner_text() == "Full"

    page.evaluate("window.__switchConversation('conversation-b')")
    page.wait_for_function("window.__tailExists?.('conversation-b')")
    assert lens_toggle.inner_text() == "Full"

    page.locator('[aria-label="Conversation options"]').click()
    page.locator("[data-conversation-new-arm]").click()
    page.locator("[data-conversation-new-confirm]").click()
    assert lens_toggle.inner_text() == "Full"
    page.evaluate("window.__switchConversation('conversation-b')")
    page.wait_for_function("window.__tailExists?.('conversation-b')")
    assert lens_toggle.inner_text() == "Full"
    lens_toggle.click()
    assert lens_toggle.inner_text() == "Focus"

    page.evaluate("""window.__emitConversationRow('conversation-a', {
      conversation_id: 'conversation-a',
      sequence: 9,
      kind: 'prompt',
      payload: { text: 'stale A row', sender_label: 'owner', mode: 'queue' },
      created_at: 9
    })""")
    page.wait_for_timeout(100)
    assert page.get_by_text("stale A row", exact=True).count() == 0
    assert page.evaluate("window.__ownerReads") == []

    page.evaluate("""window.__emitConversationRow('conversation-b', {
      conversation_id: 'conversation-b',
      sequence: 4,
      kind: 'message_to_owner',
      payload: {
        text: 'current B reply',
        sender_label: 'Ticket B',
        sender: { kind: 'ticket', id: 'b' },
        recipient: { kind: 'owner', id: 'owner' }
      },
      created_at: 10
    })""")
    page.get_by_text("current B reply", exact=True).wait_for()
    page.wait_for_function("window.__ownerReads.length === 1")
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4}
    ]
    lens_toggle.click()
    assert lens_toggle.inner_text() == "Full"
    page.evaluate("""window.__emitConversationRow('conversation-b', {
      conversation_id: 'conversation-b',
      sequence: 5,
      kind: 'message_to_owner',
      payload: {
        text: 'current B full reply',
        sender_label: 'Ticket B',
        sender: { kind: 'ticket', id: 'b' },
        recipient: { kind: 'owner', id: 'owner' }
      },
      created_at: 11
    })""")
    page.get_by_text("current B full reply", exact=True).wait_for()
    page.wait_for_function("window.__ownerReads.length === 2")
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4},
        {"conversationId": "conversation-b", "sequence": 5}
    ]

    # A delayed snapshot also loses authority when B opens before it returns. Without
    # the post-await guard, A creates a new stream and replaces B's active feed.
    page.evaluate("window.__delayNextASnapshot()")
    page.evaluate("window.__switchConversation('conversation-a')")
    page.wait_for_function("window.__delayedASnapshotRequested()")
    page.evaluate("window.__switchConversation('conversation-b')")
    page.get_by_text("current B reply", exact=True).wait_for()
    page.evaluate("window.__releaseDelayedASnapshot()")
    page.wait_for_timeout(150)
    assert page.get_by_text("current B reply", exact=True).count() == 1
    assert lens_toggle.inner_text() == "Full"
    assert page.evaluate("window.__ownerReads") == [
        {"conversationId": "conversation-b", "sequence": 4},
        {"conversationId": "conversation-b", "sequence": 5}
    ]

    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__tailExists?.('conversation-a')")
    assert lens_toggle.inner_text() == "Full"
    browser.close()

print("live-conversation-switch-browser.test.mjs: all assertions passed")
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
  assert.match(output, /live-conversation-switch-browser\.test\.mjs: all assertions passed/);
  console.log("live-conversation-switch-browser.test.mjs: all assertions passed");
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
