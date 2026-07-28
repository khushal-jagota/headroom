import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";

const component = await readFile(new URL("../src/components/VpsStatusPopover.svelte", import.meta.url), "utf8");
const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const catalogue = await readFile(new URL("../src/lib/queryCatalogue.ts", import.meta.url), "utf8");

assert.match(component, /fetchJson<VpsStatusSnapshot>\("\/api\/vps-status"\)/);
assert.match(component, /onclick=\{toggle\}/);
assert.match(component, /Refresh/);
assert.match(component, /healthy.*warning.*critical.*unavailable.*review_needed/s);
assert.doesNotMatch(component, /onMount|setInterval|setTimeout|WebSocket|cleanup/);
assert.match(component, /connectionState.*ConnectionStatus/);
assert.match(component, /data-connection-status/);
assert.match(component, /connected: "Connected".*reconnecting: "Reconnecting"/s);
assert.match(component, /aria-label=\{`\$\{connectionLabels\[connectionState\]\}\. Show VPS status`\}/);
assert.match(app, /<VpsStatusPopover connectionState=\{\$connectionStatus\} \/>/);
assert.doesNotMatch(app, /class="shell-connection"|connectionLabels/);
assert.doesNotMatch(catalogue, /vps-status/);
assert.doesNotMatch(app, /#\/status|name === "status"/);

// Render the focused component without Panels' backend. This is the interaction the
// old e2e test proved, but its data is route-mocked and therefore does not need a full
// server boot: opening fetches once, each explicit refresh fetches once, and every state
// is presented by its human label.
const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const outputDirectory = await mkdtemp(join(tmpdir(), "panels-vps-status-"));
const hostPath = join(webRoot, "tests", `.vps-status-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.vps-status-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.vps-status-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(hostPath, `
<script lang="ts">
  import VpsStatusPopover from "../src/components/VpsStatusPopover.svelte";
  const states = ["healthy", "warning", "unavailable", "review_needed"] as const;
  let requestCount = 0;
  function payload(state: typeof states[number]) {
    const section = { state, summary: state + " evidence" };
    return {
      collected_at: "2026-07-24T00:00:00+00:00",
      overall_state: state,
      environment: section,
      app: section,
      backup: section,
      disk: section,
      workloads: { ...section, items: [] },
      worktrees: { ...section, items: [] },
      logs: { ...section, file_count: 0, total_bytes: 0 },
      cleanup_candidates: { ...section, candidates: [] },
      resources: {
        ...section, cpu_percent: null, load_averages: null, ram: null, swap: null
      }
    };
  }
  globalThis.fetch = (async () => {
    const state = states[Math.min(requestCount, states.length - 1)];
    requestCount += 1;
    return new Response(JSON.stringify(payload(state)), {
      status: 200, headers: { "Content-Type": "application/json" }
    });
  }) as typeof fetch;
  (window as any).__statusRequestCount = () => requestCount;
</script>
<VpsStatusPopover connectionState="connected" />
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
      outDir: outputDirectory,
      rollupOptions: { input: { index: indexPath } },
    },
  });

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", outputDirectory],
    { cwd: repositoryRoot, stdio: "ignore" },
  );
  const builtIndex = (await readdir(outputDirectory, { recursive: true }))
    .find((path) => path.endsWith(".html"));
  assert.ok(builtIndex);
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
import sys

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(sys.argv[1], wait_until="networkidle")
    assert page.evaluate("window.__statusRequestCount()") == 0
    trigger = page.locator("[data-vps-status] button").first
    assert trigger.get_attribute("aria-label") == "Connected. Show VPS status"
    trigger.click()
    page.wait_for_function("window.__statusRequestCount() === 1")
    page.locator("[data-vps-status-content]").wait_for()
    assert "Healthy" in page.locator("[data-vps-status-content]").inner_text()
    refresh = page.get_by_role("button", name="Refresh")
    for count, label in [(2, "Warning"), (3, "Unavailable"), (4, "Review needed")]:
        refresh.click()
        page.wait_for_function(
            "([count, label]) => window.__statusRequestCount() === count"
            " && document.querySelector('[data-vps-status-content]')?.textContent.includes(label)",
            arg=[count, label],
        )
    browser.close()

print("vps status component assertions passed")
`;
  const probe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserScript, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "";
  probe.stdout.on("data", (chunk) => { output += chunk; });
  probe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => probe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /vps status component assertions passed/);
} finally {
  serverProcess?.kill();
  await rm(hostPath, { force: true });
  await rm(mainPath, { force: true });
  await rm(indexPath, { force: true });
  await rm(outputDirectory, { recursive: true, force: true });
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

console.log("vps-status.test.mjs: all assertions passed");
