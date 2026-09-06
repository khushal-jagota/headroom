/** Browser proof for voice availability and first-message dictation. */
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
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-voice-everywhere-"));
const hostPath = join(webRoot, "tests", `.voice-everywhere-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.voice-everywhere-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.voice-everywhere-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import ConversationComposer from "../src/components/conversation/ConversationComposer.svelte";
  import ReviewProposalCard from "../src/components/ReviewProposalCard.svelte";

  const parameters = new URLSearchParams(location.search);
  const review = parameters.get("mode") === "review";
  const conversationId = parameters.get("conversation") === "1" ? "conv-existing" : null;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let sent = $state<string[]>([]);
  (window as any).__sentVoiceMessages = () => sent;
</script>

{#if review}
  <QueryClientProvider client={queryClient}>
    <ReviewProposalCard ticketId="review-voice" field="implementation" />
  </QueryClientProvider>
{:else}
  <ConversationComposer
    {conversationId}
    conversationExists={conversationId !== null}
    showRunPicker={false}
    onSend={async (content) => {
      sent = content.flatMap((piece) => piece.piece === "text" ? [piece.text] : []);
      return true;
    }}
  />
{/if}
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    String.raw`const parameters = new URLSearchParams(location.search);
Object.defineProperty(window, "matchMedia", {
  value: () => ({
    matches: parameters.get("coarse") === "1",
    addEventListener() {},
    removeEventListener() {}
  })
});
if (parameters.get("supported") !== "0") {
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) }
  });
  class FixtureMediaRecorder {
    static isTypeSupported() { return true; }
    mimeType = "audio/webm";
    ondataavailable = null;
    onstop = null;
    start() {}
    stop() {
      this.ondataavailable?.({ data: new Blob(["spoken"], { type: "audio/webm" }) });
      this.onstop?.();
    }
  }
  Object.defineProperty(window, "MediaRecorder", { value: FixtureMediaRecorder });
} else {
  Object.defineProperty(window, "MediaRecorder", { value: undefined });
  Object.defineProperty(navigator, "mediaDevices", { value: undefined });
}
import "../../assets/tokens.css";
import "../../assets/app.css";
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
mount(Host, { target: document.getElementById("app")! });
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
  assert.ok(builtIndex);
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
import sys

