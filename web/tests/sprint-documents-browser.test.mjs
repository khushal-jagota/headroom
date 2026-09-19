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
  const requests: Array<{ path: string; method: string; body: any }> = [];
  let commitmentAttempts = 0;
  const outcome = { id: "outcome_existing", title: "Ship it", priority: "P1", deadline: null, project_id: "project_one", project: "One", created_at: 1, updated_at: 1 };
  const json = (value: unknown) => new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" }
  });
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    requests.push({ path, method, body });
    if (path === "/api/sprints/sp_test" && init.method === "PATCH") {
      writes.push(body);
      sprint = { ...sprint, ...body };
      return json(sprint);
    }
    if (path === "/api/sprints/sp_test/tracking") return json({
      sprint, planning_date: "2026-09-08", outcome_groups: [{ outcome, committed: true, tickets: [
        { id: "t_one", title: "Move me", stage: "needs_plan", priority: "P1", ticket_status: "empty", project_id: "project_one", sprint_item_id: outcome.id, waiting_to_closeout: false },
        { id: "t_dropped", title: "Abandoned work", stage: "dropped", priority: "P2", ticket_status: "empty", project_id: "project_one", sprint_item_id: outcome.id, waiting_to_closeout: false },
        { id: "t_done", title: "Leave done", stage: "done", priority: "P2", ticket_status: "empty", project_id: "project_one", sprint_item_id: outcome.id, waiting_to_closeout: false }
      ] }], unclassified_tickets: [
        { id: "t_loose_one", title: "Loose from One", stage: "needs_plan", priority: "P1", ticket_status: "empty", project_id: "project_one", sprint_item_id: null, waiting_to_closeout: false },
        { id: "t_loose_two", title: "Loose from Two", stage: "done", priority: "P2", ticket_status: "empty", project_id: "project_two", sprint_item_id: null, waiting_to_closeout: false },
        { id: "t_loose_dropped", title: "Dropped loose work", stage: "dropped", priority: "P2", ticket_status: "empty", project_id: "project_two", sprint_item_id: null, waiting_to_closeout: false }
      ]
    });
    if (path === "/api/projects") return json({ projects: [{ id: "project_one", name: "One", summary: "", priority: "P1", created_at: 1, updated_at: 1 }, { id: "project_two", name: "Two", summary: "", priority: "P2", created_at: 1, updated_at: 1 }] });
    if (path === "/api/day/today") return json({ tickets: [] });
    if (path === "/api/sprints") return json({ sprints: [sprint, { ...sprint, id: "sp_next", name: "Next" }] });
    if (path.startsWith("/api/sprint-item-summaries?")) return json({ items: [outcome], page: { match_count: 1, return_count: 1, limit: 30, offset: 0, omitted_before: 0, omitted_after: 0, complete: true, next_offset: null } });
    if (path === "/api/items" && method === "POST") return json({ ...outcome, id: "outcome_created", title: body.title });
    if (path.includes("/collections/sprint_outcomes/") && path.endsWith("/outcome_created") && method === "PUT") { commitmentAttempts += 1; if (commitmentAttempts === 1) return new Response(JSON.stringify({ error: { code: "failed", message: "Try again" } }), { status: 500, headers: { "Content-Type": "application/json" } }); return json({ sprint_id: "sp_test", outcome_id: "outcome_created" }); }
    if (path.endsWith("/outcomes/outcome_existing/carry") && method === "POST") return json(body);
    throw new Error("Unexpected request: " + path);
  }) as typeof fetch;
  (window as any).saved = () => ({ sprint, writes, requests });
</script>
<button onclick={() => sub = sub === "documents" ? "tracking" : "documents"}>Switch view</button>
<QueryClientProvider client={queryClient}><SprintRoute {sub} sprintId="sp_test" /></QueryClientProvider>
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
        assert page.locator("a.sprint-back").get_attribute("href") == "#/sprint?sprint=sp_test"
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
        assert page.locator("a.sprint-docs-link").get_attribute("href") == "#/sprint/documents?sprint=sp_test"
        assert page.locator('.sprint-meta-line .sep').count() == 0
        assert "committed outcomes" not in page.locator('.sprint-meta-line').inner_text()
        outcome = page.locator('[data-outcome-id="outcome_existing"]')
        expect(outcome.locator('.sprint-item-rollup')).to_have_text("1/2")
        assert outcome.locator('[data-sprint-ticket-id]').count() == 0
        assert page.locator('.sprint-outcome-meta, .sprint-outcome-menu, .sprint-outcome-tickets, .board-workspace-bucket-count').count() == 0
        assert "Committed outcome" not in page.locator('[data-sprint-projects]').inner_text()
        assert "Other work" not in page.locator('[data-sprint-projects]').inner_text()
        no_outcome = page.locator('[data-sprint-no-outcome]')
        expect(no_outcome.locator(':scope > summary')).to_contain_text("No Outcome")
        expect(no_outcome.locator(':scope > summary .sprint-item-rollup')).to_have_text("1/2")
        assert no_outcome.get_attribute('open') is None
        assert no_outcome.locator('[data-sprint-ticket-id]:visible').count() == 0
        no_outcome.locator(':scope > summary').click()
        assert no_outcome.locator('[data-sprint-ticket-id]:visible').count() == 2
        assert no_outcome.locator('[data-sprint-ticket-id="t_loose_dropped"]').count() == 0
        assert no_outcome.locator('[data-sprint-ticket-id="t_loose_one"]').get_attribute('href') == '#/workspace/t_loose_one'
        assert no_outcome.locator('[data-sprint-ticket-id="t_loose_two"]').get_attribute('href') == '#/workspace/t_loose_two'
        assert page.evaluate("""() => Boolean(document.querySelector('[data-sprint-no-outcome]').compareDocumentPosition(document.querySelector('.sprint-outcome-actions')) & Node.DOCUMENT_POSITION_FOLLOWING)""")
        def assert_sprint_layout(width):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(50)
            assert page.evaluate("""() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1""")
            no_outcome_box = no_outcome.bounding_box()
            add_outcome_box = page.locator('.sprint-outcome-actions').bounding_box()
            assert no_outcome_box and add_outcome_box
            assert no_outcome_box['y'] + no_outcome_box['height'] <= add_outcome_box['y'] + 1
        assert_sprint_layout(1280)
        assert_sprint_layout(390)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.get_by_role("button", name="Add outcome").click()
        picker = page.locator('[data-outcome-picker]')
        picker.locator('[data-new-outcome] > summary').click()
        picker.locator('[data-new-outcome] input').fill("Created once")
        picker.locator('[data-new-outcome] textarea').fill("Keep this brief")
        picker.locator('[data-new-outcome] select').select_option("project_one")
        picker.get_by_role("button", name="Create and add").click()
        picker.locator('[data-retry-created-outcome]').wait_for()
        post_count = page.evaluate("window.saved().requests.filter(request => request.path === '/api/items' && request.method === 'POST').length")
        assert post_count == 1
        picker.locator('[data-retry-created-outcome]').click()
        page.wait_for_function("() => window.saved().requests.filter(request => request.path.includes('/collections/sprint_outcomes/') && request.method === 'PUT').length === 2")
        assert page.evaluate("window.saved().requests.filter(request => request.path === '/api/items' && request.method === 'POST').length") == 1
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
