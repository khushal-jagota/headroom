/** Start and stop one isolated TypeScript Panels server for one browser test. */
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { closeSync, mkdirSync, mkdtempSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

export const REPO_ROOT = resolve(import.meta.dirname, '../../..');
export const FAKE_NOW = '2026-07-04T12:00:00';
/** Every explicit browser wait carries this limit. */
export const WAIT_MS = 10_000;

export type JsonObject = Record<string, any>;

/** `main` is the production entry. `test_host` adds scripted backends and /api/test routes. */
export type ServerHost = 'main' | 'test_host';

export interface ServerOptions {
  host?: ServerHost;
  fakeNow?: string;
  databaseName?: string;
}

export interface ServerHandle {
  base: string;
  port: number;
  root: string;
  dbPath: string;
  logPath: string;
  /** The private homes and paths the server runs with. The CLI reuses them. */
  environment: Record<string, string>;
  process: ChildProcess;
}

/** Private homes, in the same layout as the Python synthetic homes. */
export function syntheticHomes(storage: string): Record<string, string> {
  const home = join(mkdtempSync(join(storage, 'synthetic-homes-')), 'home');
  const homes = {
    HOME: home,
    CODEX_HOME: join(home, '.codex'),
    CLAUDE_CONFIG_DIR: join(home, '.claude'),
    HERMES_HOME: join(home, '.hermes'),
    PLAN_HERMES_HOME: join(home, '.hermes'),
  };
  for (const path of Object.values(homes)) mkdirSync(path, { recursive: true });
  return homes;
}

/**
 * Runtime paths inside `root`, with no inherited PLAN_ value and no real provider opt-in.
 * The CLI also runs with this environment, so it never reaches a live server.
 */
export function privateEnvironment(root: string): Record<string, string> {
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(
      (entry): entry is [string, string] =>
        entry[1] !== undefined && !/^(PLAN_|PANELS_REAL_)/.test(entry[0]),
    ),
  );
  const directories = {
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_WORKSPACE_ROOT: join(root, 'workspace'),
    PLAN_TICKET_SCRATCH_ROOT: join(root, 'tickets'),
    PLAN_TICKET_FILES_DIR: join(root, 'files'),
  };
  for (const path of Object.values(directories)) mkdirSync(path, { recursive: true });
  writeFileSync(join(root, 'test-config.yaml'), '{}\n');
  return {
    ...inherited,
    ...directories,
    PLAN_CONFIG_PATH: join(root, 'test-config.yaml'),
    PLAN_DB_PATH: join(root, 'planning.db'),
    PLAN_CREDENTIAL_KEY_PATH: join(root, 'credential-key'),
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(root, 'lifecycle-lease.json'),
    PLAN_TICKET_SCRATCH_DATABASE: join(root, 'planning.db'),
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    PLAN_TEST_MODE: '1',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_PORT: '0',
    ...syntheticHomes(root),
  };
}

async function loopbackListener(): Promise<Server> {
  const listener = createServer();
  await new Promise<void>((done, fail) => {
    listener.once('error', fail);
    listener.listen(0, '127.0.0.1', done);
  });
  return listener;
}

function listenerDescriptor(listener: Server): number {
  // Node exposes no public descriptor for a listening server. The child inherits this one
  // as fd 3, so the port is held from bind to the server's first accept.
  const fd = (listener as unknown as { _handle?: { fd?: number } })._handle?.fd;
  if (typeof fd !== 'number' || fd < 0) throw new Error('The test listener has no descriptor');
  return fd;
}

function logTail(path: string, lines = 40): string {
  try {
    return readFileSync(path, 'utf8').split('\n').slice(-lines).join('\n');
  } catch {
    return '(no server log)';
  }
}

export async function startServer(options: ServerOptions = {}): Promise<ServerHandle> {
  const started = performance.now();
  // The server refuses test databases outside the OS temporary folder.
  const root = mkdtempSync(join(tmpdir(), 'panels-browser-'));
  const dbPath = join(root, options.databaseName ?? 'planning.db');
  const logPath = join(root, 'server.log');
  const environment = privateEnvironment(root);
  // A fresh TypeScript install's database, built from the application schema seed.
  const created = spawnSync(
    process.execPath,
    [join(REPO_ROOT, 'server/installation/database_main.ts'), dbPath],
    { cwd: REPO_ROOT, env: environment, encoding: 'utf8' },
  );
  if (created.status !== 0)
    throw new Error(`database_main.ts failed (${created.status}): ${created.stderr}`);

  const socketDirectory = mkdtempSync(join(tmpdir(), 'ps-'));
  const listener = await loopbackListener();
  const address = listener.address();
  if (address === null || typeof address === 'string') throw new Error('No loopback port');
  const port = address.port;
  const base = `http://127.0.0.1:${port}`;
  Object.assign(environment, {
    PLAN_DB_PATH: dbPath,
    PLAN_PORT: String(port),
    PLAN_SERVER_URL: base,
    PLAN_SERVER_LISTENER_FD: '3',
    PLAN_SERVER_CONTROL_SOCKET: join(socketDirectory, 'control.sock'),
    PLAN_FAKE_NOW: options.fakeNow ?? FAKE_NOW,
  });
  const log = openSync(logPath, 'w');
  let child: ChildProcess;
  try {
    const entry = options.host === 'test_host' ? 'server/test_host.ts' : 'server/main.ts';
    child = spawn(process.execPath, [join(REPO_ROOT, entry)], {
      // The assets mount and config.yaml resolve from the repository root.
      cwd: REPO_ROOT,
      env: environment,
      stdio: ['ignore', log, log, listenerDescriptor(listener)],
    });
  } finally {
    closeSync(log);
    // The server holds the only other copy, so its exit closes the listener.
    listener.close();
  }
  // The listener descriptor belongs to this child alone.
  const { PLAN_SERVER_LISTENER_FD: _listener, ...shared } = environment;
  const handle: ServerHandle = { base, port, root, dbPath, logPath, environment: shared, process: child };
  try {
    // The request waits in the listener queue until the server accepts it, however long
    // boot takes. If the server exits first, the queued connection fails at once.
    let meta: Response;
    try {
      meta = await fetch(`${base}/api/meta`);
    } catch (error) {
      throw new Error(
        `The server (exit ${child.exitCode ?? child.signalCode}) closed its listener without answering /api/meta: ${error}\n--- server log tail ---\n${logTail(logPath)}`,
      );
    }
    if (meta.status !== 200) throw new Error(`/api/meta -> ${meta.status}: ${await meta.text()}`);
    if ((await meta.json()).test_mode !== true) throw new Error('The server is not in test mode');
    const page = await fetch(`${base}/`);
    if (page.status !== 200 || !(await page.text()).includes('data-svelte-app'))
      throw new Error('The server does not serve the built web app. Build web/ first.');
  } catch (error) {
    await stopServer(handle);
    throw error;
  }
  console.log(`E2E_SERVER_START ${root} seconds=${((performance.now() - started) / 1000).toFixed(3)}`);
  return handle;
}

export async function stopServer(handle: ServerHandle): Promise<void> {
  const child = handle.process;
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise<void>((done) => child.once('exit', () => done()));
  child.kill('SIGTERM');
  const timer = setTimeout(() => child.kill('SIGKILL'), 5_000);
  await exited;
  clearTimeout(timer);
}

export function serverLogTail(handle: ServerHandle, lines = 200): string {
  return logTail(handle.logPath, lines);
}
