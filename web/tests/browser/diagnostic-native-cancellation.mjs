/** Focused controls for native cancellation premises. Never emits a catalog receipt. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { markdownWorkerPlugins } from "../../build/markdown-worker.mjs";
import { ownedByProcess, scratchDirectory } from "../support/scratch.mjs";
import { prepare } from "./cases/live-conversation-unanswered-send.mjs";

const controls = ["clean", "zero-tail", "positive-tail", "late-wheel", "late-acceptance", "broken-wheel", "broken-supersession"];
const webRoot = fileURLToPath(new URL("../../", import.meta.url));
const repositoryRoot = dirname(webRoot.replace(/\/$/, ""));
const selections = [];
let report;
for (let index = 2; index < process.argv.length; index++) {
  const argument = process.argv[index];
  if (argument === "--report") {
    assert.ok(process.argv[index + 1], "Missing --report path");
    report = resolve(process.argv[++index]);
  } else {
    assert.ok(controls.includes(argument), `Unknown diagnostic control: ${argument}`);
    assert.ok(!selections.includes(argument), `Duplicate diagnostic control: ${argument}`);
    selections.push(argument);
  }
}
if (!selections.length) selections.push(...controls);

// A disposable Vite transform changes every call path through the one writer.
// Neither the checkout nor a normal diagnostic build contains this mutation.
function disableAcceptedSendSupersession() {
  let mutations = 0;
  return {
    name: "diagnostic-disable-accepted-send-supersession",
    enforce: "pre",
    transform(source, id) {
      if (!id.endsWith("/conversation/viewport/ConversationViewport.svelte")) return;
      const start = "  function supersedeRestorationWithAcceptedSend(): void {";
      const end = "  function stopAutomaticTravel(): void {";
      const first = source.indexOf(start), last = source.indexOf(end, first);
      assert.ok(first >= 0 && last > first, "Diagnostic supersession writer moved");
      const body = source.slice(first, last);
      assert.ok(body.includes("latestIntent.controller?.abort()") && body.includes("cancelTextRestoration()"), "Diagnostic supersession writer changed");
      mutations++;
      return {code: source.slice(0, first) + body.replace(start, start + "\n    if ((window as any).__nativeDisableSupersession) return;") + source.slice(last), map: null};
    },
    buildEnd(error) {
      if (!error) assert.equal(mutations, 1, "Diagnostic supersession mutation did not run exactly once");
    },
  };
}

const temporary = await mkdtemp(join(tmpdir(), "panels-native-cancellation-"));
report ||= join(tmpdir(), `${temporary.split(sep).at(-1)}.json`);
const evidence = report.replace(/\.json$/, "") + "-evidence";
await mkdir(evidence, {recursive: true});
const scratchRoot = await scratchDirectory();
const before = new Set(await readdir(scratchRoot));
let server;
try {
  const prepared = await prepare({scratchRoot});
  const variants = new Set(selections.map(control => control === "broken-supersession" ? "broken" : "normal"));
  for (const variant of variants) {
    await build({root: webRoot, base: `/${variant}/`, configFile: false, logLevel: "warn",
      plugins: [...(variant === "broken" ? [disableAcceptedSendSupersession()] : []), svelte()],
      worker: {plugins: markdownWorkerPlugins},
      build: {outDir: join(temporary, variant), emptyOutDir: true, rollupOptions: {input: prepared.inputs}}});
  }
  const types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2"};
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
      const path = resolve(temporary, `.${pathname}`);
      assert.ok(path.startsWith(temporary + sep), "Invalid diagnostic static path");
      const data = await readFile(path);
      response.writeHead(200, {"Content-Type": types[extname(path)] || "application/octet-stream"});
      response.end(data);
    } catch {response.writeHead(404); response.end("Not found");}
  });
  await new Promise((resolve, reject) => {server.once("error", reject); server.listen(0, "127.0.0.1", resolve);});
  const origin = `http://127.0.0.1:${server.address().port}`;
  const manifest = {python: prepared.python, evidence, controls: selections.map(control => ({control,
    urls: prepared.arguments.map(path => `${origin}/${control === "broken-supersession" ? "broken" : "normal"}/${relative(webRoot, path).split(sep).join("/")}`)}))};
  const manifestPath = join(temporary, "diagnostics.json");
  await writeFile(manifestPath, JSON.stringify(manifest));
  const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(PLAN_|PANELS_REAL_|PANELS_BROWSER_|PANELS_NATIVE_CANCELLATION_CONTROL$)/.test(key) && !["HERMES_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"].includes(key)));
  await new Promise((resolve, reject) => {
    const child = spawn(process.env.PANELS_TEST_PYTHON || join(repositoryRoot, ".venv/bin/python"),
      [fileURLToPath(new URL("./diagnostic-native-cancellation.py", import.meta.url)), manifestPath, report],
      {cwd: repositoryRoot, env: environment, stdio: "inherit"});
    child.once("error", reject);
    child.once("exit", (code, signal) => code === 0 ? resolve() : reject(new Error(`Native cancellation diagnostic exited ${code ?? signal}; evidence: ${report}`)));
  });
  console.log(`Native cancellation diagnostic evidence: ${report}`);
} finally {
  if (server) {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));}
  await rm(temporary, {recursive: true, force: true});
  for (const name of await readdir(scratchRoot)) {
    if (!before.has(name) && ownedByProcess(name)) await rm(join(scratchRoot, name), {recursive: true, force: true});
  }
}
