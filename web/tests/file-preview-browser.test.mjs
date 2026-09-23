/**
 * Two things only a real browser can show, around the production FilePreview.
 *
 * One: a missing artifact's image fails with a genuine `error` event from the browser's
 * own image loader, and the failure line replaces the broken image rather than leaving
 * nothing visible.
 *
 * Two: three previews naming one file make one request. The sharing is a race between
 * three components mounting in the same frame, which is the thing a stubbed fetch in a
 * unit test cannot put under real timing.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";
import { scratchDirectory } from "./support/scratch.mjs";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-file-preview-"));
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `file-preview-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `file-preview-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `file-preview-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import FilePreview from "../src/components/FilePreview.svelte";

  const sharedNote = { kind: "ticket-file", ticketId: "t_probe01", path: "note.md" } as const;
</script>

<div data-probe="missing">
  <FilePreview
    target={{ kind: "external-link", href: "./missing-artifact.png", label: "Missing" }}
  />
</div>

<div data-probe="shared">
  <FilePreview target={sharedNote} />
  <FilePreview target={sharedNote} />
  <FilePreview target={sharedNote} />
</div>
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

  // The three shared previews resolve to this address. It is written after the build,
  // because the build empties the directory it writes into.
  await mkdir(join(temporaryDirectory, "files", "tickets", "t_probe01"), { recursive: true });
  await writeFile(
    join(temporaryDirectory, "files", "tickets", "t_probe01", "note.md"),
    "# The shared note\n\nRead once, drawn three times.\n",
    "utf8"
  );

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
    page = browser.new_page(viewport={"width": 600, "height": 400})
    page.set_default_timeout(5_000)

    reads = []
    page.on("request", lambda request: reads.append(request.url)
            if "/files/tickets/t_probe01/note.md" in request.url else None)
    page.goto(sys.argv[1], wait_until="domcontentloaded")

    page.locator("[data-file-preview]").first.wait_for()
    page.wait_for_function("""() => {
      const line = document.querySelector('[data-probe="missing"] .quiet-line');
      return line !== null && line.textContent.trim().length > 0;
    }""")
    assert page.locator('[data-probe="missing"] .quiet-line').inner_text() == "file fetch failed"
    assert page.locator('[data-probe="missing"] img').count() == 0

    # All three draw the note.
    page.wait_for_function("""() => {
      const bodies = document.querySelectorAll('[data-probe="shared"] .file-preview-document-body');
      return bodies.length === 3
        && [...bodies].every((body) => body.textContent.includes("The shared note"));
    }""")
    page.wait_for_timeout(500)
    assert len(reads) == 1, f"three previews of one file made {len(reads)} requests: {reads}"

    browser.close()

print("file-preview-browser.test.mjs: all assertions passed")
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
  assert.match(output, /file-preview-browser\.test\.mjs: all assertions passed/);
  console.log("file-preview-browser.test.mjs: all assertions passed");
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
