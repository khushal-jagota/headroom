import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";
import { scratchDirectory } from "./support/scratch.mjs";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const outputDirectory = await mkdtemp(join(tmpdir(), "panels-sprint-documents-"));
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `sprint-documents-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `sprint-documents-${process.pid}.ts`);
const indexPath = join(scratchRoot, `sprint-documents-${process.pid}.html`);
let server;
try {
  await writeFile(hostPath, `
<script lang="ts">
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { queryClient } from "../src/lib/queryClient";
  import SprintRoute from "../src/routes/SprintRoute.svelte";
  let sub = $state("tracking");
  let selectedItemId = $state<string | null>(null);
  const sprint = {
    id: "sp_test", name: "A useful historical sprint", date_start: "2026-09-01", date_end: "2026-09-07",
    primary_bet: "Original **summary**", kickoff: "Original plan. [Proof](/files/tickets/t_history/artifacts/plan.md)",
    checkpoint: "Original checkpoint", review: "Original review", created_at: 1, updated_at: 1
  };
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  const outcome = { id: "outcome_existing", title: "Ship it", deadline: null, project_id: "project_one", project: "One", created_at: 1, updated_at: 1 };
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json" }
  });
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    const method = init.method || "GET";
    requests.push({ path, method, body: init.body ? JSON.parse(String(init.body)) : null });
    if (path === "/files/tickets/t_history/artifacts/plan.md") return new Response("Historical artifact proof", { headers:{ "Content-Type":"text/markdown" } });
    if (path === "/api/sprints/sp_test/tracking") return json({
      sprint, planning_date: "2026-09-08", outcome_groups: [{ outcome, committed: true, tickets: [
        { id: "t_one", title: "Move me", stage: "needs_plan", ticket_status: "empty", project_id: "project_one", sprint_item_id: outcome.id, waiting_to_closeout: false },
        { id: "t_done", title: "Leave done", stage: "done", ticket_status: "empty", project_id: "project_one", sprint_item_id: outcome.id, waiting_to_closeout: false }
      ] }], unclassified_tickets: [
        { id: "t_loose_one", title: "Loose from One", stage: "needs_plan", ticket_status: "empty", project_id: "project_one", sprint_item_id: null, waiting_to_closeout: false },
        { id: "t_loose_two", title: "Loose from Two", stage: "done", ticket_status: "empty", project_id: "project_two", sprint_item_id: null, waiting_to_closeout: false }
      ]
    });
    // A retired live calendar and absent current catalogue must never hide this record.
    // Any accidental calendar request receives a real HTTP failure, not an empty fixture.
    if (["/api/day/today", "/api/sprint/current", "/api/projects?detail=full"].includes(path)) {
      return json({ error:{ code:"not_found", message:"No live calendar/catalogue record" } },404);
    }
    throw new Error("Unexpected historical Sprint request: " + method + " " + path);
  }) as typeof fetch;
  (window as any).saved = () => ({ sprint, requests });
  (window as any).showLegacyItem = () => { selectedItemId = outcome.id; };
</script>
<button onclick={() => sub = sub === "documents" ? "tracking" : "documents"}>Switch view</button>
<QueryClientProvider client={queryClient}><SprintRoute {sub} {selectedItemId} sprintId="sp_test" /></QueryClientProvider>
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
        # The explicit historical record renders despite unavailable Today, current
        # Sprint and project catalogue. Its stored labels are enough to read tracking.
        expect(page.get_by_role("heading", name="A useful historical sprint")).to_be_visible()
        expect(page.locator('[data-sprint-bet]')).to_have_text("Original summary")
        assert page.locator("a.sprint-docs-link").get_attribute("href") == "#/sprint/documents?sprint=sp_test"
        assert page.locator('.sprint-meta-line .sep').count() == 0
        outcome = page.locator('[data-outcome-id="outcome_existing"]')
        expect(outcome.locator('.sprint-item-rollup')).to_have_text("1/2")
        assert outcome.locator('a').get_attribute('href') == '#/workspace/item/outcome_existing'
        expect(page.locator('[data-sprint-project="project_one"] .board-workspace-bucket-label')).to_have_text("One")
        no_outcome = page.locator('[data-sprint-no-outcome]')
        expect(no_outcome.locator(':scope > summary .sprint-item-rollup')).to_have_text("1/2")
        assert no_outcome.get_attribute('open') is None
        no_outcome.locator(':scope > summary').click()
        assert no_outcome.locator('[data-sprint-ticket-id]:visible').count() == 2
        assert no_outcome.locator('[data-sprint-ticket-id="t_loose_one"]').get_attribute('href') == '#/workspace/t_loose_one'
        assert no_outcome.locator('[data-sprint-ticket-id="t_loose_two"]').get_attribute('href') == '#/workspace/t_loose_two'
        assert page.get_by_role('button', name='Add outcome').count() == 0
        assert page.locator('[contenteditable], [data-outcome-picker]').count() == 0
        for width in (1280, 390):
            page.set_viewport_size({"width":width,"height":900})
            assert page.evaluate("() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= innerWidth + 1")

        page.get_by_role("button", name="Switch view").click()
        expect(page.get_by_role("heading", name="Sprint documents")).to_be_visible()
        assert page.locator("a.sprint-back").get_attribute("href") == "#/sprint?sprint=sp_test"
        assert page.locator('[data-field]').count() == 4
        for field, text in [("primary_bet","Original summary"),("kickoff","Original plan."),("checkpoint","Original checkpoint"),("review","Original review")]:
            disclosure = page.locator(f'[data-phase="{field}"]')
            if disclosure.count() and disclosure.get_attribute('open') is None:
                disclosure.locator(':scope > summary').click()
            expect(page.locator(f'[data-field="{field}"]')).to_contain_text(text)
        proof = page.get_by_role('link', name='Open plan.md', exact=True)
        expect(proof).to_be_visible()
        assert proof.get_attribute('href').startswith('#/preview?')
        assert 'plan.md' in proof.get_attribute('href')
        expect(page.locator('[data-field="kickoff"]')).to_contain_text('Historical artifact proof')
        assert page.locator('[contenteditable], input, textarea').count() == 0
        assert page.get_by_role('textbox').count() == 0
        # Legacy historical Item address offers exact current navigation, without
        # embedding an editable workspace or asking for a current Item payload.
        page.evaluate('window.showLegacyItem()')
        expect(page.locator('[data-historical-orchestrator]')).to_have_attribute('href', '#/workspace/item/outcome_existing')
        assert page.locator('[contenteditable], input, textarea').count() == 0
        saved = page.evaluate('window.saved()')
        assert saved['sprint']['primary_bet'] == 'Original **summary**'
        assert all(request['method'] == 'GET' for request in saved['requests']), saved['requests']
        paths = [request['path'] for request in saved['requests']]
        assert '/api/sprints/sp_test/tracking' in paths
        assert '/api/day/today' not in paths and '/api/sprint/current' not in paths, paths
        assert not any('/collections/' in path for path in paths)
    finally:
        browser.close()
print("historical sprint tracking and read-only documents survive missing live calendar/catalogue")
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
