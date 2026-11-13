/** Run the real production entry against one explicitly prepared private fixture. */
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { mkdir, open, readdir, writeFile } from "node:fs/promises";
import type { FileHandle } from "node:fs/promises";
import { createServer } from "node:net";
import { once } from "node:events";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { create_environment } from "./production_smoke_environment.ts";
import {
  smoke_before_restart,
  smoke_after_restart,
} from "./production_smoke.ts";
import { createReadAccess } from "../../server/core/database/index.ts";
import { load_worker_runtime_definitions } from "../../server/worker_types/configuration.ts";
import { load_ticket_for_write } from "../../server/tickets/data/index.ts";

const source = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const root = resolve(process.argv[2]);
const revision = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: source,
  encoding: "utf8",
}).trim();
await mkdir(join(root, "evidence"), { recursive: true });
const seed = createReadAccess(join(root, "data/planning.db"));
try {
  seed.read((sql) => {
    for (const table of [
      "conversation_background_tasks",
      "deploy_interruptions",
      "deploy_interruption_deliveries",
      "mac_runs",
    ])
      assert.equal(
        sql.get<{ count: number }>(`SELECT COUNT(*) AS count FROM ${table}`)!
          .count,
        0,
        table,
      );
    load_worker_runtime_definitions(sql);
    for (const { id } of sql.all<{ id: string }>("SELECT id FROM tickets"))
      load_ticket_for_write(sql, id);
  });
} finally {
  seed.close();
}
const reservation = createServer();
reservation.listen(0, "127.0.0.1");
await once(reservation, "listening");
const address = reservation.address();
assert(address && typeof address !== "string");
const base = `http://127.0.0.1:${address.port}`;
await new Promise<void>((resolve, reject) =>
  reservation.close((error) => (error ? reject(error) : resolve())),
);
const environment = await create_environment(root, source, base, revision);
const steps: string[] = [];
const step = (message: string) => {
  steps.push(message);
  console.log(`${steps.length}. ${message}`);
};
let server: ReturnType<typeof spawn> | undefined;
let log: FileHandle | undefined;
async function start() {
  log = await open(join(root, "evidence/server.log"), "a");
  server = spawn(process.execPath, ["server/main.ts"], {
    cwd: source,
    env: environment,
    stdio: ["ignore", log.fd, log.fd],
  });
  await writeFile(join(root, "evidence/server-pid"), String(server.pid));
  const ready = AbortSignal.timeout(30_000);
  while (true) {
    if (server.exitCode !== null || server.signalCode !== null)
      throw new Error(
        `Production server exited: ${server.exitCode ?? server.signalCode}`,
      );
    ready.throwIfAborted();
    try {
      const response = await fetch(
        `${base}/api/health?expected_sha=${revision}`,
        { signal: ready },
      );
      if (response.ok) break;
    } catch (error) {
      ready.throwIfAborted();
      if (!(error instanceof TypeError)) throw error;
    }
    await delay(100, undefined, { signal: ready });
  }
}
async function stop() {
  if (server && server.exitCode === null && server.signalCode === null) {
    const exited = once(server, "exit", {
      signal: AbortSignal.timeout(45_000),
    });
    server.kill("SIGTERM");
    const [code, signal] = await exited;
    assert.equal(code, 0, `Production shutdown: ${code ?? signal}`);
  }
  server = undefined;
  await log?.close();
  log = undefined;
}
async function web() {
  const python = (await readdir(join(source, ".venv/lib"))).find((name) =>
    name.startsWith("python"),
  )!;
  const driver = join(
    source,
    ".venv/lib",
    python,
    "site-packages/playwright/driver/package/index.mjs",
  );
  const { chromium } = await import(pathToFileURL(driver).href);
  const browser = await chromium.launch({ headless: true, env: environment });
  try {
    const page = await browser.newPage();
    const errors: string[] = [];
    page.on("pageerror", (error: Error) => errors.push(error.message));
    await page.goto(base, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page
      .getByText("Navigation 2", { exact: true })
      .filter({ visible: true })
      .first()
      .waitFor();
    await page.screenshot({
      path: join(root, "evidence/workspace.png"),
      fullPage: true,
    });
    assert.deepEqual(errors, [], "Production web UI JavaScript errors");
    step("Real web UI loads the populated synthetic workspace");
  } finally {
    await browser.close();
  }
}
try {
  await start();
  step("Production server reaches expected-SHA health");
  await web();
  const state = await smoke_before_restart(base, environment, {
    step,
    workspace: join(root, "workspace"),
    model: "TEST",
    reply: "Production smoke reply.",
  });
  await stop();
  step("Production server stops cleanly");
  await start();
  step("Production server restarts against the same private database");
  await smoke_after_restart(base, state, environment, step);
  await stop();
  step("Restarted production server stops cleanly");
  await writeFile(
    join(root, "evidence/smoke.json"),
    JSON.stringify({ revision, base, steps, passed: true }, null, 2) + "\n",
  );
} catch (error) {
  await writeFile(
    join(root, "evidence/smoke.json"),
    JSON.stringify(
      { revision, base, steps, passed: false, error: String(error) },
      null,
      2,
    ) + "\n",
  );
  throw error;
} finally {
  await stop();
}
