/**
 * The Atlas panel catches a link, in a real browser.
 *
 * The mapping from an href to a place in the world is proved without a browser in
 * `atlas-navigation.test.ts`. What needs a real DOM is the delegated handler: the
 * anchor lookup from whatever was clicked, and the cancelled navigation. This builds a
 * small Svelte host around the production `AtlasPanel` and serves only static assets.
 * No Panels server or API participates.
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
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-atlas-panel-"));
const hostPath = join(webRoot, "tests", `.atlas-panel-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.atlas-panel-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.atlas-panel-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import AtlasPanel from "../src/components/atlas/AtlasPanel.svelte";
  import type { AtlasSelection } from "../src/lib/atlas/contracts";

  let backOffered = $state(false);

  (window as any).__atlasMoves = [];
  (window as any).__atlasBacks = 0;
  (window as any).__offerBack = () => { backOffered = true; };

  function navigate(selection: AtlasSelection): void {
    (window as any).__atlasMoves.push(selection);
  }
</script>

<main class="fixture-world">
  <AtlasPanel
    open
    onClose={() => {}}
    onNavigate={navigate}
    onBack={backOffered ? () => { (window as any).__atlasBacks += 1; } : null}
  >
    <div class="fixture-screen">
      <a href="#/workspace/t_abc123" data-mapped-link><span data-mapped-inner>A Ticket</span></a>
      <a href="#/sprint?item=si_xyz" data-supervisor-link>Its Item</a>
      <a href="#/preview?source=ticket&amp;ticket=t_abc123" data-unmapped-link>An artifact</a>
    </div>
  </AtlasPanel>
</main>

<style>
  :global(html), :global(body), :global(#app) {
    height: 100%;
    margin: 0;
  }
  .fixture-world {
    position: relative;
    height: 720px;
  }
  .fixture-screen {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 24px;
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

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1000, "height": 760})
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="domcontentloaded")
    start = page.evaluate("location.href")

    # A link to a Ticket moves Atlas, and the reader stays where they are. The click
    # lands on the text inside the anchor, which is what a reader hits.
    page.locator("[data-mapped-inner]").click()
    assert page.evaluate("window.__atlasMoves") == [{"kind": "ticket", "id": "t_abc123"}]
    assert page.evaluate("location.href") == start

    # The supervisor panel's own link is caught by the frame, not by any screen.
    page.locator("[data-supervisor-link]").click()
    assert page.evaluate("window.__atlasMoves")[-1] == {"kind": "item", "id": "si_xyz"}
    assert page.evaluate("location.href") == start

    # An artifact link is no place in the world, so it is left alone and it leaves.
    page.locator("[data-unmapped-link]").click()
    page.wait_for_function("() => location.hash.startsWith('#/preview')")
    assert len(page.evaluate("window.__atlasMoves")) == 2

    # The back line appears only when the route has somewhere to go back to.
    assert page.locator("[data-atlas-back]").count() == 0
    page.evaluate("window.__offerBack()")
    page.locator("[data-atlas-back]").click()
    assert page.evaluate("window.__atlasBacks") == 1

    browser.close()

print("atlas-panel-navigation-browser.test.mjs: all assertions passed")
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
  assert.match(output, /atlas-panel-navigation-browser\.test\.mjs: all assertions passed/);
  console.log("atlas-panel-navigation-browser.test.mjs: all assertions passed");
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
