import { usageAliases } from "../../usage-aliases.mjs";
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, mkdir, open, readFile, rm, writeFile, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";
import { build } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { markdownWorkerPlugins } from "../../build/markdown-worker.mjs";
import { scratchDirectory, ownedByProcess } from "../support/scratch.mjs";
import { assertCompleteCaseEnvironment, selectedEntries, validateCompanion } from "./contracts.mjs";

const webRoot = fileURLToPath(new URL("../../", import.meta.url));
const repositoryRoot = dirname(webRoot.replace(/\/$/, ""));
const catalog = JSON.parse(await readFile(new URL("./catalog.json", import.meta.url), "utf8"));
// Cases run side by side in this many Chromium processes. GitHub's hosted runners have
// four vCPUs, and the longest case alone sets the lower limit on the wall clock.
const browserProcesses = 4;
// The measured longest cases start first, so none of them starts late and ends the run late.
const longestCases = ["conversation-pane", "goal-preview", "workspace-utility-pane"];
const longestFirst = identity => longestCases.includes(identity) ? longestCases.indexOf(identity) : longestCases.length;

export function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const process = spawn(command, args, {cwd: webRoot, stdio: "inherit", ...options});
    process.once("error", reject);
    process.once("exit", (code, signal) => code === 0 ? resolve() : reject(new Error(`${command} exited ${code ?? signal}`)));
  });
}

function cleanEnvironment() {
  return Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(PLAN_|PANELS_REAL_)/.test(key) && !["HERMES_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"].includes(key)));
}

function printScope(entries, receipt, requiredCompanions, selections) {
  const cases = Array.isArray(receipt.cases) ? receipt.cases.filter(case_ => case_ && typeof case_ === "object") : [];
  console.log(`\nPARTIAL ${receipt.outcome === "passed" ? "PASS" : "FAIL"} — browser entries: ${entries.map(entry => entry.id).join(", ") || selections.join(", ") || "none"}`);
  console.log(`EXECUTED cases: ${cases.map(case_ => `${case_.id} (${case_.status})`).join(", ") || "none"}`);
  const executed = new Set(cases.map(case_ => case_.id));
  console.log(`NOT RUN selected cases: ${entries.flatMap(entry => entry.cases).filter(id => !executed.has(id)).join(", ") || "none"}`);
  if (receipt.companion) console.log(`PASS: Node companion runtime (${requiredCompanions.join(", ")}) and TypeScript test project compiler`);
  else if (requiredCompanions.length) console.log("NOT PROVED: required Node companion runtime and TypeScript test project compiler");
  console.log(`NOT RUN: ${catalog.entries.filter(entry => !entries.includes(entry)).map(entry => entry.id).join(", ") || "no omitted browser entries"}; ${receipt.companion ? "other Vitest runtime tests" : requiredCompanions.length ? "other Vitest runtime tests" : "Vitest runtime and test compiler"}, Python checks, Svelte checks, production build. This result is not a suite-wide pass.`);
}

export async function run(selections = [], options = {}) {
  let entries = [], requiredCompanions = [];
  const receipt = {outcome: "failed", cases: [], companion: null};
  try {
    entries = selectedEntries(catalog, selections);
    assertCompleteCaseEnvironment(process.env);
    requiredCompanions = [...new Set(entries.flatMap(entry => entry.companions || []))];
    return await runSelected(entries, options, receipt, requiredCompanions);
  } catch (error) {
    receipt.outcome = "failed";
    throw error;
  } finally {
    printScope(entries, receipt, requiredCompanions, selections);
  }
}

