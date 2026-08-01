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
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-worker-configuration-"));
const hostPath = join(webRoot, "tests", `.worker-configuration-host-${process.pid}.svelte`);
const mainPath = join(webRoot, "tests", `.worker-configuration-main-${process.pid}.ts`);
const indexPath = join(webRoot, "tests", `.worker-configuration-index-${process.pid}.html`);
let serverProcess;

// Both configuration screens read what the backends on this machine are from the one
// place that answers it, and neither knows the name of a single backend: the list, the
// models and the efforts all arrive together, so a fourth agent needs no edit here.
for (const fileName of ["WorkerConfigurationSetup.svelte", "ManagedLaunchDefaults.svelte"]) {
  const source = await readFile(
    new URL(`../src/components/${fileName}`, import.meta.url),
    "utf8",
  );
  assert.match(source, /employee_launch_model/, fileName);
  assert.match(source, /employee_launch_reasoning_effort/, fileName);
  assert.match(source, /readBackends/, fileName);
  assert.match(source, /lib\/conversation\/wire/, fileName);
  assert.match(source, /effortOptionsFor/, fileName);
  assert.match(source, /requestGeneration/, fileName);
  assert.doesNotMatch(source, /employee-configuration-catalog/, fileName);
  assert.doesNotMatch(source, /["'](?:hermes|codex|claude(?: code)?)["']/i, fileName);
}
const setupSource = await readFile(
  new URL("../src/components/WorkerConfigurationSetup.svelte", import.meta.url),
  "utf8",
);
assert.match(setupSource, /data-employee-configuration-retry/);

try {
  await writeFile(hostPath, `
<script lang="ts">
  import WorkerConfigurationSetup from "../src/components/WorkerConfigurationSetup.svelte";
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
  <WorkerConfigurationSetup
    ticketId="ticket-ui"
    employeeBackend={saved.employee_backend}
    employeeLaunchModel={saved.employee_launch_model}
    employeeLaunchReasoningEffort={saved.employee_launch_reasoning_effort}
    {onSave}
  />
{/if}

{#if showLaunchDefaults}
  <ManagedLaunchDefaults
    label="Coding"
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

def model(model_id, display_name, efforts=()):
    return {
        "model_id": model_id,
        "display_name": display_name,
        "detail": None,
        "reasoning_effort_options": list(efforts),
    }

def backend(key, models, efforts=(), default_model=None, default_effort=None):
    return {
        "backend_key": key,
        "installed": True,
        "executable_path": "/probe/" + key,
        "version": "1.0.0",
        "identity": None,
        "available_models": list(models),
        "reasoning_effort_options": list(efforts),
        "default_model_id": default_model,
        "default_reasoning_effort": default_effort,
        "update_advisory": None,
        "diagnoses": [],
    }

def machine():
    return {
        "backends": [
            backend(
                "hermes",
                [model("openai-codex:gpt-5.6-sol", "GPT-5.6 Sol")],
                default_model="openai-codex:gpt-5.6-sol",
            ),
            backend(
                "codex",
                [
                    model("codex-native", "Codex native", ("low", "high")),
                    model("codex-deep", "Codex deep", ("low", "high")),
                    # A model that says it takes no effort at all is believed, which is
                    # what makes effort a fact about the model rather than the backend.
                    model("codex-plain", "Codex plain"),
                ],
                ("low", "high"),
                default_model="codex-native",
                default_effort="low",
            ),
            # A backend that names no model of its own: the control shows nothing rather
            # than inventing a word for it.
            backend("claude", [model("claude-a", "Claude A")]),
        ]
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

    # One read of the machine's agents, not one per backend and not one per model.
    assert page.evaluate("window.__requests().length") == 1
    assert page.evaluate("window.__requests()[0].url").endswith("/api/conversation/backends")

    # A machine that will not answer leaves the saved values on show, and a way back.
    page.evaluate("window.__fail(0)")
    page.locator("[data-employee-configuration-error]").wait_for()
    assert page.locator("[data-employee-configuration-saved-model]").inner_text() == "model"
    assert page.locator("[data-employee-configuration-refresh]").count() == 0
    page.locator("[data-employee-configuration-retry]").click()
    page.wait_for_function("window.__requests().length === 2")
    assert "refresh=true" in page.evaluate("window.__requests()[1].url")
    page.evaluate("payload => window.__respond(1, payload)", machine())

    page.locator("[data-employee-configuration-model-control]").wait_for()
    # hermes advertises no reasoning effort, so there is no reasoning control at all.
    assert page.locator("[data-employee-configuration-reasoning-control]").count() == 0
    assert worker.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    ) == ["hermes", "codex", "claude"]

    # Changing worker is a save, and it asks the machine nothing: one read covers all three.
    # The new backend's own model comes with it, out of the answer already in hand — a
    # model id belongs to the backend that named it, and a Ticket saved naming none would
    # launch its worker on whatever the backend picked for itself.
    worker.select_option("codex")
    page.wait_for_function("window.__saveCalls().length === 1")
    assert page.evaluate("window.__saveCalls()[0]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-native",
        "employee_launch_reasoning_effort": None,
    }
    assert page.evaluate("window.__requests().length") == 2
    assert setup.get_attribute("data-employee-configuration-backend") == "codex"

    model_select = page.locator("[data-employee-configuration-model-control] select")
    reasoning = page.locator("[data-employee-configuration-reasoning-control] select")
    reasoning.wait_for()
    # Nothing pinned: both controls rest on the backend's own concrete values.
    assert model_select.input_value() == "codex-native"
    assert reasoning.input_value() == "low"

    model_select.select_option("codex-deep")
    page.wait_for_function("window.__saveCalls().length === 2")
    assert page.evaluate("window.__saveCalls()[1]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-deep",
        "employee_launch_reasoning_effort": None,
    }
    reasoning.select_option("high")
    page.wait_for_function("window.__saveCalls().length === 3")
    assert page.evaluate("window.__saveCalls()[2]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-deep",
        "employee_launch_reasoning_effort": "high",
    }

    # No dropdown offers a "default" entry: only the machine's real options.
    option_labels = page.locator("[data-employee-configuration-setup] option").evaluate_all(
        "options => options.map(option => option.textContent)"
    )
    assert all("default" not in label for label in option_labels)
    assert model_select.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    ) == ["codex-native", "codex-deep", "codex-plain"]

    # A model that takes no effort takes the control away with it.
    model_select.select_option("codex-plain")
    page.wait_for_function("window.__saveCalls().length === 4")
    page.locator("[data-employee-configuration-reasoning-control]").wait_for(state="detached")

    # The option that happens to be the one this backend runs is a model like any other,
    # and choosing it stores its name. There is no longer a choice that means "whichever
    # one the backend feels like", so nothing here turns a name back into nothing.
    model_select.select_option("codex-native")
    page.wait_for_function("window.__saveCalls().length === 5")
    assert page.evaluate("window.__saveCalls()[4]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-native",
        "employee_launch_reasoning_effort": "high",
    }
    reasoning.wait_for()
    assert setup.get_attribute("data-employee-configuration-model") == "codex-native"
    assert model_select.input_value() == "codex-native"
    control_text = page.locator("[data-employee-configuration-model-control]").inner_text()
    assert "Codex native" in control_text
    assert "default" not in page.locator("[data-employee-configuration-setup]").inner_text()

    # A backend this machine reported no model for has none to bring. The save goes out
    # naming none, which the server refuses — that is the honest end of it, and it is not
    # papered over here by choosing something nobody could see.
    worker.select_option("claude")
    page.wait_for_function("window.__saveCalls().length === 6")
    assert page.evaluate("window.__saveCalls()[5]") == {
        "employee_backend": "claude",
        "employee_launch_model": None,
        "employee_launch_reasoning_effort": None,
    }
    worker.select_option("codex")
    page.wait_for_function("window.__saveCalls().length === 7")

    page.locator("[data-employee-configuration-refresh]").click()
    page.wait_for_function("window.__requests().length === 3")
    assert "refresh=true" in page.evaluate("window.__requests()[2].url")
    page.evaluate("payload => window.__respond(2, payload)", machine())
    model_select.wait_for()

    page.evaluate("window.__freeze()")
    page.wait_for_function("document.querySelector('[data-employee-configuration-setup]') === null")
    assert page.locator("text=codex-deep").count() == 0
    assert page.locator("text=high").count() == 0

    # ManagedLaunchDefaults follows the same rules: the machine's options only, the value
    # the backend runs as the resting point, and every selection stored under its name.
    page.evaluate("window.__showLaunchDefaults()")
    page.locator("[data-launch-defaults]").wait_for()
    page.wait_for_function("window.__requests().length === 4")
    page.evaluate("payload => window.__respond(3, payload)", machine())
    defaults_model = page.locator('select[aria-label="Coding model"]')
    defaults_reasoning = page.locator('select[aria-label="Coding reasoning"]')
    defaults_model.wait_for()
    assert defaults_model.locator("option").evaluate_all(
        "options => options.map(option => [option.value, option.textContent])"
    ) == [
        ["codex-native", "Codex native"],
        ["codex-deep", "Codex deep"],
        ["codex-plain", "Codex plain"],
    ]
    # An effort is the word the backend uses for it, shown as itself — the same way the
    # conversation composer shows it, so one value is not two names in two places.
    assert defaults_reasoning.locator("option").evaluate_all(
        "options => options.map(option => [option.value, option.textContent])"
    ) == [["low", "low"], ["high", "high"]]
    assert "default" not in page.locator(
        "[data-launch-defaults] .worker-launch-defaults-controls"
    ).inner_text().lower()
    assert defaults_model.input_value() == "codex-native"
    assert defaults_reasoning.input_value() == "low"

    # Selecting a non-native option pins it, and asks the machine nothing.
    defaults_model.select_option("codex-deep")
    page.wait_for_function("window.__launchDefaultsSaveCalls().length === 1")
    assert page.evaluate("window.__launchDefaultsSaveCalls()[0]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-deep",
        "employee_launch_reasoning_effort": None,
    }
    assert page.evaluate("window.__requests().length") == 4
    assert defaults_model.input_value() == "codex-deep"

    # And back: the option this backend happens to run is a name like any other here too.
    defaults_model.select_option("codex-native")
    page.wait_for_function("window.__launchDefaultsSaveCalls().length === 2")
    assert page.evaluate("window.__launchDefaultsSaveCalls()[1]") == {
        "employee_backend": "codex",
        "employee_launch_model": "codex-native",
        "employee_launch_reasoning_effort": None,
    }
    # Re-selecting the native reasoning while not pinned saves nothing.
    defaults_reasoning.select_option("low")
    assert page.evaluate("window.__launchDefaultsSaveCalls().length") == 2

    # Changing the backend here works the way it does on a Ticket: the new backend's own
    # model comes with it, so these workers launch on something somebody can point at.
    page.locator('select[aria-label="Coding backend"]').select_option("hermes")
    page.wait_for_function("window.__launchDefaultsSaveCalls().length === 3")
    assert page.evaluate("window.__launchDefaultsSaveCalls()[2]") == {
        "employee_backend": "hermes",
        "employee_launch_model": "openai-codex:gpt-5.6-sol",
        "employee_launch_reasoning_effort": None,
    }

    # No native value and nothing pinned: the control shows nothing.
    page.evaluate(
        'window.__setLaunchDefaults({ employee_backend: "claude",'
        ' employee_launch_model: null, employee_launch_reasoning_effort: null })'
    )
    page.wait_for_function(
        "document.querySelector('select[aria-label=\"Coding model\"]').value === ''"
    )
    assert defaults_model.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    ) == ["claude-a"]

    # A saved value the machine no longer offers is shown as itself, and refused.
    page.evaluate(
        'window.__setLaunchDefaults({ employee_backend: "codex",'
        ' employee_launch_model: "codex-gone", employee_launch_reasoning_effort: "extreme" })'
    )
    unavailable_model = defaults_model.locator('option[value="codex-gone"]')
    unavailable_reasoning = defaults_reasoning.locator('option[value="extreme"]')
    unavailable_model.wait_for(state="attached")
    unavailable_reasoning.wait_for(state="attached")
    assert unavailable_model.get_attribute("disabled") is not None
    assert unavailable_model.inner_text().endswith("unavailable")
    assert unavailable_reasoning.get_attribute("disabled") is not None
    assert unavailable_reasoning.inner_text().endswith("unavailable")

    page.locator("[data-launch-defaults-refresh]").click()
    page.wait_for_function("window.__requests().length === 5")
    assert "refresh=true" in page.evaluate("window.__requests()[4].url")
    page.evaluate("payload => window.__respond(4, payload)", machine())
    unavailable_model.wait_for(state="attached")
    assert unavailable_model.get_attribute("disabled") is not None
    assert unavailable_reasoning.get_attribute("disabled") is not None
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

console.log("worker-configuration-setup.test.mjs: all assertions passed");
