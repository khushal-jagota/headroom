import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";

const component = await readFile(new URL("../src/components/VpsStatusPopover.svelte", import.meta.url), "utf8");
const app = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const catalogue = await readFile(new URL("../src/lib/queryCatalogue.ts", import.meta.url), "utf8");

assert.match(component, /queries\.deploymentStatus\(\)/);
assert.match(component, /queries\.vpsStatusSummary\(\)/);
assert.match(component, /enabled: isOpen/);
assert.match(component, /data-status-state=\{statusState\}/);
assert.match(component, /data-connection-state=\{connectionState\}/);
assert.match(component, /Deployed commit/);
assert.match(component, /Latest verified backup/);
assert.doesNotMatch(component, /Environment|Worktree|Logs|Cleanup|overall_state|collected_at/);
assert.match(app, /<VpsStatusPopover connectionState=\{\$connectionStatus\} \/>/);
assert.match(catalogue, /"\/api\/deployment-status"/);
assert.match(catalogue, /"\/api\/vps-status-summary"/);
assert.doesNotMatch(app, /#\/status|name === "status"/);

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const outputDirectory = await mkdtemp(join(tmpdir(), "panels-vps-status-"));
const hostPath = join(webRoot, "tests", `.vps-status-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.vps-status-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.vps-status-index-${process.pid}.html`);
const screenshotPath = join(
  repositoryRoot,
  "data",
  "test-artifacts",
  "t_na231g5b-vps-status.png"
);
let serverProcess;

try {
  await writeFile(hostPath, `
<script lang="ts">
  import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import VpsStatusPopover from "../src/components/VpsStatusPopover.svelte";

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const states = ["healthy", "warning", "unavailable", "critical"] as const;
  let summaryRequestCount = 0;
  let connectionState = $state<"connected" | "reconnecting">("connected");
  let deploymentPayload: any = {
    state: "idle", deployed_sha: "0123456789abcdef", target_sha: null,
    outcome: "succeeded", detail: null, valid_until: null
  };

  function metric(state: typeof states[number], value: number | null) {
    return { used_percent: value, state, unavailable_reason: value === null ? "probe failed" : null };
  }

  function summary(state: typeof states[number]) {
    const value = state === "unavailable" ? null : 71.6;
    return {
      deployed_sha: state === "unavailable" ? null : "0123456789abcdef",
      deployment: {
        outcome: state === "critical" ? "rolled_back" : "succeeded",
        detail: state === "critical" ? "health check failed" : null
      },
      cpu: metric(state, value),
      ram: metric(state, value),
      disk: metric(state, value),
      backup: {
        age_seconds: value === null ? null : 3660,
        state,
        unavailable_reason: value === null ? "no verified backup" : null
      }
    };
  }

  globalThis.fetch = (async (input) => {
    const path = String(input);
    const payload = path.includes("deployment-status")
      ? deploymentPayload
      : summary(states[Math.min(summaryRequestCount++, states.length - 1)]);
    return new Response(JSON.stringify(payload), {
      status: 200, headers: { "Content-Type": "application/json" }
    });
  }) as typeof fetch;
  (window as any).__summaryRequestCount = () => summaryRequestCount;
  (window as any).__setDeployment = async (next: typeof deploymentPayload) => {
    deploymentPayload = next;
    await client.invalidateQueries({ queryKey: ["deployment-status"] });
  };
  (window as any).__setConnection = (next: "connected" | "reconnecting") => {
    connectionState = next;
  };
  (window as any).__invalidateAll = () => client.invalidateQueries();
</script>
<QueryClientProvider client={client}>
  <VpsStatusPopover {connectionState} />
</QueryClientProvider>
`, "utf8");
  await writeFile(mainPath, `
import { mount } from "svelte";
import "../../assets/tokens.css";
import "../../assets/app.css";
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
  await mkdir(dirname(screenshotPath), { recursive: true });

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
import sys

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 720})
    page.goto(sys.argv[1], wait_until="networkidle")
    assert page.evaluate("window.__summaryRequestCount()") == 0
    trigger = page.locator("[data-connection-status]")
    trigger.wait_for()
    assert trigger.get_attribute("data-status-state") == "connected"
    assert trigger.get_attribute("data-connection-state") == "connected"

    def set_deployment(state, *, outcome=None, valid_for_ms=None):
        valid_until = None
        if valid_for_ms is not None:
            valid_until = page.evaluate(
                "(delay) => new Date(Date.now() + delay).toISOString()", valid_for_ms
            )
        page.evaluate(
            """async ([state, outcome, validUntil]) => {
                await window.__setDeployment({
                    state,
                    deployed_sha: "0123456789abcdef",
                    target_sha: state === "idle" ? null : "fedcba9876543210",
                    outcome,
                    detail: state === "problem" ? "health check failed" : null,
                    valid_until: validUntil
                });
            }""",
            [state, outcome, valid_until],
        )

    for state, label in [
        ("preparing", "Preparing"),
        ("restarting", "Restarting"),
        ("back_up", "Back up"),
        ("problem", "Problem"),
    ]:
        set_deployment(state, valid_for_ms=2000 if state != "problem" else None)
        page.locator(f'[data-status-state="{state}"]', has_text=label).wait_for()

    # A persistent failure wins even when the transport drops.
    page.evaluate("window.__setConnection('reconnecting')")
    assert trigger.get_attribute("data-status-state") == "problem"
    set_deployment("idle", outcome="succeeded")
    page.locator('[data-status-state="reconnecting"]', has_text="Reconnecting").wait_for()

    # Planned and Back up phases re-evaluate from valid_until without another response.
    page.evaluate("window.__setConnection('connected')")
    # Let the component's initial render clock become meaningfully old. The timeout for
    # the new response must still be based on Date.now(), not that render-time value.
    page.wait_for_timeout(800)
    set_deployment("preparing", valid_for_ms=250)
    page.locator('[data-status-state="preparing"]').wait_for()
    page.locator('[data-status-state="reconnecting"]').wait_for(timeout=700)
    set_deployment("back_up", valid_for_ms=250)
    page.locator('[data-status-state="back_up"]').wait_for()
    page.locator('[data-status-state="connected"]').wait_for(timeout=3000)

    trigger.click()
    page.wait_for_function("window.__summaryRequestCount() === 1")
    content = page.locator("[data-vps-status-content]")
    content.wait_for()
    assert "01234567" in content.inner_text()
    assert "72% · Healthy" in content.inner_text()
    assert "1h ago · Healthy" in content.inner_text()
    assert "Environment" not in content.inner_text()
    refresh = page.get_by_role("button", name="Refresh")
    refresh.click()
    page.wait_for_function(
        "() => window.__summaryRequestCount() === 2"
        " && document.querySelector('[data-vps-status-content]')?.textContent.includes('Warning')"
    )
    assert "Warning" in content.inner_text()
    refresh.click()
    page.wait_for_function(
        "() => window.__summaryRequestCount() === 3"
        " && document.querySelector('[data-vps-status-content]')?.textContent.includes('Unavailable')"
    )
    assert content.inner_text().count("Unavailable") == 5
    refresh.click()
    page.wait_for_function(
        "() => window.__summaryRequestCount() === 4"
        " && document.querySelector('[data-vps-status-content]')?.textContent.includes('Rolled back')"
    )
    popover = page.locator("[data-vps-status] section")
    box = popover.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, box
    set_deployment("problem", outcome="rolled_back")
    page.locator('[data-status-state="problem"]', has_text="Problem").wait_for()
    page.screenshot(path=sys.argv[2], full_page=True)
    trigger.click()
    page.evaluate("window.__invalidateAll()")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__summaryRequestCount()") == 4
    browser.close()

print("vps status component assertions passed")
`;
  const probe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserScript, url, screenshotPath],
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
