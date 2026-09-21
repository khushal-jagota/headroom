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
const outputDirectory = await mkdtemp(join(tmpdir(), "panels-feedback-"));
const suffix = process.pid;
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `feedback-host-${suffix}.svelte`);
const mainPath = join(scratchRoot, `feedback-main-${suffix}.ts`);
const indexPath = join(scratchRoot, `feedback-index-${suffix}.html`);
let serverProcess;

try {
  await writeFile(hostPath, `
<script lang="ts">
  import AppWithQueryClient from "../src/AppWithQueryClient.svelte";
  let notes: any[] = [{
    id: "f_1", text: "The button moved", page_address: "#/review", page_label: "Review",
    state: "open", ticket_id: null, created_at: Date.now() / 1000,
    updated_at: Date.now() / 1000, handled_at: null
  }];
  const requests: any[] = [];
  class QuietEventSource { onopen = null; onmessage = null; onerror = null; close() {} }
  (globalThis as any).EventSource = QuietEventSource;
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json" }
  });
  const response = () => ({
    open_count: notes.filter((note) => note.state === "open").length,
    open: notes.filter((note) => note.state === "open"),
    handled_groups: notes.some((note) => note.state === "handled")
      ? [{ ticket: null, notes: notes.filter((note) => note.state === "handled") }]
      : []
  });
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input); const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    requests.push({ path, method, body });
    if (path === "/api/review") return json({ items: [], running_worker_count: 0 });
    if (path === "/api/feedback/count" && method === "GET") return json({ open_count: response().open_count });
    if (path === "/api/feedback" && method === "GET") return json(response());
    if (path === "/api/feedback" && method === "POST") {
      if (body.text === "fail") return json({ error: { code: "offline", message: "offline" } }, 503);
      const note = { id: "f_" + (notes.length + 1), ...body, state: "open", ticket_id: null,
        created_at: Date.now() / 1000, updated_at: Date.now() / 1000, handled_at: null };
      notes = [note, ...notes]; return json(note);
    }
    const match = path.match(/^\\/api\\/feedback\\/(f_\\d+)\\/(dismiss|reopen)$/);
    if (match && method === "POST") {
      notes = notes.map((note) => note.id === match[1] ? { ...note, state: match[2] === "dismiss" ? "handled" : "open" } : note);
      return json({});
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
  await writeFile(indexPath, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"></head><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`, "utf8");
  await build({
    root: webRoot, base: "./", configFile: false, logLevel: "silent", plugins: [svelte()],
    build: { emptyOutDir: true, outDir: outputDirectory, rollupOptions: { input: { index: indexPath } } }
  });

  const port = await availablePort();
  serverProcess = spawn(join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", outputDirectory],
    { cwd: repositoryRoot, stdio: "ignore" });
  const builtIndex = (await readdir(outputDirectory, { recursive: true })).find((path) => path.endsWith(".html"));
  assert.ok(builtIndex);
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
import sys
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    # The global shell reads only the bounded count; the full history belongs to its route.
    page.goto(sys.argv[1] + "#/unknown", wait_until="networkidle")
    page.locator('[data-screen="more"]').click()
    page.locator('.fb-count').wait_for()
    shell_requests = page.evaluate("window.__requests()")
    assert any(r["path"] == "/api/feedback/count" for r in shell_requests)
    assert not any(r["path"] == "/api/feedback" for r in shell_requests)
    page.keyboard.press("Escape")
    page.evaluate("window.location.hash = '#/feedback'")
    page.locator('[data-screen="feedback"]').wait_for()
    assert any(r["path"] == "/api/feedback" for r in page.evaluate("window.__requests()"))
    assert page.locator('[data-feedback-note="f_1"]').count() == 1
    page.locator('[data-screen="more"]').click()
    assert page.locator('.fb-count').inner_text() == "1"
    page.keyboard.press("Escape")

    # Closing keeps a device-local draft and restores it.
    trigger = page.locator('[data-feedback-trigger]')
    trigger.click()
    input_box = page.locator('[data-feedback-input]')
    assert input_box.evaluate("element => element === document.activeElement")
    assert page.locator('.fb-context .chip').inner_text() == "Feedback"
    input_box.fill("unfinished")
    page.keyboard.press("Escape")
    assert trigger.get_attribute("data-draft") == "true"
    assert trigger.evaluate("element => element === document.activeElement")
    page.reload(wait_until="networkidle")
    page.locator('[data-screen="feedback"]').wait_for()
    trigger = page.locator('[data-feedback-trigger]')
    assert trigger.get_attribute("data-draft") == "true"
    trigger.click()
    input_box = page.locator('[data-feedback-input]')
    assert input_box.input_value() == "unfinished"

    # Shift+Enter inserts a line; Enter saves with the current route context.
    input_box.fill("first line")
    page.keyboard.press("Shift+Enter")
    input_box.type("second line")
    page.keyboard.press("Enter")
    page.locator('[data-feedback-toast]').wait_for()
    toast_text = page.locator('[data-feedback-toast]').inner_text()
    assert toast_text == "Saved to Feedback · View", repr(toast_text)
    posted = [r for r in page.evaluate("window.__requests()") if r["path"] == "/api/feedback" and r["method"] == "POST"][-1]
    assert posted["body"] == {"text": "first line\nsecond line", "page_address": "#/feedback", "page_label": "Feedback"}

    # Failure leaves the text and changes the retry action.
    trigger.click(); input_box.fill("fail"); page.keyboard.press("Enter")
    page.locator('.error-line').wait_for()
    assert input_box.input_value() == "fail"
    assert page.locator('[data-feedback-save]').inner_text() == "Try again"
    page.keyboard.press("Escape")

    # Dismiss moves immediately; Undo reopens through the same canonical action.
    page.locator('[data-feedback-note="f_1"] [data-feedback-action="dismiss"]').click()
    page.locator('[data-feedback-toast] button', has_text="Undo").click()
    page.locator('[data-feedback-note="f_1"]').wait_for()
    paths = [r["path"] for r in page.evaluate("window.__requests()")]
    assert "/api/feedback/f_1/dismiss" in paths
    assert "/api/feedback/f_1/reopen" in paths

    # At phone size the capture becomes a touch-safe sheet above the bottom nav.
    phone_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    phone = phone_context.new_page()
    phone.goto(sys.argv[1] + "#/feedback", wait_until="networkidle")
    phone.locator('[data-feedback-trigger]').click()
    sheet = phone.locator('[data-feedback-capture]').bounding_box()
    nav = phone.locator('.shell-nav').bounding_box()
    assert sheet and nav and sheet["y"] + sheet["height"] <= nav["y"] + 1
    phone_input = phone.locator('[data-feedback-input]')
    assert phone_input.get_attribute("enterkeyhint") == "done"
    assert phone_input.evaluate("element => getComputedStyle(element).fontSize") == "16px"
    assert phone.locator('[data-feedback-save]').is_visible()
    phone_context.close()
    browser.close()
`;
  const result = await run(join(repositoryRoot, ".venv", "bin", "python"), ["-c", browserScript, url]);
  assert.equal(result.code, 0, result.stderr || result.stdout);
  console.log("feedback inbox browser tests passed");
} finally {
  serverProcess?.kill("SIGTERM");
  await Promise.all([rm(outputDirectory, { recursive: true, force: true }), rm(hostPath, { force: true }), rm(mainPath, { force: true }), rm(indexPath, { force: true })]);
}

function availablePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") return reject(new Error("no port"));
      server.close(() => resolve(address.port));
    });
  });
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try { if ((await fetch(url)).ok) return; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("server did not start");
}

function run(command, args) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.on("data", (chunk) => stdout += chunk);
    child.stderr.on("data", (chunk) => stderr += chunk);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}
