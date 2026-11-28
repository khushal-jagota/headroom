// Start one private test-host server from a checkout on a fresh database, run the iPhone
// probe against it, then stop that exact server process by its PID.
// Mirrors tests/e2e/support/server.ts (private homes, inherited loopback listener, test mode).
// Usage: node run_probe_server.mjs <checkout_root> <probe.mjs> <out.json>
import { spawn, spawnSync } from 'node:child_process';
import { closeSync, mkdirSync, mkdtempSync, openSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const [checkout, probe, out] = process.argv.slice(2).map((p) => resolve(p));
const root = mkdtempSync(join(tmpdir(), 'panels-ios-probe-'));
const home = join(root, 'home');
const dirs = {
  PLAN_LOGS_DIR: join(root, 'logs'),
  PLAN_BACKUP_DIR: join(root, 'backups'),
  PLAN_WORKSPACE_ROOT: join(root, 'workspace'),
  PLAN_TICKET_SCRATCH_ROOT: join(root, 'tickets'),
  PLAN_TICKET_FILES_DIR: join(root, 'files'),
};
for (const p of [...Object.values(dirs), home, join(home, '.codex'), join(home, '.claude')])
  mkdirSync(p, { recursive: true });
writeFileSync(join(root, 'test-config.yaml'), '{}\n');
const inherited = Object.fromEntries(
  Object.entries(process.env).filter(([k, v]) => v !== undefined && !/^(PLAN_|PANELS_REAL_)/.test(k)),
);
const dbPath = join(root, 'planning.db');
const env = {
  ...inherited,
  ...dirs,
  HOME: home,
  CODEX_HOME: join(home, '.codex'),
  CLAUDE_CONFIG_DIR: join(home, '.claude'),
  PLAN_CONFIG_PATH: join(root, 'test-config.yaml'),
  PLAN_DB_PATH: dbPath,
  PLAN_CREDENTIAL_KEY_PATH: join(root, 'credential-key'),
  PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
  PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(root, 'lifecycle-lease.json'),
  PLAN_TICKET_SCRATCH_DATABASE: dbPath,
  PLAN_TEST_MODE: '1',
  PLAN_DISPATCH_ENABLED: 'false',
};
const created = spawnSync(process.execPath, [join(checkout, 'server/installation/database_main.ts'), dbPath], {
  cwd: checkout,
  env,
  encoding: 'utf8',
});
if (created.status !== 0) throw new Error(`database_main.ts failed: ${created.stderr}`);

const listener = createServer();
await new Promise((ok, fail) => (listener.once('error', fail), listener.listen(0, '127.0.0.1', ok)));
const port = listener.address().port;
const base = `http://127.0.0.1:${port}`;
const socketDir = mkdtempSync(join(tmpdir(), 'ps-'));
Object.assign(env, {
  PLAN_PORT: String(port),
  PLAN_SERVER_URL: base,
  PLAN_SERVER_LISTENER_FD: '3',
  PLAN_SERVER_CONTROL_SOCKET: join(socketDir, 'control.sock'),
});
const logPath = join(root, 'server.log');
const log = openSync(logPath, 'w');
const child = spawn(process.execPath, [join(checkout, 'server/test_host.ts')], {
  cwd: checkout,
  env,
  stdio: ['ignore', log, log, listener._handle.fd],
});
closeSync(log);
listener.close();
console.log(`SERVER_START pid=${child.pid} port=${port} root=${root} checkout=${checkout}`);
let exit = 0;
try {
  const meta = await fetch(`${base}/api/meta`);
  const body = await meta.json();
  if (meta.status !== 200 || body.test_mode !== true) throw new Error('The server is not in test mode');
  const run = spawnSync(process.execPath, [probe, base, out], { encoding: 'utf8' });
  process.stdout.write(run.stdout);
  process.stderr.write(run.stderr);
  exit = run.status ?? 1;
} catch (error) {
  console.error(error, '\n--- server log ---\n', readFileSync(logPath, 'utf8').split('\n').slice(-40).join('\n'));
  exit = 1;
} finally {
  const exited = new Promise((ok) => child.once('exit', (code, signal) => ok({ code, signal })));
  if (child.exitCode === null && child.signalCode === null) process.kill(child.pid, 'SIGTERM');
  const timer = setTimeout(() => process.kill(child.pid, 'SIGKILL'), 5000);
  const { code, signal } = await exited;
  clearTimeout(timer);
  console.log(`SERVER_STOP pid=${child.pid} code=${code} signal=${signal}`);
}
process.exit(exit);