async function runSelected(entries, options, receipt, requiredCompanions) {
  const started = performance.now();
  const invocationId = options.invocationId || randomUUID();
  const revision = execFileSync("git", ["rev-parse", "HEAD"], {cwd: repositoryRoot, encoding: "utf8"}).trim();
  assert.ok(!options.revision || options.revision === revision, "Requested browser revision differs from checkout");
  const environment = cleanEnvironment();
  const temporary = await mkdtemp(join(tmpdir(), "panels-browser-"));
  const evidence = options.report ? join(dirname(resolve(options.report)), `browser-evidence-${invocationId}`) : join(temporary, "evidence");
  await mkdir(evidence, {recursive: true});
  environment.PANELS_SIDEBAR_ARTIFACT_DIR ||= join(evidence, "rail");
  environment.PANELS_E2E_FAILURE_DIR ||= join(evidence, "failure");
  const scratchRoot = await scratchDirectory();
  const before = new Set(await readdir(scratchRoot));
  let server;
  let completed = false;
  Object.assign(receipt, {schema: 1, revision, invocationId, selected: entries.map(entry => entry.path), timing: {}});
  let primaryError;
  try {
    const companionStarted = performance.now();
    if (requiredCompanions.length) {
      const companionPath = join(temporary, "companion.json");
      await runCommand(process.execPath, [join(webRoot, "node_modules/vitest/vitest.mjs"), "run", "--typecheck", ...requiredCompanions.map(file => file.replace(/^web\//, ""))], {
        env: {...environment, PANELS_COMPANION_REPORT: companionPath, PANELS_BROWSER_INVOCATION: invocationId, PANELS_BROWSER_REVISION: revision},
      });
      receipt.companion = validateCompanion(JSON.parse(await readFile(companionPath, "utf8")), invocationId, revision, requiredCompanions);
    }
    receipt.timing.companionSeconds = (performance.now() - companionStarted) / 1000;
    const prepared = [];
    const prepareStarted = performance.now();
    for (const entry of entries) {
      for (const identity of entry.cases) {
        const module = await import(`./cases/${identity}.mjs`);
        const result = await module.prepare({scratchRoot});
        assert.ok(result.inputs?.length && result.arguments?.length && result.python, `Invalid browser case: ${identity}`);
        prepared.push({...result, id: identity, entry: entry.id});
      }
    }
    receipt.timing.prepareSeconds = (performance.now() - prepareStarted) / 1000;
    const output = join(temporary, "dist");
    const buildStarted = performance.now();
    await build({root: webRoot, base: "/", configFile: false, resolve: { alias: usageAliases }, logLevel: "warn", plugins: [svelte()], worker: { plugins: markdownWorkerPlugins },
      build: {outDir: output, emptyOutDir: true, rollupOptions: {input: prepared.flatMap(case_ => case_.inputs)}}});
    receipt.timing.sharedBuildSeconds = (performance.now() - buildStarted) / 1000;
    const serverStarted = performance.now();
    const types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2"};
    const resources = new Map(prepared.flatMap(case_ => (case_.resources || []).map(resource => [resource.path, resource])));
    server = createServer(async (request, response) => {
      try {
        const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
        const resource = resources.get(pathname);
        if (resource) { response.writeHead(200, {"Content-Type": resource.contentType}); response.end(resource.body); return; }
        const path = resolve(output, `.${pathname}`);
        assert.ok(path.startsWith(output + sep), "Invalid static path");
        const data = await readFile(path);
        response.writeHead(200, {"Content-Type": types[extname(path)] || "application/octet-stream"});
        response.end(data);
      } catch { response.writeHead(404); response.end("Not found"); }
    });
    await new Promise((resolve, reject) => {server.once("error", reject); server.listen(0, "127.0.0.1", resolve);});
    receipt.timing.sharedServerSeconds = (performance.now() - serverStarted) / 1000;
    const origin = `http://127.0.0.1:${server.address().port}`;
    const urls = case_ => case_.arguments.map(path => `${origin}/${relative(webRoot, path).split(sep).join("/")}`);
    const results = new Map();
    const processFailures = [];
    let browserStartupSeconds = 0;
    const runCase = async case_ => {
      // Each case gets its own Chromium process, evidence folders, and log, so cases running
      // side by side share nothing but the read-only build and its static server.
      const manifestPath = join(temporary, `${case_.id}.case.json`);
      const resultsPath = join(temporary, `${case_.id}.result.json`);
      const logPath = join(evidence, "logs", `${case_.id}.log`);
      await writeFile(manifestPath, JSON.stringify({id: case_.id, entry: case_.entry, python: case_.python, urls: urls(case_)}));
      const log = await open(logPath, "w");
      try {
        await runCommand(process.env.PANELS_TEST_PYTHON || join(repositoryRoot, ".venv/bin/python"), [fileURLToPath(new URL("./execute.py", import.meta.url)), manifestPath, resultsPath], {
          cwd: repositoryRoot, stdio: ["ignore", log.fd, log.fd],
          env: {...environment, PYTHONUNBUFFERED: "1",
            PANELS_SIDEBAR_ARTIFACT_DIR: join(environment.PANELS_SIDEBAR_ARTIFACT_DIR, case_.id),
            PANELS_E2E_FAILURE_DIR: join(environment.PANELS_E2E_FAILURE_DIR, case_.id)}});
      } catch (error) {
        processFailures.push(`${case_.id}: ${error.message}`);
      } finally {
        await log.close();
      }
      // One block per case, printed when it ends, keeps a failure readable.
      console.log(`\n===== browser case ${case_.entry}/${case_.id} =====\n${(await readFile(logPath, "utf8")).trimEnd()}`);
      try {
        const result = JSON.parse(await readFile(resultsPath, "utf8"));
        results.set(case_.id, result.case);
        browserStartupSeconds += result.browserStartupSeconds;
      } catch { /* Missing proof remains invalid and the process failure survives. */ }
    };
    await mkdir(join(evidence, "logs"), {recursive: true});
    const queue = [...prepared].sort((left, right) => longestFirst(left.id) - longestFirst(right.id));
    await Promise.all(Array.from({length: Math.min(browserProcesses, queue.length)}, async () => {
      while (queue.length) await runCase(queue.shift());
    }));
    receipt.cases = prepared.filter(case_ => results.has(case_.id)).map(case_ => results.get(case_.id));
    receipt.timing.browserProcesses = Math.min(browserProcesses, prepared.length);
    receipt.timing.browserStartupSeconds = browserStartupSeconds;
    assert.equal(receipt.cases.length, prepared.length, "Browser receipt omitted cases");
    assert.ok(receipt.cases.every(case_ => case_.status === "passed"), "Browser case failed");
    assert.deepEqual(processFailures, [], "Browser case process failed");
    completed = true;
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    const teardownStarted = performance.now();
    let teardownError;
    try {
      if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
      await rm(temporary, {recursive: true, force: true});
      for (const name of await readdir(scratchRoot)) {
        if (!before.has(name) && ownedByProcess(name)) await rm(join(scratchRoot, name), {recursive: true, force: true});
      }
    } catch (error) {
      teardownError = error;
      receipt.teardownError = String(error);
    } finally {
      receipt.outcome = completed && !teardownError ? "passed" : "failed";
      receipt.timing.teardownSeconds = (performance.now() - teardownStarted) / 1000;
      receipt.timing.invocationSeconds = (performance.now() - started) / 1000;
      if (options.report) {
        try { await writeFile(options.report, JSON.stringify(receipt, null, 2) + "\n"); }
        catch (error) {
          if (!primaryError && !teardownError) throw error;
          console.error("Browser report failed:", error);
        }
      }
    }
    if (teardownError) {
      if (!primaryError) throw teardownError;
      console.error("Browser cleanup failed:", teardownError);
    }
  }
  return receipt;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const selections = [];
  const options = {};
  try {
    for (let index = 2; index < process.argv.length; index++) {
      const argument = process.argv[index];
      if (["--report", "--revision", "--invocation-id"].includes(argument)) {
        assert.ok(process.argv[index + 1], `Missing argument for ${argument}`);
        options[argument === "--invocation-id" ? "invocationId" : argument.slice(2)] = process.argv[++index];
      } else selections.push(argument);
    }
  } catch (error) {
    printScope([], {outcome: "failed", cases: [], companion: null}, [], selections);
    throw error;
  }
  await run(selections, options);
}
