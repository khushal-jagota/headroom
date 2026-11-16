/** Run the real production entry against one explicitly prepared private fixture. */
import assert from 'node:assert/strict';
import { spawn, execFile, execFileSync } from 'node:child_process';
import { mkdir, open, readFile, readdir, realpath, writeFile } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { createServer } from 'node:net';
import { once } from 'node:events';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { parseArgs, promisify } from 'node:util';
import { create_environment, create_seed_environment } from './smoke/environment.ts';
import { smoke_before_restart, smoke_after_restart } from './smoke/steps.ts';
import { createReadAccess } from '../core/database/index.ts';
import { register_test_root } from '../core/test_paths.ts';
import { load_worker_runtime_definitions } from '../worker_types/configuration.ts';
import { load_ticket_for_write } from '../tickets/data/index.ts';

const source = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { values } = parseArgs({
  options: { 'app-root': { type: 'string' }, 'state-root': { type: 'string' } },
  allowPositionals: false,
});
assert.ok(values['app-root'] && values['state-root'], '--app-root and --state-root are required');
const app_root = await realpath(resolve(values['app-root']));
const root = await realpath(resolve(values['state-root']));
assert.deepEqual(await readdir(root), [], '--state-root must be an empty private folder');
const unregister = register_test_root(root);
let manifest: string | undefined;
try {
  manifest = await readFile(join(app_root, 'manifest.json'), 'utf8');
} catch (error) {
  if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
}
const revision: unknown =
  manifest === undefined
    ? execFileSync('git', ['-C', app_root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
    : (JSON.parse(manifest) as { app_sha: unknown }).app_sha;
assert.ok(
  typeof revision === 'string' && /^[a-f0-9]{40}$/.test(revision),
  'Invalid application SHA',
);
const execute = promisify(execFile);
const seeded = await execute(
  join(source, '.venv/bin/python'),
  ['-m', 'tests.support.production_smoke_seed', '--root', root],
  {
    cwd: source,
    env: await create_seed_environment(root, source),
    timeout: 120_000,
    maxBuffer: 1024 * 1024,
  },
);
console.log(seeded.stdout.trim());
await mkdir(join(root, 'evidence'), { recursive: true });
const seed = createReadAccess(join(root, 'data/planning.db'));
try {
  seed.read((sql) => {
    for (const table of [
      'conversation_background_tasks',
      'deploy_interruptions',
      'deploy_interruption_deliveries',
      'mac_runs',
    ])
      assert.equal(
        sql.get<{ count: number }>(`SELECT COUNT(*) AS count FROM ${table}`)!.count,
        0,
        table,
      );
    load_worker_runtime_definitions(sql);
    for (const { id } of sql.all<{ id: string }>('SELECT id FROM tickets'))
      load_ticket_for_write(sql, id);
  });
} finally {
  seed.close();
}
const reservation = createServer();
reservation.listen(0, '127.0.0.1');
await once(reservation, 'listening');
const address = reservation.address();
assert(address && typeof address !== 'string');
const base = `http://127.0.0.1:${address.port}`;
await new Promise<void>((resolve, reject) =>
  reservation.close((error) => (error ? reject(error) : resolve())),
);
const environment = await create_environment(root, source, app_root, base, revision);
const steps: string[] = [];
const step = (message: string) => {
  steps.push(message);
  console.log(`${steps.length}. ${message}`);
};
let server: ReturnType<typeof spawn> | undefined;
let log: FileHandle | undefined;
async function start() {
  log = await open(join(root, 'evidence/server.log'), 'a');
  server = spawn(process.execPath, [join(app_root, 'server/main.ts')], {
    cwd: app_root,
    env: environment,
    stdio: ['ignore', log.fd, log.fd],
  });
  await writeFile(join(root, 'evidence/server-pid'), String(server.pid));
  const ready = AbortSignal.timeout(30_000);
  while (true) {
    if (server.exitCode !== null || server.signalCode !== null)
      throw new Error(`Production server exited: ${server.exitCode ?? server.signalCode}`);
    ready.throwIfAborted();
    try {
      const response = await fetch(`${base}/api/health?expected_sha=${revision}`, {
        signal: ready,
      });
      if (response.ok) {
        const meta = await fetch(`${base}/api/meta`, { signal: ready });
        assert.equal(meta.status, 200);
        assert.equal(((await meta.json()) as { test_mode: unknown }).test_mode, false);
        break;
      }
    } catch (error) {
      ready.throwIfAborted();
      if (!(error instanceof TypeError)) throw error;
    }
    await delay(100, undefined, { signal: ready });
  }
}
async function stop() {
  if (server && server.exitCode === null && server.signalCode === null) {
    const exited = once(server, 'exit', {
      signal: AbortSignal.timeout(45_000),
    });
    server.kill('SIGTERM');
    let outcome;
    try {
      outcome = await exited;
    } catch (error) {
      if (server.exitCode === null && server.signalCode === null) {
        const killed = once(server, 'exit', { signal: AbortSignal.timeout(5_000) });
        server.kill('SIGKILL');
        await killed;
      }
      throw error;
    }
    const [code, signal] = outcome;
    assert.equal(code, 0, `Production shutdown: ${code ?? signal}`);
  }
  server = undefined;
  await log?.close();
  log = undefined;
}
async function web() {
  const python = (await readdir(join(source, '.venv/lib'))).find((name) =>
    name.startsWith('python'),
  )!;
  const driver = join(
    source,
    '.venv/lib',
    python,
    'site-packages/playwright/driver/package/index.mjs',
  );
  const { chromium } = await import(pathToFileURL(driver).href);
  const browser = await chromium.launch({ headless: true, env: environment });
  try {
    const page = await browser.newPage();
    const errors: string[] = [];
    page.on('pageerror', (error: Error) => errors.push(error.message));
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    await page
      .getByText('Navigation 2', { exact: true })
      .filter({ visible: true })
      .first()
      .waitFor();
    await page.screenshot({
      path: join(root, 'evidence/workspace.png'),
      fullPage: true,
    });
    assert.deepEqual(errors, [], 'Production web UI JavaScript errors');
    step('Real web UI loads the populated synthetic workspace');
  } finally {
    await browser.close();
  }
}
try {
  await start();
  step('Production server reaches expected-SHA health');
  await web();
  const state = await smoke_before_restart(base, environment, {
    step,
    workspace: join(root, 'workspace'),
    model: 'TEST',
    reply: 'Production smoke reply.',
  });
  await stop();
  step('Production server stops cleanly');
  await start();
  step('Production server restarts against the same private database');
  await smoke_after_restart(base, state, environment, step);
  await stop();
  step('Restarted production server stops cleanly');
  await writeFile(
    join(root, 'evidence/smoke.json'),
    JSON.stringify({ revision, base, steps, passed: true }, null, 2) + '\n',
  );
} catch (error) {
  await writeFile(
    join(root, 'evidence/smoke.json'),
    JSON.stringify({ revision, base, steps, passed: false, error: String(error) }, null, 2) + '\n',
  );
  throw error;
} finally {
  try {
    await stop();
  } finally {
    unregister();
  }
}
