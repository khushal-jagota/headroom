import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Browser proof for the two user-facing consequences of declared ownership:
// Config only displays it, and approval scope starts at the accepted Stage's successor.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const stem = `.ownership-controls-${process.pid}`;
const host = join(root, "tests", `${stem}.svelte`);
const main = join(root, "tests", `${stem}.ts`);
const index = join(root, "tests", `${stem}.html`);
let server;

try {
  await writeFile(host, `<script lang="ts">
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import CeilingPicker from "../src/components/CeilingPicker.svelte";
import ConfigRoute from "../src/routes/ConfigRoute.svelte";
import { buildLifecycle, type WorkerTypeManifest } from "../src/lib/lifecycle";

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const manifest: WorkerTypeManifest = {
  worker_type: "coding",
  label: "Coding",
  stages: [
    { id: "needs_success", label: "Success", gating_field: "success", is_terminal: false, ownership_mode: "user" },
    { id: "needs_approach", label: "Approach", gating_field: "approach", is_terminal: false, ownership_mode: "worker" },
    { id: "needs_plan", label: "Plan", gating_field: "plan", is_terminal: false, ownership_mode: "worker" },
    { id: "done", label: "Done", gating_field: null, is_terminal: true, ownership_mode: null }
  ],
  advance: { needs_success: "needs_approach", needs_approach: "needs_plan", needs_plan: "done" },
  fields: [
    { id: "success", label: "Success" },
    { id: "approach", label: "Approach" },
    { id: "plan", label: "Plan" }
  ],
  ceiling_range: ["needs_success", "needs_approach", "needs_plan", "done"],
  default_ceiling: "needs_plan",
  worker_profile_id: "panels-worker-coding",
  default_backend: "codex",
  default_model: null,
  default_reasoning_effort: null
};
const lifecycle = buildLifecycle(manifest);
let ceiling = $state<string | null>(null);
</script>

<QueryClientProvider {client}>
  <ConfigRoute roleKind="worker" roleId="coding" />
</QueryClientProvider>
<div data-scope-proof>
  <CeilingPicker newStage="needs_approach" {lifecycle} bind:ceiling />
</div>
`, "utf8");
  await writeFile(
    main,
    `import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host, { target: document.getElementById("app")! });`,
    "utf8"
  );
  await writeFile(
    index,
    `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`,
    "utf8"
  );

  server = await createServer({
    root,
    configFile: false,
    plugins: [svelte()],
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "error"
  });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === "object");

  const script = String.raw`
import json, sys
from playwright.sync_api import sync_playwright, expect

manifest = {
    "worker_type": "coding", "label": "Coding",
    "stages": [
        {"id": "needs_success", "label": "Success", "gating_field": "success", "is_terminal": False, "ownership_mode": "user"},
        {"id": "needs_approach", "label": "Approach", "gating_field": "approach", "is_terminal": False, "ownership_mode": "worker"},
        {"id": "needs_plan", "label": "Plan", "gating_field": "plan", "is_terminal": False, "ownership_mode": "worker"},
        {"id": "done", "label": "Done", "gating_field": None, "is_terminal": True, "ownership_mode": None},
    ],
    "advance": {"needs_success": "needs_approach", "needs_approach": "needs_plan", "needs_plan": "done"},
    "fields": [{"id": "success", "label": "Success"}, {"id": "approach", "label": "Approach"}, {"id": "plan", "label": "Plan"}],
    "ceiling_range": ["needs_success", "needs_approach", "needs_plan", "done"],
    "default_ceiling": "needs_plan", "worker_profile_id": "panels-worker-coding",
    "default_backend": "codex", "default_model": None, "default_reasoning_effort": None,
}
skill = {"name": "panels-worker-coding", "description": "Coding worker", "markdown_body": "# Coding"}
detail = {
    "manifest": manifest,
    "settings": {
        "worker_type": "coding", "specialist_skill": skill,
        "launch_defaults": {"employee_backend": "codex", "employee_launch_model": None, "employee_launch_reasoning_effort": None},
    },
}

def respond(route):
    path = route.request.url.split("/api/", 1)[1]
    if path == "workers/coding": result = detail
    elif path == "conversation/backends": result = {"backends": []}
    else: result = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/api/**", respond)
    page.goto(sys.argv[1])

    table = page.locator("[data-worker-stage-table]")
    expect(table.locator("[data-worker-stage-row]")).to_have_count(4)
    expect(table.locator('[data-stage="needs_success"] [data-stage-owner]')).to_have_attribute("data-stage-owner", "user")
    expect(table.locator('[data-stage="needs_approach"] [data-stage-owner]')).to_have_attribute("data-stage-owner", "worker")
    expect(table.locator('[data-stage="done"] [data-stage-owner]')).to_have_attribute("data-stage-owner", "")
    assert table.locator("select, input, [contenteditable]").count() == 0
    assert page.locator("[data-suggested-next-ceiling]").count() == 0

    ceiling = page.locator("[data-scope-proof] [data-scope-ceiling]")
    expect(ceiling).to_have_value("needs_plan")
    assert ceiling.locator('option[value="needs_plan"]').count() == 1
    browser.close()
`;
  const child = spawn(
    join(root, "..", ".venv", "bin", "python"),
    ["-c", script, `http://127.0.0.1:${address.port}/tests/${stem}.html`],
    { stdio: "inherit" }
  );
  const code = await new Promise((resolve) => child.on("exit", resolve));
  assert.equal(code, 0, "Ownership controls browser proof failed");
} finally {
  await server?.close();
  await Promise.all([host, main, index].map((path) => rm(path, { force: true })));
}

console.log("ownership-controls-browser.test.mjs: all assertions passed");
