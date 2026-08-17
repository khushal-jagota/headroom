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

  let backlogItems: any[] = [];
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
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    requests.push({ path, method, body });
    if (path === "/api/review") {
      return json({ running_worker_count: 0, awaiting_approval_count: 0 });
    }
    if (path === "/api/projects") return json({ projects });
    if (path === "/api/items?sprint_id=null" && method === "GET") {
      return json({ items: backlogItems });
    }
    if (path === "/api/items" && method === "POST") {
      const project = projects.find((entry) => entry.id === body.project_id);
      const item = {
        id: "item_" + (backlogItems.length + 1),
        title: body.title,
        body: body.body,
        priority: body.priority,
        deadline: body.deadline ?? null,
        project_id: body.project_id,
        project: project?.name ?? null
      };
      backlogItems = [item, ...backlogItems];
      return json(item);
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

    # The dormant compose writes through the real mutation/query invalidation path.
    page.locator('[data-create="item"] > summary').click()
    priority_control = page.locator('[data-create="item"] [data-seg="priority"]')
    expected_colours = {
        "P0": ("rgb(125, 44, 38)", "rgb(255, 224, 219)"),
        "P1": ("rgb(81, 39, 37)", "rgb(236, 204, 199)"),
        "P2": ("rgb(84, 51, 31)", "rgb(236, 216, 198)"),
        "P3": ("rgb(83, 67, 35)", "rgb(233, 228, 198)"),
    }
    assert priority_control.locator("[data-priority-tile]").count() == 4
    for priority, expected in expected_colours.items():
        option = priority_control.locator(f'[data-value="{priority}"]')
        tile = option.locator(f'[data-priority-tile="{priority}"]')
        assert tile.count() == 1
        assert tile.get_attribute("class").split() == [
            "priority-tile",
            f"priority-tile--{priority.lower()}",
        ]
        assert tile.get_attribute("role") == "img"
        assert tile.get_attribute("aria-label") == f"Priority {priority}"
        assert option.get_attribute("aria-pressed") == (
            "true" if priority == "P3" else "false"
        )
        presentation = tile.evaluate(
            """element => {
              const style = getComputedStyle(element);
              const box = element.getBoundingClientRect();
              return {
                background: style.backgroundColor,
                color: style.color,
                minWidth: style.minWidth,
                padding: [
                  style.paddingTop,
                  style.paddingRight,
                  style.paddingBottom,
                  style.paddingLeft,
                ],
                lineHeight: style.lineHeight,
                width: box.width,
                height: box.height,
              };
            }"""
        )
        assert (presentation["background"], presentation["color"]) == expected
        assert presentation["minWidth"] == "24px"
        assert presentation["padding"] == ["1px", "5px", "1px", "5px"]
        assert presentation["lineHeight"] == "16.5px"
        assert 24 <= presentation["width"] < 27, presentation
        assert 18 <= presentation["height"] < 19, presentation

    page.locator('[data-create="item"] [data-input="title"]').fill("Wire the audit log")
    page.locator('[data-create="item"] [data-seg="project"] [data-value="project_tribe"]').click()
    page.locator('[data-create="item"] [data-seg="priority"] [data-value="P1"]').click()
    assert priority_control.locator('[data-value="P1"]').get_attribute("aria-pressed") == "true"
    assert priority_control.locator('[data-value="P3"]').get_attribute("aria-pressed") == "false"
    page.locator('[data-create="item"] [data-commit]').click()
    item = page.locator('[data-priority-group="P1"] [data-item-id]')
    item.wait_for()
    assert item.locator(".list-row-title").inner_text() == "Wire the audit log"
    assert "Tribe" in item.inner_text()
    assert "P1" not in item.inner_text()
    group = page.locator('[data-priority-group="P1"]')
    assert group.locator('.section-heading [data-priority-tile="P1"]').count() == 1
    assert item.locator("[data-priority-tile]").count() == 0
    assert page.locator("[data-backlog-items] [data-item-id]").count() == 1

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
