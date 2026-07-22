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
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-employee-configuration-"));
const hostPath = join(webRoot, "tests", `.employee-configuration-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.employee-configuration-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.employee-configuration-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(hostPath, `
<script lang="ts">
  import EmployeeConfigurationSetup from "../src/components/EmployeeConfigurationSetup.svelte";
  import ManagedLaunchDefaults from "../src/components/ManagedLaunchDefaults.svelte";
  import type {
    EmployeeConfigurationSnapshot,
    TicketDetail
  } from "../src/lib/types";

  type PendingRequest = {
    url: string;
    aborted: boolean;
    settled: boolean;
    resolve: (response: Response) => void;
    reject: (error: unknown) => void;
  };

  let editable = $state(true);
  let saved = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "hermes",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null
  });
  const saveCalls: EmployeeConfigurationSnapshot[] = [];
  const requests: PendingRequest[] = [];

  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    return new Promise<Response>((resolve, reject) => {
      const request: PendingRequest = { url, aborted: false, settled: false, resolve, reject };
      requests.push(request);
      init?.signal?.addEventListener("abort", () => {
        request.aborted = true;
        if (!request.settled) {
          request.settled = true;
          reject(new DOMException("aborted", "AbortError"));
        }
      }, { once: true });
    });
  }) as typeof fetch;

  function response(status: number, payload: unknown): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      text: async () => JSON.stringify(payload)
    } as Response;
  }

  async function onSave(configuration: EmployeeConfigurationSnapshot): Promise<TicketDetail> {
    saveCalls.push(structuredClone(configuration));
    saved = { ...configuration };
    return {
      employee_backend: saved.employee_backend,
      employee_launch_model: saved.employee_launch_model,
      employee_launch_reasoning_effort: saved.employee_launch_reasoning_effort
    } as TicketDetail;
  }

  (window as any).__requests = () => requests.map((request) => ({
    url: request.url,
    aborted: request.aborted,
    settled: request.settled
  }));
  (window as any).__respond = (index: number, payload: unknown) => {
    const request = requests[index];
    if (!request || request.settled) return;
    request.settled = true;
    request.resolve(response(200, payload));
  };
  (window as any).__fail = (index: number) => {
    const request = requests[index];
    if (!request || request.settled) return;
    request.settled = true;
    request.resolve(response(503, {
      error: { code: "catalog_unavailable", message: "catalog unavailable" }
    }));
  };
  (window as any).__saveCalls = () => saveCalls;
  (window as any).__freeze = () => (editable = false);

  let showLaunchDefaults = $state(false);
  let launchDefaults = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "codex",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null
  });
  const launchDefaultsSaveCalls: EmployeeConfigurationSnapshot[] = [];

  async function onLaunchDefaultsSave(
    next: EmployeeConfigurationSnapshot
  ): Promise<EmployeeConfigurationSnapshot> {
    launchDefaultsSaveCalls.push(structuredClone(next));
    launchDefaults = { ...next };
    return launchDefaults;
  }

  (window as any).__showLaunchDefaults = () => (showLaunchDefaults = true);
  (window as any).__launchDefaultsSaveCalls = () => launchDefaultsSaveCalls;
  (window as any).__setLaunchDefaults = (next: EmployeeConfigurationSnapshot) =>
    (launchDefaults = next);
</script>

{#if editable}
  <EmployeeConfigurationSetup
    ticketId="ticket-ui"
    employeeBackends={["hermes", "codex"]}
    employeeBackend={saved.employee_backend}
    employeeLaunchModel={saved.employee_launch_model}
    employeeLaunchReasoningEffort={saved.employee_launch_reasoning_effort}
    {onSave}
  />
{/if}

{#if showLaunchDefaults}
  <ManagedLaunchDefaults
    label="Coding"
    employeeBackends={["hermes", "codex"]}
    value={launchDefaults}
    onSave={onLaunchDefaultsSave}
  />
{/if}
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
      outDir: temporaryDirectory,
      rollupOptions: { input: { index: indexPath } },
    },
  });

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", temporaryDirectory],
    { cwd: repositoryRoot, stdio: "ignore" },
  );
  const builtIndex = (await readdir(temporaryDirectory, { recursive: true }))
    .find((path) => path.endsWith(".html"));
  assert.ok(builtIndex, "the component build must emit an HTML entry");
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
import sys

