/** Run the real production entry against one explicitly prepared private fixture. */
import assert from 'node:assert/strict';
import { execFileSync, type ChildProcess } from 'node:child_process';
import { mkdir, open, readFile, readdir, realpath, writeFile } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { once } from 'node:events';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright';
import { DEFAULT_BACKEND_PEER_REPLY } from './smoke/codex_peer.ts';
import { create_environment, reserve_loopback_base } from './smoke/environment.ts';
import { seed_populated_fixture } from './smoke/seed.ts';
import { smoke_before_restart, smoke_after_restart } from './smoke/steps.ts';
import { createReadAccess } from '../core/database/index.ts';
import { register_test_root } from '../core/test_paths.ts';
import { load_worker_runtime_definitions } from '../worker_types/configuration.ts';
import { load_ticket_for_write } from '../tickets/data/index.ts';
import { ChildProcesses, signal_child } from '../lifecycle/owned_processes.ts';
import { executor_link_socket_path } from '../core/http/executor_link.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import type { MachineView } from '../machines/contracts.ts';

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
const homes = await seed_populated_fixture(root, source);
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
const base = await reserve_loopback_base();
const environment = await create_environment(root, source, app_root, base, revision, homes);
const steps: string[] = [];
const step = (message: string) => {
  steps.push(message);
  console.log(`${steps.length}. ${message}`);
};
const children = new ChildProcesses();
let server: ChildProcess | undefined;
let machine: ChildProcess | undefined;
let machine_log: FileHandle | undefined;
let log: FileHandle | undefined;
async function start() {
  log = await open(join(root, 'evidence/server.log'), 'a');
  server = children.spawn(process.execPath, [join(app_root, 'server/main.ts')], {
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
async function stop_process(child: ChildProcess | undefined, name: string, expected_code: number) {
  if (child && child.exitCode === null && child.signalCode === null) {
    const exited = once(child, 'exit', { signal: AbortSignal.timeout(45_000) });
    signal_child(child, 'SIGTERM', false);
    try {
      await exited;
    } catch (error) {
      if (child.exitCode === null && child.signalCode === null) {
        const killed = once(child, 'exit', { signal: AbortSignal.timeout(5_000) });
        signal_child(child, 'SIGKILL');
        await killed;
      }
      throw error;
    }
  }
  if (child)
    assert.equal(
      child.exitCode,
      expected_code,
      `${name} shutdown: ${child.exitCode ?? child.signalCode}`,
    );
}
async function stop() {
  try {
    await stop_process(server, 'Production server', 0);
  } finally {
    server = undefined;
    await log?.close();
    log = undefined;
  }
}
async function machine_ready() {
  const ready = AbortSignal.timeout(30_000);
  while (true) {
    assert.ok(
      machine && machine.exitCode === null && machine.signalCode === null,
      `Home machine exited: ${machine?.exitCode ?? machine?.signalCode}`,
    );
    ready.throwIfAborted();
    const response = await fetch(`${base}/api/machines`, { signal: ready });
    assert.equal(response.status, 200);
    const body = (await response.json()) as { machines: MachineView[] };
    if (body.machines.some((item) => item.machine_id === HOME_MACHINE_ID && item.available)) return;
    await delay(100, undefined, { signal: ready });
  }
}
async function start_machine() {
  machine_log = await open(join(root, 'evidence/machine.log'), 'a');
  machine = children.spawn(
    process.execPath,
    [
      join(app_root, 'server/executor/mac/entrypoint.ts'),
      'executor',
      'run',
      '--executor-id',
      HOME_MACHINE_ID,
      '--application-revision',
      environment.PLAN_APP_SHA,
      '--panels-server-url',
      executor_link_socket_path(environment.PLAN_DB_PATH),
    ],
    {
      cwd: app_root,
      env: { ...environment, PLAN_EXECUTOR_ROOT: join(root, 'machine') },
      stdio: ['ignore', machine_log.fd, machine_log.fd],
    },
  );
  await machine_ready();
}
async function stop_machine() {
  try {
    await stop_process(machine, 'Home machine', 143);
  } finally {
    machine = undefined;
    await machine_log?.close();
    machine_log = undefined;
  }
}
async function web() {
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
  await start_machine();
  step('Production home machine leases through the private local link');
  await web();
  const state = await smoke_before_restart(base, environment, {
    step,
    workspace: join(root, 'workspace'),
    model: 'TEST',
    reply: DEFAULT_BACKEND_PEER_REPLY,
  });
  await stop();
  step('Production server stops cleanly');
  await start();
  step('Production server restarts against the same private database');
  await machine_ready();
  step('The same home machine reconnects after the server restart');
  await smoke_after_restart(base, state, environment, step);
  await stop_machine();
  step('Production home machine stops cleanly');
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
    try {
      await stop_machine();
    } finally {
      await stop();
    }
  } finally {
    children.seal();
    try {
      await children.settled();
    } finally {
      unregister();
    }
  }
}
