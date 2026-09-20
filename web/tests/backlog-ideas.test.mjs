import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const outputDirectory = await mkdtemp(join(tmpdir(), "panels-backlog-ideas-"));
const hostPath = join(webRoot, "tests", `.backlog-ideas-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.backlog-ideas-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.backlog-ideas-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(hostPath, `
<script lang="ts">
  import AppWithQueryClient from "../src/AppWithQueryClient.svelte";

  const ticket = (id: string, priority = "P3") => ({
    id,
    title: "Ticket " + id,
    worker_type: "coding",
    stage: "needs_plan",
    ticket_status: "empty",
    priority,
    project_id: "project_vylo",
    project: "Vylo",
    sprint_item_id: null,
    sprint_item: null,
    sprint_id: null,
    effective_sprint_id: null,
    recap_preview: "A bounded recap for " + id
  });
  const item = (id: string) => ({
    id,
    title: "Brief " + id,
    status: "active",
    priority: "P2",
    deadline: null,
    project_id: "project_tribe",
    project: "Tribe",
    sprint_id: null
  });
  let backlogTickets = Array.from({ length: 30 }, (_, index) =>
    ticket("ticket_" + index, index === 0 ? "P0" : "P3")
  );
  const firstItemPage = Array.from({ length: 30 }, (_, index) => item("item_" + index));
  const finalItem = item("item_offboard");
  let ideas: any[] = [];
  const projects = [
    { id: "project_vylo", name: "Vylo" },
    { id: "project_tribe", name: "Tribe" }
  ];
  const requests: Array<{ path: string; method: string; body: any }> = [];

  class QuietEventSource {
    onopen: null | (() => void) = null;
    onmessage: null | (() => void) = null;
    onerror: null | (() => void) = null;
    constructor(_url: string) {}
    close() {}
  }
  (globalThis as any).EventSource = QuietEventSource;

  function json(value: unknown): Response {
    return new Response(JSON.stringify(value), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  }
  function page(returnCount: number, matchCount: number, offset: number) {
    const omittedBefore = Math.min(offset, matchCount);
    const omittedAfter = Math.max(matchCount - omittedBefore - returnCount, 0);
    return {
      match_count: matchCount,
      return_count: returnCount,
      limit: 30,
      offset,
      omitted_before: omittedBefore,
      omitted_after: omittedAfter,
      complete: omittedBefore === 0 && omittedAfter === 0,
      next_offset: omittedAfter === 0 ? null : omittedBefore + returnCount
    };
  }
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    requests.push({ path, method, body });
    if (path === "/api/review") {
      return json({ running_worker_count: 0, awaiting_approval_count: 0 });
    }
    if (path === "/api/projects") return json({ projects });
    if (path === "/api/worker-types") {
      return json({
        worker_types: [
          { worker_type: "coding", label: "Coding" },
          { worker_type: "exploration", label: "Exploration" }
        ]
      });
    }
    if (path === "/api/ticket-summaries?sprint_id=null&limit=30&offset=0") {
      return json({
        tickets: backlogTickets,
        page: page(backlogTickets.length, backlogTickets.length + 1, 0)
      });
    }
    if (path === "/api/ticket-summaries?sprint_id=null&limit=30&offset=30") {
      return json({ tickets: [ticket("ticket_last", "P1")], page: page(1, backlogTickets.length + 1, 30) });
    }
    if (path === "/api/sprint-item-summaries?limit=30&offset=0") {
      return json({ items: firstItemPage, page: page(30, 31, 0) });
    }
    if (path === "/api/sprint-item-summaries?limit=30&offset=30") {
      return json({ items: [finalItem], page: page(1, 31, 30) });
    }
    if (path === "/api/tickets" && method === "POST") {
      const project = projects.find((entry) => entry.id === body.project_id);
      const created = {
        ...ticket("ticket_created", body.priority),
        title: body.title,
        project_id: body.project_id,
        project: project?.name ?? null
      };
      backlogTickets = [created, ...backlogTickets].slice(0, 30);
      return json(created);
    }
    if (path === "/api/board") {
      return json({ columns: [], sprint_items: [] });
    }
    if (path === "/api/workers") {
      return json({
        workers: [],
        chief_of_staff: {
          label: "Chief of Staff",
          conversation_id: null,
          needs_me: false,
          agent_working: false,
          latest_turn_ended_sequence: 0
        }
      });
    }
    if (path === "/api/items/item_offboard/workspace") {
      return json({
        ...finalItem,
        kind: "normal",
        body: "The off-board brief remains readable.",
        committed_sprints: [],
        supervisor: {
          agent_key: "sprint_item:item_offboard",
          conversation_id: null,
          launch_configuration: {
            employee_backend: "codex",
            employee_launch_model: "gpt-5",
            employee_launch_reasoning_effort: null
          }
        },
        planning_day_id: "2026-09-05",
        today_ticket_ids: [],
        tickets: [],
        artifacts: [],
        conversation_history: []
      });
    }
    if (path === "/api/items/item_offboard/supervisor/conversation/start-values") {
      return json({ backend_key: "codex", model: "gpt-5", reasoning_effort: null });
    }
    if (path === "/api/conversation/backends") {
      return json({ backends: [] });
    }
    if (path === "/api/ideas" && method === "GET") return json({ ideas });
    if (path === "/api/ideas" && method === "POST") {
      const project = projects.find((entry) => entry.id === body.project_id);
      const idea = {
        id: "idea_" + (ideas.length + 1),
        title: body.title,
        body: body.body ?? null,
        project_id: body.project_id ?? null,
        project: project?.name ?? null
      };
      ideas = [idea, ...ideas];
      return json(idea);
    }
    return new Response(JSON.stringify({ error: { code: "unexpected", message: path } }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
  }) as typeof fetch;
  (window as any).__requests = () => requests;
</script>
<AppWithQueryClient />
`, "utf8");
  await writeFile(mainPath, `
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
import "../../assets/tokens.css";
import "../../assets/app.css";
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
    page.goto(sys.argv[1] + "#/backlog", wait_until="networkidle")
    page.locator('section[data-screen="backlog"]').wait_for()

    assert page.locator('[data-pagination="tickets"] [data-page-range]').inner_text() == "1–30 of 31"
    assert not any("sprint-item-summaries" in request["path"] for request in page.evaluate("window.__requests()"))
    assert page.locator('[data-backlog-outcomes], [data-outcome-picker]').count() == 0
    assert page.locator('[data-backlog-tickets] > .section-heading').count() == 0
    first_ticket = page.locator('[data-ticket-id="ticket_0"]')
    assert first_ticket.get_attribute("href") == "#/workspace/ticket_0"
    assert first_ticket.locator('.list-row-title').inner_text() == 'Ticket ticket_0'
    assert "A bounded recap for ticket_0" not in first_ticket.inner_text()
    assert first_ticket.locator('.chip').count() == 1
    assert first_ticket.locator('.chip--project').count() == 1
    assert first_ticket.locator('.chip--project').inner_text() == 'Vylo'

    # Each bounded list moves by its own page facts.
    page.locator('[data-pagination="tickets"] button', has_text="Next").click()
    page.locator('[data-ticket-id="ticket_last"]').wait_for()
    assert page.locator('[data-pagination="tickets"] [data-page-range]').inner_text() == "31–31 of 31"
    page.locator('[data-pagination="tickets"] button', has_text="Previous").click()
    page.locator('[data-ticket-id="ticket_0"]').wait_for()

    # The dormant compose writes an ordinary explicitly unscheduled Ticket through
    # the real mutation/query invalidation path.
    page.locator('[data-create="ticket"] > summary').click()
    assert page.locator('[data-create="ticket"] [data-input="worker-type"]').input_value() == "coding"
    priority_control = page.locator('[data-create="ticket"] [data-seg="priority"]')
    assert priority_control.locator('[data-value=""]').get_attribute("aria-pressed") == "true"

    page.locator('[data-create="ticket"] [data-input="title"]').fill("Wire the audit log")
    page.locator('[data-create="ticket"] [data-input="kickoff-note"]').fill("Keep the receipts.")
    page.locator('[data-create="ticket"] [data-seg="project"] [data-value="project_tribe"]').click()
    page.locator('[data-create="ticket"] [data-seg="priority"] [data-value="P1"]').click()
    assert priority_control.locator('[data-value="P1"]').get_attribute("aria-pressed") == "true"
    assert priority_control.locator('[data-value="P3"]').get_attribute("aria-pressed") == "false"
    # Creation names who is asked at the ceiling. Unchanged it is "me"; a Ticket made
    # for somebody else says so here, and the creator does not have to hold it first.
    holder_select = page.locator('[data-create="ticket"] [data-input="ceiling-holder"]')
    assert holder_select.input_value() == "owner"
    assert [option.inner_text() for option in holder_select.locator("option").all()] == ["me", "Chief"]
    holder_select.select_option("chief")
    page.locator('[data-create="ticket"] [data-commit]').click()
    created = page.locator('[data-ticket-id="ticket_created"]')
    created.wait_for()
    assert "Wire the audit log" in created.inner_text()
    assert "Tribe" in created.inner_text()
    create_request = page.evaluate("""() => window.__requests().find(
      request => request.path === "/api/tickets" && request.method === "POST"
    )""")
    assert create_request["body"] == {
        "worker_type": "coding",
        "title": "Wire the audit log",
        "kickoff_note": "Keep the receipts.",
        "project_id": "project_tribe",
        "priority": "P1",
        "sprint_id": None,
        "sprint_item_id": None,
        "ceiling_holder": {"kind": "chief", "id": "chief"},
    }
    assert not any("sprint-item-summaries" in request["path"] for request in page.evaluate("window.__requests()"))
    page.locator('[data-create="ticket"] > summary').click()
    def assert_backlog_layout(width):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(50)
        assert page.evaluate("""() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1""")
        row = page.locator('[data-ticket-id="ticket_0"]')
        title_box = row.locator('.list-row-title').bounding_box()
        chip_box = row.locator('.chip--project').bounding_box()
        assert title_box and chip_box
        assert title_box['x'] + title_box['width'] <= chip_box['x'] + 1
    assert_backlog_layout(1280)
    assert_backlog_layout(390)
    page.set_viewport_size({"width": 1280, "height": 900})

    # A canonical direct Item address survives the empty board response and mounts
    # the real Item workspace, even though the board rail has no row for it.
    page.evaluate("window.location.hash = '#/workspace/item/item_offboard'")
    page.locator('[data-sprint-item-workspace="item_offboard"] [data-sprint-item-brief]').wait_for()
    assert page.locator('[data-sprint-item-brief]').inner_text() == "The off-board brief remains readable."
    page.wait_for_function(
        """() => window.__requests().some(request => request.path === "/api/board")"""
    )
    page.wait_for_timeout(50)
    assert page.evaluate("window.location.hash") == "#/workspace/item/item_offboard"

    page.evaluate("window.location.hash = '#/ideas'")
    page.locator('section[data-screen="ideas"]').wait_for()

    # Title-only ideas are flat.
    page.locator('[data-create="idea"] [data-input="title"]').fill(
        "Dark mode only, skip the light theme"
    )
    page.locator('[data-create="idea"] [data-commit]').click()
    flat = page.locator('[data-ideas] div.list-row[data-idea-id]')
    flat.wait_for()
    assert "Dark mode only, skip the light theme" in flat.inner_text()
    assert page.locator('[data-ideas] details.disclosure--idea').count() == 0

    # A body changes the real rendered component to a closed disclosure. Capturing the
    # first idea clears the title, and that clearing lands when the POST resolves — so
    # the next title is typed only once the box is empty. Typed before, the clearing
    # wipes it and the Capture button stays disabled.
    page.wait_for_function(
        "selector => document.querySelector(selector).value === ''",
        arg='[data-create="idea"] [data-input="title"]',
    )
    page.locator('[data-create="idea"] [data-input="title"]').fill("One-question onboarding")
    page.locator('[data-create="idea"] [data-input="body"]').fill(
        "Ask one thing that matters, infer the rest."
    )
    page.locator('[data-create="idea"] [data-commit]').click()
    details = page.locator('[data-ideas] details.disclosure--idea[data-idea-id]')
    details.wait_for()
    assert "One-question onboarding" in details.locator(".it").inner_text()
    assert "Ask one thing that matters" in details.locator(".disclosure-body").text_content()
    assert details.get_attribute("open") is None
    details.locator("> summary").click()
    page.locator('[data-ideas] details.disclosure--idea[open]').wait_for()
    assert page.locator('[data-ideas] div.list-row[data-idea-id]').count() == 1
    browser.close()

print("backlog and ideas component assertions passed")
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
  assert.match(output, /backlog and ideas component assertions passed/);
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

console.log("backlog-ideas.test.mjs: all assertions passed");