def catalog(backend, candidate, native_model, models, reasoning_supported=False, reasoning=()):
    return {
        "employee_backend": backend,
        "candidate_model": candidate,
        "native_model": native_model,
        "models": [
            {"value": value, "label": label, "description": None}
            for value, label in models
        ],
        "reasoning_supported": reasoning_supported,
        "native_reasoning_effort": reasoning[0][0] if reasoning else None,
        "reasoning_efforts": [
            {"value": value, "label": label, "description": None}
            for value, label in reasoning
        ],
    }

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="networkidle")

    setup = page.locator("[data-employee-configuration-setup]")
    setup.wait_for()
    assert setup.get_attribute("data-employee-configuration-backend") == "hermes"
    page.locator("[data-employee-configuration-loading]").wait_for()
    assert page.locator("[data-employee-configuration-worker]").inner_text().startswith("worker")
    worker = page.locator("[data-employee-configuration-worker] select")
    worker.focus()
    focus_style = page.locator("[data-employee-configuration-worker]").evaluate("""
        element => {
            const style = getComputedStyle(element);
            return {
                style: style.outlineStyle,
                width: style.outlineWidth,
                color: style.outlineColor,
            };
        }
    """)
    assert focus_style["style"] == "solid"
    assert focus_style["width"] != "0px"
    assert focus_style["color"] not in ("transparent", "rgba(0, 0, 0, 0)")
    assert page.evaluate("window.__requests().length") == 1
    assert "employee_backend=hermes" in page.evaluate("window.__requests()[0].url")
    assert "candidate_model" not in page.evaluate("window.__requests()[0].url")

    page.evaluate("payload => window.__respond(0, payload)", catalog(
        "hermes", None, "hermes-native", [("hermes-native", "Hermes native")]
    ))
    page.locator("[data-employee-configuration-model-control]").wait_for()
    assert page.locator("[data-employee-configuration-reasoning-control]").count() == 0

    worker.select_option("codex")
    page.wait_for_function("window.__saveCalls().length === 1")
    assert page.evaluate("window.__saveCalls()[0]") == {
        "employee_backend": "codex",
        "employee_launch_model": None,
        "employee_launch_reasoning_effort": None,
    }
    page.wait_for_function("window.__requests().length === 2")
    worker.select_option("hermes")
    page.wait_for_function("window.__saveCalls().length === 2 && window.__requests().length === 3")
    assert page.evaluate("window.__requests()[1].aborted") is True
    page.evaluate("payload => window.__respond(1, payload)", catalog(
        "codex", None, "codex-native", [("codex-native", "Codex native")], True,
        (("low", "Low"),)
    ))
    page.evaluate("payload => window.__respond(2, payload)", catalog(
        "hermes", None, "hermes-native", [("hermes-native", "Hermes native")]
    ))
    page.locator("[data-employee-configuration-model-control]").wait_for()
    assert setup.get_attribute("data-employee-configuration-backend") == "hermes"
    assert page.locator("[data-employee-configuration-reasoning-control]").count() == 0

    worker.select_option("codex")
    page.wait_for_function("window.__requests().length === 4")
    page.evaluate("window.__fail(3)")
    page.locator("[data-employee-configuration-error]").wait_for()
    assert setup.get_attribute("data-employee-configuration-backend") == "codex"
    assert page.locator("[data-employee-configuration-saved-model]").inner_text() == "model"
    page.locator("[data-employee-configuration-retry]").click()
    page.wait_for_function("window.__requests().length === 5")
    page.evaluate("payload => window.__respond(4, payload)", catalog(
        "codex", None, "codex-native",
        [("codex-native", "Codex native"), ("codex-deep", "Codex deep")],
        True, (("low", "Low"), ("high", "High"))
    ))
    model = page.locator("[data-employee-configuration-model-control] select")
    model.wait_for()
    model.select_option("codex-deep")
    page.wait_for_function("window.__saveCalls().length === 4 && window.__requests().length === 6")
    assert page.evaluate("window.__saveCalls()[3]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-deep",
        "employee_launch_reasoning_effort": None,
    }
    page.evaluate("window.__fail(5)")
    page.locator("[data-employee-configuration-error]").wait_for()
    assert page.locator("[data-employee-configuration-saved-model]").inner_text().endswith("codex-deep")
    page.locator("[data-employee-configuration-retry]").click()
    page.wait_for_function("window.__requests().length === 7")
    assert "candidate_model=codex-deep" in page.evaluate("window.__requests()[6].url")
    page.evaluate("payload => window.__respond(6, payload)", catalog(
        "codex", "codex-deep", "codex-native",
        [("codex-native", "Codex native"), ("codex-deep", "Codex deep")],
        True, (("low", "Low"), ("high", "High"))
    ))
    reasoning = page.locator("[data-employee-configuration-reasoning-control] select")
    reasoning.wait_for()
    reasoning.select_option("high")
    page.wait_for_function("window.__saveCalls().length === 5")
    assert page.evaluate("window.__saveCalls()[4]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-deep",
        "employee_launch_reasoning_effort": "high",
    }

    # No dropdown offers a "default" entry: only the catalog's real options.
    option_labels = page.locator("[data-employee-configuration-setup] option").evaluate_all(
        "options => options.map(option => option.textContent)"
    )
    assert all("default" not in label for label in option_labels)
    model_option_values = model.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    )
    assert model_option_values == ["codex-native", "codex-deep"]

    # Choosing the option equal to the native value stores null ("not pinned").
    model.select_option("codex-native")
    page.wait_for_function("window.__saveCalls().length === 6 && window.__requests().length === 8")
    assert page.evaluate("window.__saveCalls()[5]") == {
        "employee_backend": "codex",
        "employee_launch_model": None,
        "employee_launch_reasoning_effort": "high",
    }
    page.evaluate("payload => window.__respond(7, payload)", catalog(
        "codex", None, "codex-native",
        [("codex-native", "Codex native"), ("codex-deep", "Codex deep")],
        True, (("low", "Low"), ("high", "High"))
    ))
    model.wait_for()
    # Not pinned: the control displays and sits on the backend's concrete native value.
    assert setup.get_attribute("data-employee-configuration-model") == ""
    assert model.input_value() == "codex-native"
    control_text = page.locator("[data-employee-configuration-model-control]").inner_text()
    assert "Codex native" in control_text
    assert "default" not in page.locator("[data-employee-configuration-setup]").inner_text()

    page.evaluate("window.__freeze()")
    page.wait_for_function("document.querySelector('[data-employee-configuration-setup]') === null")
    assert page.locator("text=codex-deep").count() == 0
    assert page.locator("text=high").count() == 0

    # ManagedLaunchDefaults follows the same rules: catalog options only, the
    # native value as the resting point, native selection stored as null.
    page.evaluate("window.__showLaunchDefaults()")
    page.locator("[data-launch-defaults]").wait_for()
    page.wait_for_function("window.__requests().length === 9")
    page.evaluate("payload => window.__respond(8, payload)", catalog(
        "codex", None, "codex-native",
        [("codex-native", "Codex native"), ("codex-deep", "Codex deep")],
        True, (("low", "Low"), ("high", "High"))
    ))
    defaults_model = page.locator('select[aria-label="Coding model"]')
    defaults_reasoning = page.locator('select[aria-label="Coding reasoning"]')
    defaults_model.wait_for()
    # Exactly the catalog's options: no synthetic empty-valued entry, no "default".
    assert defaults_model.locator("option").evaluate_all(
        "options => options.map(option => [option.value, option.textContent])"
    ) == [["codex-native", "Codex native"], ["codex-deep", "Codex deep"]]
    assert defaults_reasoning.locator("option").evaluate_all(
        "options => options.map(option => [option.value, option.textContent])"
    ) == [["low", "Low"], ["high", "High"]]
    assert "default" not in page.locator(
        "[data-launch-defaults] .worker-launch-defaults-controls"
    ).inner_text().lower()
    # Not pinned: the selects sit on the concrete native values.
    assert defaults_model.input_value() == "codex-native"
    assert defaults_reasoning.input_value() == "low"

    # Selecting a non-native option pins it.
    defaults_model.select_option("codex-deep")
    page.wait_for_function(
        "window.__launchDefaultsSaveCalls().length === 1 && window.__requests().length === 10"
    )
    assert page.evaluate("window.__launchDefaultsSaveCalls()[0]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-deep",
        "employee_launch_reasoning_effort": None,
    }
    page.evaluate("payload => window.__respond(9, payload)", catalog(
        "codex", "codex-deep", "codex-native",
        [("codex-native", "Codex native"), ("codex-deep", "Codex deep")],
        True, (("low", "Low"), ("high", "High"))
    ))
    defaults_model.wait_for()
    assert defaults_model.input_value() == "codex-deep"

    # Selecting the option equal to the native value stores null.
    defaults_model.select_option("codex-native")
    page.wait_for_function(
        "window.__launchDefaultsSaveCalls().length === 2 && window.__requests().length === 11"
    )
    assert page.evaluate("window.__launchDefaultsSaveCalls()[1]") == {
        "employee_backend": "codex",
        "employee_launch_model": None,
        "employee_launch_reasoning_effort": None,
    }
    page.evaluate("payload => window.__respond(10, payload)", catalog(
        "codex", None, "codex-native",
        [("codex-native", "Codex native"), ("codex-deep", "Codex deep")],
        True, (("low", "Low"), ("high", "High"))
    ))
    defaults_model.wait_for()
    assert defaults_model.input_value() == "codex-native"
    # Re-selecting the native reasoning while not pinned saves nothing.
    defaults_reasoning.select_option("low")
    assert page.evaluate("window.__launchDefaultsSaveCalls().length") == 2

    # No native value and nothing pinned: the control shows nothing.
    page.evaluate(
        'window.__setLaunchDefaults({ employee_backend: "hermes",'
        ' employee_launch_model: null, employee_launch_reasoning_effort: null })'
    )
    page.wait_for_function("window.__requests().length === 12")
    page.evaluate("payload => window.__respond(11, payload)", catalog(
        "hermes", None, None, [("hermes-a", "Hermes A")]
    ))
    defaults_model.wait_for()
    assert defaults_model.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    ) == ["hermes-a"]
    assert defaults_model.input_value() == ""
    browser.close()

print("employee configuration component assertions passed")
`;
  const browserProbe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserScript, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "";
  browserProbe.stdout.on("data", (chunk) => { output += chunk; });
  browserProbe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => browserProbe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /employee configuration component assertions passed/);
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

console.log("employee-configuration-setup.test.mjs: all assertions passed");
