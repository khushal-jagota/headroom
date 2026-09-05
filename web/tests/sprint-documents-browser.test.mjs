import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const outputDirectory = await mkdtemp(join(tmpdir(), "panels-sprint-documents-"));
const hostPath = join(webRoot, "tests", `.sprint-documents-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.sprint-documents-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.sprint-documents-${process.pid}.html`);
let server;
try {
  await writeFile(hostPath, `
<script lang="ts">
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { queryClient } from "../src/lib/queryClient";
  import SprintRoute from "../src/routes/SprintRoute.svelte";
  let sub = $state("documents");
  let sprint = {
    id: "sp_test", name: "A useful sprint", date_start: "2026-09-01", date_end: "2026-09-07",
    primary_bet: "Original summary", kickoff: "Original plan", checkpoint: "", review: "",
    created_at: 1, updated_at: 1
  };
  const writes: object[] = [];
  const json = (value: unknown) => new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" }
  });
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    if (path === "/api/sprints/sp_test" && init.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      writes.push(body);
      sprint = { ...sprint, ...body };
      return json(sprint);
    }
    if (path === "/api/sprint/current") return json({
      sprint, planning_date: "2026-09-05", groups: {}, other_tickets: []
    });
    if (path === "/api/projects") return json({ projects: [] });
    if (path === "/api/day/today") return json({ tickets: [] });
    throw new Error("Unexpected request: " + path);
  }) as typeof fetch;
  (window as any).saved = () => ({ sprint, writes });
</script>
<button onclick={() => sub = sub === "documents" ? "tracking" : "documents"}>Switch view</button>
<QueryClientProvider client={queryClient}><SprintRoute {sub} /></QueryClientProvider>
`);
  await writeFile(mainPath, `
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
import "../../assets/tokens.css";
import "../../assets/app.css";
mount(Host, { target: document.getElementById("app")! });
`);
  await writeFile(indexPath, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`);
  await build({
    root: webRoot, base: "./", configFile: false, logLevel: "silent", plugins: [svelte()],
    build: { outDir: outputDirectory, emptyOutDir: true, rollupOptions: { input: indexPath } }
  });
  const builtIndex = (await readdir(outputDirectory, { recursive: true })).find(path => path.endsWith(".html"));
  assert.ok(builtIndex);
  // Port zero binds an available port atomically; no Panels server or subprocess to clean up.
  server = createServer(async (request, response) => {
    try {
      const path = new URL(request.url, "http://localhost").pathname;
      const body = await readFile(join(outputDirectory, path));
      const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2" };
      response.writeHead(200, { "Content-Type": types[extname(path)] || "application/octet-stream" });
      response.end(body);
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  const browserScript = String.raw`
import sys
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.goto(sys.argv[1], wait_until="networkidle")
        page.get_by_role("heading", name="Sprint documents").wait_for()
        assert page.locator('[data-field]').count() == 4
        for field, label, text in [
            ("primary_bet", "Primary bet", "Updated summary."),
            ("kickoff", "Kickoff", "Updated kickoff."),
            ("checkpoint", "Checkpoint", "Updated checkpoint."),
            ("review", "Sprint Review", "Updated review."),
        ]:
            disclosure = page.locator(f'[data-phase="{field}"]')
            if disclosure.count() and disclosure.get_attribute("open") is None:
                disclosure.locator(":scope > summary").click()
            editor = page.get_by_role("textbox", name=label, exact=True)
            editor.fill(text)
            page.get_by_role("heading", name="Sprint documents").click()
            page.wait_for_function("([field, text]) => window.saved().sprint[field].trim() === text", arg=[field, text])
            expect(editor).to_have_text(text)
        saved = page.evaluate("window.saved()")
        assert [list(body) for body in saved["writes"]] == [[field] for field in ["primary_bet", "kickoff", "checkpoint", "review"]]
        assert saved["sprint"]["name"] == "A useful sprint"
        page.get_by_role("button", name="Switch view").click()
        expect(page.locator("[data-sprint-bet]")).to_have_text("Updated summary.")
        page.get_by_role("button", name="Switch view").click()
        # Remount reads the persisted document through the real query path.
        expect(page.get_by_role("textbox", name="Sprint Review", exact=True)).to_have_text("Updated review.")
    finally:
        browser.close()
print("sprint document editing and summary readback passed")
`;
  const probe = spawn(join(webRoot, "..", ".venv", "bin", "python"), ["-c", browserScript, `http://127.0.0.1:${port}/${builtIndex}`], { stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  probe.stdout.on("data", chunk => { output += chunk; });
  probe.stderr.on("data", chunk => { output += chunk; });
  const exitCode = await new Promise((resolve, reject) => {
    probe.once("error", reject);
    probe.once("close", resolve);
  });
  assert.equal(exitCode, 0, output);
  console.log(output.trim());
} finally {
  if (server) {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
  for (const path of [hostPath, mainPath, indexPath]) await rm(path, { force: true });
  await rm(outputDirectory, { recursive: true, force: true });
}
