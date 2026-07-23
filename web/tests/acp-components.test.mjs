import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { compile } from "svelte/compiler";
import { build } from "vite";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-acp07-components-"));
const hostPath = join(webRoot, "tests", `.acp07-component-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.acp07-component-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.acp07-component-index-${process.pid}.html`);
let serverProcess;

const ticketRouteSource = await readFile(new URL("../src/routes/TicketRoute.svelte", import.meta.url), "utf8");
const boardRouteSource = await readFile(new URL("../src/routes/BoardRoute.svelte", import.meta.url), "utf8");
const employeeConfigurationSource = await readFile(
  new URL("../src/components/EmployeeConfigurationSetup.svelte", import.meta.url),
  "utf8",
);
assert.match(ticketRouteSource, /<EmployeeConfigurationSetup/);
assert.match(ticketRouteSource, /contextRow=\{name === "kickoff" && kickoffCardShowsContextRow/);
assert.match(ticketRouteSource, /\/api\/tickets\/\$\{stableId\}\/employee-configuration/);
assert.match(ticketRouteSource, /kind: "ticketChanged", ticketId: stableId/);
assert.match(ticketRouteSource, /\/acknowledge-completed-response/);
assert.match(boardRouteSource, /settled:\s*\{\s*state: "completed"/);
assert.match(ticketRouteSource, /deferInitialAttach=\{detail\.employee_configuration_editable\}/);
assert.doesNotMatch(ticketRouteSource, /pristineKickoff|employeeBackendOptions|\/employee-backend/);
assert.match(employeeConfigurationSource, /employee_launch_model/);
assert.match(employeeConfigurationSource, /employee_launch_reasoning_effort/);
assert.match(employeeConfigurationSource, /employee-configuration-catalog/);
assert.match(employeeConfigurationSource, /query\.set\("candidate_model", model\)/);
assert.match(employeeConfigurationSource, /AbortController/);
assert.match(employeeConfigurationSource, /requestGeneration/);
assert.match(employeeConfigurationSource, /data-employee-configuration-retry/);
assert.doesNotMatch(ticketRouteSource, /["'](?:hermes|codex|claude(?: code)?)["']/i);
assert.doesNotMatch(employeeConfigurationSource, /["'](?:hermes|codex|claude(?: code)?)["']/i);
assert.doesNotMatch(ticketRouteSource, /<style>|settings|employee backend|ACP backend/i);

for (const fileName of [
  "AcpConversation.svelte",
  "acp/AcpConversationPane.svelte",
  "acp/AcpComposer.svelte",
]) {
  const source = await readFile(new URL(`../src/components/${fileName}`, import.meta.url), "utf8");
  assert.deepEqual(
    compile(source, { filename: fileName, generate: "client" }).warnings,
    [],
    `${fileName} must compile without Svelte warnings`,
  );
}

try {
  await writeFile(hostPath, `
<script lang="ts">
  import AcpConversationPane from "../src/components/acp/AcpConversationPane.svelte";
  import type { ConversationController } from "../src/lib/acp/conversationController";
  import {
    createConversationState,
    projectConversationSnapshot,
    type ConversationSnapshot
  } from "../src/lib/acp/conversationState";

  let deferInitialAttach = $state(true);
  let attached = false;
  let attachCount = 0;
  let promptCount = 0;
  let disposeCount = 0;
  const snapshot = projectConversationSnapshot(createConversationState("ticket-component"));
  const controller: ConversationController = {
    attach() {
      if (attached) return;
      attached = true;
      attachCount += 1;
    },
    snapshot() { return snapshot; },
    subscribe(listener: (value: ConversationSnapshot) => void) {
      listener(snapshot);
      return () => undefined;
    },
    prompt() {
      promptCount += 1;
      return promptCount === 1
        ? { ok: true, clientMessageId: "first" }
        : { ok: false, reason: "The first message is waiting for the conversation to connect" };
    },
    cancelActive() { return { ok: true }; },
    cancelQueued() { return { ok: true }; },
    newConversation() { return { ok: true }; },
    respondToPermission() { return { ok: true }; },
    setThoughtExpanded() {},
    setToolExpanded() {},
    dispose() { disposeCount += 1; }
  };

  (window as any).__releaseKickoff = () => (deferInitialAttach = false);
  (window as any).__attachCount = () => attachCount;
  (window as any).__promptCount = () => promptCount;
  (window as any).__disposeCount = () => disposeCount;
</script>

<AcpConversationPane
  {controller}
  employeeLabel="Component worker"
  {deferInitialAttach}
/>
`, "utf8");
  await writeFile(mainPath, `
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
mount(Host, { target: document.getElementById("app")! });
`, "utf8");
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`,
    "utf8",
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
      rollupOptions: { input: { index: indexPath } },
    },
  });

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", temporaryDirectory],
    { cwd: repositoryRoot, stdio: "ignore" },
  );
  const builtIndex = (await readdir(temporaryDirectory, { recursive: true }))
    .find((path) => path.endsWith(".html"));
  assert.ok(builtIndex, "the component build must emit an HTML entry");
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);
  const browserScript = `
from playwright.sync_api import sync_playwright
import sys

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="networkidle")
    assert page.evaluate("window.__attachCount()") == 0
    composer = page.locator("[data-chat-input]")
    composer.fill("First prompt")
    page.locator("[data-chat-send]").click()
    page.wait_for_function("document.querySelector('[data-chat-input]').value === ''")
    composer.fill("Second prompt stays")
    page.locator("[data-chat-send]").click()
    page.wait_for_function("document.querySelector('[data-acp-composer-error]') !== null")
    assert composer.input_value() == "Second prompt stays"
    assert page.locator("[data-acp-composer-error]").inner_text() == (
        "The first message is waiting for the conversation to connect"
    )
    assert page.evaluate("window.__promptCount()") == 2
    page.evaluate("window.__releaseKickoff()")
    page.wait_for_function("window.__attachCount() === 1")
    page.evaluate("window.__releaseKickoff()")
    assert page.evaluate("window.__attachCount()") == 1
    browser.close()

print("acp07 component runtime assertions passed")
`;
  const browserProbe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserScript, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "";
  browserProbe.stdout.on("data", (chunk) => { output += chunk; });
  browserProbe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => browserProbe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /acp07 component runtime assertions passed/);
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
  const port = address.port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`component runtime server did not become ready: ${url}`);
}

console.log("acp-components.test.mjs: all assertions passed");