MANIFEST = {
  "worker_types": [{
    "worker_type": "coding", "label": "Coding",
    "stages": [
      {"id": "needs_implementation", "label": "Implementation", "gating_field": "implementation", "is_terminal": False, "default_ownership_mode": "worker"},
      {"id": "needs_closeout", "label": "Closeout", "gating_field": "closeout", "is_terminal": False, "default_ownership_mode": "worker"},
      {"id": "done", "label": "Done", "gating_field": None, "is_terminal": True, "default_ownership_mode": None}
    ],
    "dropped": {"id": "dropped", "label": "Dropped", "gating_field": None, "is_terminal": True, "default_ownership_mode": None},
    "advance": {"needs_implementation": "needs_closeout", "needs_closeout": "done"},
    "fields": [{"id": "implementation", "label": "Implementation"}, {"id": "closeout", "label": "Closeout"}],
    "ceiling_range": ["needs_implementation", "needs_closeout", "done"],
    "default_ceiling": "needs_implementation", "worker_profile_id": "panels-worker-coding",
    "default_backend": "codex", "default_model": None, "default_reasoning_effort": None
  }]
}
TICKET = {
  "id": "review-voice", "title": "Review voice", "worker_type": "coding",
  "employee_backend": "codex", "employee_launch_model": None,
  "employee_launch_reasoning_effort": None, "employee_configuration_editable": True,
  "stage": "needs_implementation", "ceiling": "done", "at_cap": "propose",
  "suggested_next_ceiling": "needs_closeout", "priority": "P2",
  "resolved_priority_anchors": {"sprint_item": None, "project": None},
  "ticket_status": "awaiting_approval", "backend_error": None,
  "stage_ownership_overrides": {}, "default_stage_ownership_mode": "worker",
  "effective_stage_ownership_mode": "worker", "conversation_id": None,
  "conversation_history": [], "verdict": None, "trouble_notes": [],
  "guidance": "", "field_values": {},
  "pending_proposal": {"field": "implementation", "body": "Done", "proposed_by": "worker", "created_at": 1},
  "archived_field_content": ""
}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)

    def open_page(query):
        page = browser.new_page(viewport={"width": 1100, "height": 760})
        page.set_default_timeout(5_000)
        page.route("**/api/worker-types", lambda route: route.fulfill(json=MANIFEST))
        page.route("**/api/tickets/review-voice", lambda route: route.fulfill(json=TICKET))
        page.route("**/api/conversation/voice-transcriptions", lambda route: route.fulfill(json={"transcript": "dictated words"}))
        page.route("**/api/conversation/conversations/*/voice-transcriptions", lambda route: route.abort())
        page.goto(sys.argv[1] + query, wait_until="domcontentloaded")
        return page

    # Fine-pointer desktop gets the microphone even before a conversation exists.
    page = open_page("?supported=1")
    page.locator("[data-conversation-input]").wait_for(state="visible")
    assert page.locator("[data-voice-record]").is_visible()
    page.locator("[data-voice-record]").click()
    page.locator("[data-voice-stop]").click()
    page.locator("[data-conversation-input]").wait_for(state="visible")
    assert page.locator("[data-conversation-input]").input_value() == "dictated words"
    page.locator("[data-conversation-input]").fill("dictated words, edited")
    page.locator("[data-conversation-input]").press("Enter")
    page.wait_for_function("window.__sentVoiceMessages().length === 1")
    assert page.evaluate("window.__sentVoiceMessages()") == ["dictated words, edited"]
    page.close()

    # An existing conversation uses the same conversation-independent route.
    page = open_page("?supported=1&conversation=1")
    page.locator("[data-voice-record]").click()
    page.locator("[data-voice-stop]").click()
    page.locator("[data-conversation-input]").wait_for(state="visible")
    assert page.locator("[data-conversation-input]").input_value() == "dictated words"
    page.close()

    # Coarse pointer changes only the fresh empty presentation to voice-first.
    page = open_page("?supported=1&coarse=1")
    assert page.locator("[data-conversation-voice='idle']").is_visible()
    assert not page.locator("[data-conversation-input]").is_visible()
    page.close()

    # Unsupported browsers keep the ordinary composer and expose no dead control.
    page = open_page("?supported=0&coarse=1")
    page.locator("[data-conversation-input]").wait_for(state="visible")
    assert page.locator("[data-voice-record]").count() == 0
    page.close()

    # Proposal revision voice is available on desktop without a conversation link.
    page = open_page("?supported=1&mode=review")
    page.locator("[data-review-revision-input]").wait_for(state="visible")
    assert page.locator("[data-review-revision] [data-voice-record]").is_visible()
    page.locator("[data-review-revision] [data-voice-record]").click()
    page.locator("[data-review-revision] [data-voice-stop]").click()
    page.locator("[data-review-revision-input]").wait_for(state="visible")
    assert page.locator("[data-review-revision-input]").input_value() == "dictated words"
    page.close()

    browser.close()
`;
  await run(join(repositoryRoot, ".venv", "bin", "python"), ["-c", browserScript, url]);
} finally {
  serverProcess?.kill("SIGTERM");
  await Promise.all([
    rm(temporaryDirectory, { recursive: true, force: true }),
    rm(hostPath, { force: true }),
    rm(mainPath, { force: true }),
    rm(indexPath, { force: true })
  ]);
}

async function availablePort() {
  return await new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`fixture server did not start: ${url}`);
}

async function run(command, args) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: repositoryRoot, stdio: "inherit" });
    child.once("error", reject);
    child.once("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${command} exited ${code}`))));
  });
}
