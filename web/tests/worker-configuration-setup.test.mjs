import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-model-picker-"));
const hostPath = join(webRoot, "tests", `.model-picker-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.model-picker-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.model-picker-index-${process.pid}.html`);
let serverProcess;

for (const fileName of ["WorkerConfigurationSetup.svelte", "ManagedLaunchDefaults.svelte"]) {
  const source = await readFile(new URL(`../src/components/${fileName}`, import.meta.url), "utf8");
  assert.match(source, /UnifiedModelPicker/, fileName);
  assert.match(source, /resolveModelPicker/, fileName);
  assert.match(source, /readBackends/, fileName);
  assert.match(source, /bind:snapshots=\{backends\}/, fileName);
  assert.doesNotMatch(source, /<select/, fileName);
}

try {
  await writeFile(hostPath, `
<script lang="ts">
  import WorkerConfigurationSetup from "../src/components/WorkerConfigurationSetup.svelte";
  import ManagedLaunchDefaults from "../src/components/ManagedLaunchDefaults.svelte";
  import type { EmployeeConfigurationSnapshot, TicketDetail } from "../src/lib/types";

  const requests: Array<{
    input: RequestInfo | URL;
    init?: RequestInit;
    resolve: (response: Response) => void;
    reject: (reason: Error) => void;
  }> = [];
  globalThis.fetch = ((input, init) => new Promise<Response>((resolve, reject) => {
    requests.push({ input, init, resolve, reject });
  })) as typeof fetch;
  const response = (payload: unknown) => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(payload)
  }) as Response;

  let ticket = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "codex",
    employee_launch_model: "codex-gone",
    employee_launch_reasoning_effort: "extreme"
  });
  let defaults = $state<EmployeeConfigurationSnapshot>({ ...ticket });
  let showDefaults = $state(false);
  let rejectNextTicketSave = false;
  let holdNextTicketSave = false;
  let releaseTicketSave: (() => void) | null = null;
  let holdNextDefaultSave = false;
  let releaseDefaultSave: (() => void) | null = null;
  const ticketSaves: EmployeeConfigurationSnapshot[] = [];
  const defaultSaves: EmployeeConfigurationSnapshot[] = [];

  async function saveTicket(next: EmployeeConfigurationSnapshot): Promise<TicketDetail> {
    ticketSaves.push(structuredClone(next));
    if (rejectNextTicketSave) {
      rejectNextTicketSave = false;
      throw new Error("Ticket save was refused.");
    }
    if (holdNextTicketSave) {
      holdNextTicketSave = false;
      await new Promise<void>((resolve) => (releaseTicketSave = resolve));
      releaseTicketSave = null;
    }
    ticket = { ...next };
    return { ...next } as TicketDetail;
  }
  async function saveDefaults(next: EmployeeConfigurationSnapshot): Promise<EmployeeConfigurationSnapshot> {
    defaultSaves.push(structuredClone(next));
    if (holdNextDefaultSave) {
      holdNextDefaultSave = false;
      await new Promise<void>((resolve) => (releaseDefaultSave = resolve));
      releaseDefaultSave = null;
    }
    defaults = { ...next };
    return defaults;
  }

  (window as any).__respond = (index: number, payload: unknown) => requests[index]?.resolve(response(payload));
  (window as any).__reject = (index: number) => requests[index]?.reject(new Error("transport stopped"));
  (window as any).__request = (index: number) => ({
    url: String(requests[index]?.input),
    method: requests[index]?.init?.method ?? "GET"
  });
  (window as any).__requestCount = () => requests.length;
  (window as any).__ticketSaves = () => ticketSaves;
  (window as any).__defaultSaves = () => defaultSaves;
  (window as any).__showDefaults = () => (showDefaults = true);
  (window as any).__rejectNextTicketSave = () => (rejectNextTicketSave = true);
  (window as any).__holdNextTicketSave = () => (holdNextTicketSave = true);
  (window as any).__releaseTicketSave = () => releaseTicketSave?.();
  (window as any).__holdNextDefaultSave = () => (holdNextDefaultSave = true);
  (window as any).__releaseDefaultSave = () => releaseDefaultSave?.();
</script>

<WorkerConfigurationSetup
  ticketId="ticket-ui"
  employeeBackend={ticket.employee_backend}
  employeeLaunchModel={ticket.employee_launch_model}
  employeeLaunchReasoningEffort={ticket.employee_launch_reasoning_effort}
  onSave={saveTicket}
/>
{#if showDefaults}
  <ManagedLaunchDefaults label="Coding" value={defaults} onSave={saveDefaults} />
{/if}
`, "utf8");
  await writeFile(mainPath, `
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
import "../../assets/tokens.css";
import "../../assets/app.css";
mount(Host, { target: document.getElementById("app")! });
`, "utf8");
  await writeFile(indexPath, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`, "utf8");
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

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", temporaryDirectory],
    { cwd: repositoryRoot, stdio: "ignore" }
  );
  const builtIndex = (await readdir(temporaryDirectory, { recursive: true }))
    .find((path) => path.endsWith(".html"));
  assert.ok(builtIndex);
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
import copy
import sys

def model(model_id, name, efforts):
    return {"model_id": model_id, "display_name": name, "enabled": True, "reasoning_effort_options": efforts}

def backend(key, models, default_model, default_effort=None, efforts=[], diagnoses=[]):
    return {
        "backend_key": key, "installed": True, "executable_path": "/probe/" + key,
        "version": "1", "identity": None, "available_models": models,
        "reasoning_effort_options": efforts, "default_model_id": default_model,
        "default_reasoning_effort": default_effort, "cached_usage": None,
        "update_advisory": None, "diagnoses": diagnoses
    }

machine = {"backends": [
    backend("claude", [model("claude-a", "Claude A", [])], "claude-a"),
    backend("codex", [
        model("codex-native", "Codex native", ["low", "high"]),
        model("codex-deep", "Codex deep", ["low", "high"]),
        model("codex-plain", "Codex plain", []),
        model("codex-mini", "Codex mini", ["low", "high"]),
        model("codex-fast", "Codex fast", ["low", "high"]),
        model("codex-long", "Codex long", ["low", "high"]),
        model("codex-spark", "Codex spark", ["low", "high"])
    ], "codex-native", "low", ["low", "high"]),
    backend("hermes", [], None, diagnoses=["Hermes has no configured model."])
]}
refreshed_machine = copy.deepcopy(machine)
refreshed_machine["backends"][1]["available_models"].append(
    model("codex-refreshed", "Codex refreshed", ["low", "high"])
)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(sys.argv[1], wait_until="networkidle")
    page.wait_for_function("window.__requestCount() === 1")
    page.evaluate("payload => window.__respond(0, payload)", machine)

    setup = page.locator("[data-employee-configuration-setup]")
    picker = setup.locator("[data-conversation-model-picker]")
    trigger = picker.locator("[data-conversation-picker-trigger]")
    assert "codex-gone extreme" in trigger.inner_text()
    trigger.click()
    choices = picker.locator("[data-conversation-picker-choice]")
    panel = picker.locator("[data-conversation-picker-panel]")
    controlled = trigger.get_attribute("aria-controls")
    assert controlled and picker.locator(f"#{controlled}").get_attribute("role") == "listbox"
    assert picker.locator("button").evaluate_all("buttons => buttons.filter(button => button.tabIndex === 0).length") == 1
    assert choices.count() == 7
    assert picker.locator('[data-conversation-picker-choice="codex-gone"]').count() == 0
    assert picker.locator("[data-conversation-picker-chosen]").count() == 0
    assert picker.locator("[data-conversation-picker-feedback]").inner_text() == "Codex no longer offers codex-gone."
    assert panel.evaluate("el => el.scrollHeight <= el.clientHeight")
    assert picker.locator(".model-picker-list").evaluate("el => el.scrollHeight <= el.clientHeight")
    assert panel.bounding_box()["height"] < 844
    picker.locator('[data-conversation-picker-choice="codex-deep"]').hover()
    assert page.evaluate("window.__ticketSaves().length") == 0
    picker.get_by_role("listbox").press("c")
    page.wait_for_function("document.querySelector('[data-conversation-picker-choice=codex-plain]')?.getAttribute('data-conversation-picker-active') === 'true'")
    assert picker.locator('[data-conversation-picker-choice="codex-plain"]').get_attribute("data-conversation-picker-active") == "true"

    refresh = picker.locator("[data-conversation-picker-refresh]")
    assert refresh.inner_text() == "Refresh"
    refresh.click()
    page.wait_for_function("window.__requestCount() === 2")
    assert page.evaluate("window.__request(1)") == {
        "url": "/api/conversation/backends/refresh", "method": "POST"
    }
    assert refresh.inner_text() == "Reading…"
    assert refresh.get_attribute("aria-busy") == "true"
    assert refresh.is_disabled()
    page.evaluate("payload => window.__respond(1, payload)", {
        **refreshed_machine,
        "usage_outcomes": [
            {"backend_key": "codex", "outcome": "succeeded", "detail": None},
            {"backend_key": "claude", "outcome": "failed", "detail": "Claude usage failed."},
            {"backend_key": "hermes", "outcome": "unavailable", "detail": "No usage source."}
        ]
    })
    page.wait_for_function("document.querySelector('[data-conversation-picker-refresh]').textContent.includes('Refresh')")
    assert picker.locator('[data-conversation-picker-choice="codex-refreshed"]').count() == 1
    assert picker.locator("[data-conversation-picker-feedback]").inner_text() == "Claude usage failed."

    refresh.click()
    page.wait_for_function("window.__requestCount() === 3")
    page.evaluate("window.__reject(2)")
    page.wait_for_function("document.querySelector('[data-conversation-picker-feedback]').textContent.includes('server could not be reached')")
    assert picker.locator('[data-conversation-picker-choice="codex-refreshed"]').count() == 1

    page.evaluate("window.__rejectNextTicketSave()")
    picker.locator('[data-conversation-picker-choice="codex-native"]').click()
    page.wait_for_function("window.__ticketSaves().length === 1")
    page.wait_for_selector("[data-employee-configuration-save-error]")
    assert "codex-gone extreme" in trigger.inner_text()
    assert picker.locator("[data-conversation-picker-panel]").count() == 1
    page.keyboard.press("Escape")

    trigger.click()
    picker.locator('[data-conversation-picker-choice="codex-native"]').click()
    page.wait_for_function("window.__ticketSaves().length === 2")
    assert picker.locator('[data-conversation-picker-choice="low"]').count() == 1
    picker.locator('[data-conversation-picker-choice="low"]').click()
    page.wait_for_function("window.__ticketSaves().length === 3")
    assert page.evaluate("window.__ticketSaves()[0]") == {
        "employee_backend": "codex", "employee_launch_model": "codex-native",
        "employee_launch_reasoning_effort": "low"
    }

    trigger.click()
    assert picker.locator("[data-conversation-backend]").evaluate_all(
        "rows => rows.map(row => row.getAttribute('data-conversation-backend'))"
    ) == ["claude", "codex", "hermes"]
    picker.locator('[data-conversation-backend="codex"]').click()
    assert page.evaluate("window.__ticketSaves().length") == 3
    assert picker.locator("[data-conversation-picker-panel]").count() == 1
    hermes = picker.locator('[data-conversation-backend="hermes"]')
    assert hermes.get_attribute("aria-disabled") == "true"
    hermes.click(force=True)
    assert picker.locator("[data-conversation-picker-feedback]").inner_text() == "Hermes has no configured model."
    page.evaluate("window.__holdNextTicketSave()")
    picker.locator('[data-conversation-backend="claude"]').click()
    page.wait_for_function("window.__ticketSaves().length === 4")
    assert page.evaluate("window.__ticketSaves()[3]") == {
        "employee_backend": "claude", "employee_launch_model": "claude-a",
        "employee_launch_reasoning_effort": None
    }
    assert picker.locator("[data-conversation-picker-panel]").count() == 1
    assert picker.locator("[data-conversation-backend-showing]").inner_text() == "Claude"
    assert picker.locator('[data-conversation-picker-choice="claude-a"]').count() == 1
    assert picker.locator("[data-conversation-picker-panel]").get_attribute("aria-busy") == "true"
    assert picker.locator('[data-conversation-picker-choice="claude-a"]').is_disabled()
    page.evaluate("window.__releaseTicketSave()")
    page.wait_for_function("!document.querySelector('[data-conversation-picker-trigger]').disabled")
    reasoning = picker.locator("[data-conversation-picker-reasoning]")
    assert reasoning.get_attribute("aria-disabled") == "true"
    assert picker.locator("button").evaluate_all("buttons => buttons.filter(button => button.tabIndex === 0).length") == 1
    picker.get_by_role("listbox").press("ArrowLeft")
    page.keyboard.press("End")
    assert reasoning.evaluate("el => document.activeElement === el")
    page.keyboard.press("Enter")
    assert picker.locator("[data-conversation-picker-feedback]").inner_text() == "Claude A takes no reasoning effort."
    page.keyboard.press("Escape")

    page.evaluate("window.__showDefaults()")
    page.wait_for_function("window.__requestCount() === 4")
    page.evaluate("payload => window.__respond(3, payload)", machine)
    defaults = page.locator("[data-launch-defaults]")
    default_picker = defaults.locator("[data-conversation-model-picker]")
    default_picker.locator("[data-conversation-picker-trigger]").click()
    assert default_picker.locator('[data-conversation-picker-choice="codex-gone"]').count() == 0
    assert default_picker.locator("[data-conversation-picker-feedback]").inner_text() == "Codex no longer offers codex-gone."
    default_picker.locator('[data-conversation-picker-choice="codex-plain"]').click()
    page.wait_for_function("window.__defaultSaves().length === 1")
    assert page.evaluate("window.__defaultSaves()[0]") == {
        "employee_backend": "codex", "employee_launch_model": "codex-plain",
        "employee_launch_reasoning_effort": None
    }
    default_picker.locator("[data-conversation-picker-trigger]").click()
    page.evaluate("window.__holdNextDefaultSave()")
    default_picker.locator('[data-conversation-backend="claude"]').click()
    page.wait_for_function("window.__defaultSaves().length === 2")
    assert default_picker.locator("[data-conversation-backend-showing]").inner_text() == "Claude"
    assert default_picker.locator('[data-conversation-picker-choice="claude-a"]').count() == 1
    assert default_picker.locator("[data-conversation-picker-panel]").get_attribute("aria-busy") == "true"
    page.evaluate("window.__releaseDefaultSave()")
    page.wait_for_function("!document.querySelector('[data-launch-defaults] [data-conversation-picker-trigger]').disabled")
    browser.close()

print("unified persisted picker assertions passed")
`;
  const probe = spawn(join(repositoryRoot, ".venv", "bin", "python"), ["-c", browserScript, url], {
    cwd: repositoryRoot,
    stdio: ["ignore", "pipe", "pipe"]
  });
  let output = "";
  probe.stdout.on("data", (chunk) => { output += chunk; });
  probe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => probe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /unified persisted picker assertions passed/);
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
  throw new Error(`component runtime server did not become ready: ${url}`);
}

console.log("worker-configuration-setup.test.mjs: all assertions passed");
