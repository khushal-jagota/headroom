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
const outputDirectory = await mkdtemp(join(tmpdir(), "panels-scheduled-tasks-"));
const hostPath = join(webRoot, "tests", `.scheduled-tasks-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.scheduled-tasks-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.scheduled-tasks-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(hostPath, `
<script lang="ts">
  import AppWithQueryClient from "../src/AppWithQueryClient.svelte";

  const projects = [{
    id: "project_panels",
    name: "Panels",
    summary: "The Panels app",
    priority: "P3",
    created_at: 1,
    updated_at: 1
  }];
  const sprintItems = [{
    id: "si_demo",
    title: "Scheduled tasks",
    project_id: "project_panels",
    project: "Panels",
    sprint_id: "sp_demo",
    kind: "normal"
  }];
  const workerTypes = [
    {
      worker_type: "coding",
      label: "Coding",
      stages: [],
      dropped: { id: "dropped", label: "Dropped", gating_field: null, is_terminal: true, default_ownership_mode: null },
      advance: {},
      fields: [],
      ceiling_range: [],
      default_ceiling: "needs_implementation",
      worker_profile_id: "coding",
      default_backend: "codex",
      default_model: null,
      default_reasoning_effort: null
    }
  ];
  let schedules: any[] = [{
    id: "schedule_demo",
    enabled: true,
    cadence: "every_planning_day",
    local_time: "09:00",
    title: "Existing planning task",
    worker_type: "coding",
    kickoff_note: "Use the saved schedule.",
    priority: "P2",
    deadline: null,
    project_id: "project_panels",
    placement_mode: "current_sprint",
    sprint_item_id: null,
    employee_backend: null,
    employee_launch_model: null,
    blocked_by_ticket_ids: [],
    created_at: 1,
    updated_at: 1
  }];
  const requests: Array<{ path: string; method: string; body: any }> = [];

  class QuietEventSource {
    onopen: null | (() => void) = null;
    onmessage: null | (() => void) = null;
    onerror: null | (() => void) = null;
    constructor(_url: string) {}
    close() {}
  }
  (globalThis as any).EventSource = QuietEventSource;

  function json(value: unknown, status = 200): Response {
    return new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" }
    });
  }
  function scheduleFromBody(id: string, body: any) {
    return {
      id,
      enabled: body.enabled,
      cadence: body.cadence,
      local_time: body.local_time,
      title: body.title,
      worker_type: body.worker_type,
      kickoff_note: body.kickoff_note,
      priority: body.priority,
      deadline: body.deadline,
      project_id: body.project_id,
      placement_mode: body.placement_mode,
      sprint_item_id: body.sprint_item_id,
      employee_backend: body.employee_backend,
      employee_launch_model: body.employee_launch_model,
      blocked_by_ticket_ids: body.blocked_by_ticket_ids,
      created_at: 2,
      updated_at: 2
    };
  }
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    requests.push({ path, method, body });
    if (path === "/api/review") {
      return json({ items: [], running_worker_count: 0, awaiting_approval_count: 0 });
    }
    if (path === "/api/projects") return json({ projects });
    if (path === "/api/items") return json({ items: sprintItems });
    if (path === "/api/worker-types") return json({ worker_types: workerTypes });
    if (path === "/api/schedules" && method === "GET") return json({ schedules });
    if (path === "/api/schedules" && method === "POST") {
      if (body.title === "Reject me") {
        return json({ error: { code: "validation", message: "The schedule was rejected." } }, 422);
      }
      const created = scheduleFromBody("schedule_created", body);
      schedules = [created, ...schedules];
      return json(created);
    }
    if (path === "/api/schedules/schedule_demo" && method === "PATCH") {
      schedules = schedules.map((schedule) =>
        schedule.id === "schedule_demo" ? { ...schedule, ...body, updated_at: 3 } : schedule
      );
      return json(schedules.find((schedule) => schedule.id === "schedule_demo"));
    }
    return json({ error: { code: "unexpected", message: path } }, 500);
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
import os
import sys

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    screenshot_dir = os.environ.get("SCHEDULED_TASKS_SCREENSHOT_DIR")
    page.goto(sys.argv[1] + "#/scheduled-tasks", wait_until="networkidle")
    page.locator('section[data-screen="scheduled-tasks"]').wait_for()
    page.locator('[data-screen="more"]').click()
    scheduled_tasks_link = page.locator('a[data-screen="scheduled-tasks"].active')
    scheduled_tasks_link.wait_for()
    assert scheduled_tasks_link.inner_text() == "Scheduled tasks"
    page.keyboard.press("Escape")
    if screenshot_dir:
        page.screenshot(path=os.path.join(screenshot_dir, "scheduled-tasks-list-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=os.path.join(screenshot_dir, "scheduled-tasks-list-phone.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})
    card = page.locator('[data-schedule-id="schedule_demo"]')
    card.wait_for()
    assert "Existing planning task" in card.inner_text()
    assert card.locator('[data-schedule-title]').inner_text() == "Existing planning task"
    assert card.locator('[data-schedule-toggle]').get_attribute("aria-checked") == "true"

    page.get_by_role("button", name="Add").click()
    editor = page.locator("[data-schedule-editor]")
    editor.wait_for()
    if screenshot_dir:
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=os.path.join(screenshot_dir, "scheduled-tasks-editor-phone.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})
    submit = editor.locator("[data-commit-schedule]")
    submit.click()
    page.locator("[data-schedule-error]").wait_for()
    assert "title" in page.locator("[data-schedule-error]").inner_text().lower()
    assert submit.is_enabled()

    title = editor.locator('[data-schedule-title] .ed')
    title.fill("Reject me")
    submit.click()
    page.locator(".error-line").wait_for()
    assert "schedule was rejected" in page.locator(".error-line").inner_text()

    title.fill("Created browser schedule")
    editor.locator('[data-input="local-time"]').fill("11:30")
    editor.locator('[data-input="placement"]').select_option("sprint_item")
    editor.locator('[data-input="sprint-item"]').select_option("si_demo")
    submit.click()
    created = page.locator('[data-schedule-id="schedule_created"]')
    created.wait_for()
    assert "Created browser schedule" in created.inner_text()
    created.locator('[data-schedule-title]').click()
    editor.locator('[data-input="local-time"]').wait_for()
    assert editor.locator('[data-input="local-time"]').input_value() == "11:30"
    assert editor.locator('[data-input="sprint-item"]').input_value() == "si_demo"
    page.locator('[data-back-to-schedules]').click()

    card.locator('[data-schedule-toggle]').click()
    page.wait_for_function("document.querySelector('[data-schedule-id=\\\"schedule_demo\\\"] [data-schedule-toggle]')?.getAttribute('aria-checked') === 'false'")
    assert card.locator('[data-schedule-toggle]').get_attribute("aria-checked") == "false"
    card.locator('[data-schedule-title]').click()
    editor.locator('[data-schedule-title] .ed').fill("Edited existing schedule")
    editor.locator('[data-input="local-time"]').fill("09:50")
    editor.get_by_text("Advanced Ticket settings").click()
    editor.locator('[data-input="employee-backend"]').fill("codex")
    editor.get_by_role("button", name="Save changes").click()
    card = page.locator('[data-schedule-id="schedule_demo"]')
    card.locator('[data-schedule-title]').filter(has_text="Edited existing schedule").wait_for()
    assert "Edited existing schedule" in card.inner_text()
    assert page.locator('[data-schedule-id]').count() == 2

    requests = page.evaluate("window.__requests()")
    assert any(request["path"] == "/api/schedules" and request["method"] == "POST" for request in requests)
    assert any(request["path"] == "/api/schedules/schedule_demo" and request["method"] == "PATCH" for request in requests)
    assert any(request["method"] == "PATCH" and request["body"]["enabled"] is False for request in requests)
    assert any(request["path"] == "/api/schedules/schedule_demo" and request["method"] == "PATCH" and request["body"].get("title") == "Edited existing schedule" for request in requests)
    browser.close()

print("scheduled tasks browser assertions passed")
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
  assert.match(output, /scheduled tasks browser assertions passed/);
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

console.log("scheduled-tasks-browser.test.mjs: all assertions passed");
