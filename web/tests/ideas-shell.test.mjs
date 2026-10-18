import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";
import { scratchDirectory } from "./support/scratch.mjs";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const outputDirectory = await mkdtemp(join(tmpdir(), "panels-ideas-shell-"));
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `ideas-shell-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `ideas-shell-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `ideas-shell-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(hostPath, `
<script lang="ts">
  import AppWithQueryClient from "../src/AppWithQueryClient.svelte";

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
    if (path === "/api/projects?detail=full") return json({ projects });
    if (path === "/api/worker-types" || path === "/api/worker-types?supported=true") {
      return json({
        worker_types: [
          { worker_type: "coding", label: "Coding" },
          { worker_type: "exploration", label: "Exploration" }
        ]
      });
    }
    if (path === "/api/board") {
      return json({ columns: [], sprint_items: [] });
    }
    if (path === "/api/workers") {
      return json({
        workers: [],
        chief_of_staff: {
          label: "Chief of Staff",
          skill: { description: "Chief role", markdown_body: "# Chief" },
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
        awaiting_reply: false,
        awaiting_answer: false,
        awaiting_approval: false,
        awaiting_agent_approval: false,
        assigned: false,
        agent_state: "idle",
        ticket_rollup: {
          awaiting_reply: false,
          awaiting_answer: false,
          awaiting_approval: false,
          awaiting_agent_approval: false,
          assigned: false,
          agent_state: "idle"
        },
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
    if (path === "/api/items/item_offboard/conversation/start-values") {
      return json({ backend_key: "codex", model: "gpt-5", reasoning_effort: null });
    }
    if (path === "/api/conversation/backends") {
      return json({ backends: [] });
    }
    if (path === "/api/feedback/count") return json({ open_count: 3 });
    if (path === "/api/skills") return json({ skills: [] });
    if (path === "/api/schedules") return json({ schedules: [] });
    if (path === "/api/notifications/settings") return json({ subjects: [], vapid_public_key: "" });
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
import sys, os, pathlib

def screenshot(page, name):
    directory = os.environ.get("PANELS_UI_PROOF_DIR")
    if directory:
        pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(pathlib.Path(directory) / (name + ".png")), full_page=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(sys.argv[1] + "#/backlog?project=project_vylo", wait_until="networkidle")
    page.locator('section[data-screen="workspace"]').wait_for()
    assert page.evaluate("window.location.hash") == "#/workspace"
    assert page.locator('[data-create="ticket"], [data-backlog-tickets], [data-outcome-picker]').count() == 0

    # The footer and compact fallback expose each utility exactly once.
    for width, selected in [(1280, False), (390, False), (390, True)]:
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("window.location.hash = " + ("'#/workspace/item/item_offboard'" if selected else "'#/workspace'"))
        page.locator('section[data-screen="workspace"]').wait_for()
        more = page.get_by_role("button", name="More", exact=False)
        menu_visible = more.is_visible()
        if menu_visible: more.click()
        for label in ["Settings", "Ideas", "Feedback", "Usage and Backends"]:
            assert page.get_by_role("link", name=label, exact=True).count() == 1, (width, selected, label)
        screenshot(page, "shell-utilities-" + str(width) + ("-selected" if selected else "-rail"))
        if menu_visible:
            page.keyboard.press("Escape")
            assert more.evaluate("n => n === document.activeElement")

    # Config detail keys must remount its initial-prop snapshot on in-shell changes.
    for old, canonical, kind, ident in [
        ("#/config/worker-skill", "#/settings/config/worker-skill", "skill", "panels-worker"),
        ("#/agents/workers/coding", "#/settings/config/workers/coding", "worker", "coding"),
        ("#/config/sprint-item-supervisor", "#/settings/config/sprint-item-supervisor", "skill", "panels-sprint-item-supervisor"),
        ("#/workers/type%2Fwith%20space", "#/settings/config/workers/type%2Fwith%20space", "worker", "type/with space")
    ]:
        page.evaluate("hash => window.location.hash = hash", old)
        page.wait_for_function("hash => window.location.hash === hash", arg=canonical)
        page.locator('[data-screen="config"][data-role-kind="' + kind + '"][data-role-id="' + ident + '"]').wait_for()
    for old, canonical, screen in [
        ("#/scheduled-tasks", "#/settings/scheduled-tasks", "scheduled-tasks"),
        ("#/notifications", "#/settings/notifications", "notifications")
    ]:
        page.evaluate("hash => window.location.hash = hash", old)
        page.wait_for_function("hash => window.location.hash === hash", arg=canonical)
        page.locator('[data-screen="' + screen + '"]').wait_for()
        assert page.get_by_role("navigation", name="Settings", exact=True).count() == 1
        for width in [390, 1280]:
            page.set_viewport_size({"width": width, "height": 900})
            screenshot(page, "settings-" + screen + "-" + str(width))
    page.evaluate("window.location.hash = '#/settings/config'")
    page.locator('[data-screen="config"][data-role-kind="index"]').wait_for()
    for width in [390, 1280]:
        page.set_viewport_size({"width": width, "height": 900})
        screenshot(page, "settings-config-" + str(width))
    page.evaluate("window.location.hash = '#/settings/config/unknown/detail'")
    page.get_by_text("no such screen", exact=True).wait_for()
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

print("ideas and shell component assertions passed")
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
  assert.match(output, /ideas and shell component assertions passed/);
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

console.log("ideas-shell.test.mjs: all assertions passed");
