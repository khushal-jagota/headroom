/** Private, read-only API comparison. Preparation is safe until --confirmed is supplied. */
import assert from 'node:assert/strict';
import { execFile, spawn, type ChildProcess } from 'node:child_process';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { cp, lstat, mkdir, open, readFile, readdir, realpath, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { isDeepStrictEqual, parseArgs, promisify } from 'node:util';

const { values } = parseArgs({
  options: {
    'app-root': { type: 'string' },
    'state-root': { type: 'string' },
    snapshot: { type: 'string' },
    'snapshot-sha256': { type: 'string' },
    confirmed: { type: 'boolean', default: false },
  },
  allowPositionals: false,
});
assert(values.confirmed, 'Execution requires Manager confirmation, then --confirmed');
assert(
  values['app-root'] && values['state-root'] && values.snapshot && values['snapshot-sha256'],
  '--app-root, --state-root, --snapshot and --snapshot-sha256 are required',
);
const app = await realpath(values['app-root']);
const root = await realpath(values['state-root']);
const permitted = '/home/vps/projects/panels/task-tmp/t_mmcf9srv/gate3-instance';
assert.equal(root, permitted, 'State must be the reserved private gate3-instance folder');
assert.deepEqual(await readdir(root), [], 'State folder must be empty');
assert(!(await lstat(values.snapshot)).isSymbolicLink(), 'Snapshot must not be a symlink');
const snapshot = await realpath(values.snapshot);
const suffix = relative(snapshot, root);
assert(
  isAbsolute(suffix) || suffix === '..' || suffix.startsWith('..' + sep),
  'State must be outside the snapshot',
);
const execute = promisify(execFile);
const revision = (await execute('git', ['-C', app, 'rev-parse', 'HEAD'])).stdout.trim();
const evidence = join(root, 'evidence');
await mkdir(evidence, { mode: 0o700 });
const copied = join(root, 'snapshot');
await cp(snapshot, copied, {
  recursive: true,
  errorOnExist: true,
  force: false,
  filter: async (source) => {
    assert(!(await lstat(source)).isSymbolicLink(), 'Snapshot contains a symlink');
    return true;
  },
});

const hash = createHash('sha256');
for await (const chunk of createReadStream(join(copied, 'database.sqlite'))) hash.update(chunk);
const snapshot_sha256 = hash.digest('hex');
assert.equal(
  snapshot_sha256,
  values['snapshot-sha256'],
  'Copied snapshot differs from approved SHA',
);
const metadata = JSON.parse(await readFile(join(copied, 'metadata.json'), 'utf8'));
assert.equal(metadata.sha256, snapshot_sha256, 'Copied snapshot metadata SHA mismatch');

type Instance = {
  name: string;
  base: string;
  env: Record<string, string>;
  child?: ChildProcess;
  log?: Awaited<ReturnType<typeof open>>;
};
async function instance(name: string): Promise<Instance> {
  const home = join(root, name);
  for (const directory of [
    'home',
    'codex',
    'claude',
    'hermes',
    'logs',
    'backups',
    'workspace',
    'run',
    'tmp',
    'config',
    'cache',
    'state',
    'data',
    'bin',
  ])
    await mkdir(join(home, directory), { recursive: true, mode: 0o700 });
  await writeFile(join(home, 'config.yaml'), '{}\n', { mode: 0o600, flag: 'wx' });
  // Even an unexpected startup send cannot find a real provider executable.
  for (const executable of ['codex', 'claude'])
    await writeFile(join(home, 'bin', executable), '#!/bin/sh\nexit 1\n', {
      mode: 0o700,
      flag: 'wx',
    });
  const listener = createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const address = listener.address();
  assert(address && typeof address !== 'string');
  await new Promise<void>((resolve, reject) =>
    listener.close((error) => (error ? reject(error) : resolve())),
  );
  const base = `http://127.0.0.1:${address.port}`;
  return {
    name,
    base,
    env: {
      PATH: join(home, 'bin') + ':/usr/bin:/bin',
      LANG: 'C.UTF-8',
      HOME: join(home, 'home'),
      CODEX_HOME: join(home, 'codex'),
      CLAUDE_CONFIG_DIR: join(home, 'claude'),
      HERMES_HOME: join(home, 'hermes'),
      PLAN_HERMES_HOME: join(home, 'hermes'),
      XDG_CONFIG_HOME: join(home, 'config'),
      XDG_CACHE_HOME: join(home, 'cache'),
      XDG_DATA_HOME: join(home, 'data'),
      XDG_STATE_HOME: join(home, 'state'),
      XDG_RUNTIME_DIR: join(home, 'run'),
      TMPDIR: root,
      TMP: root,
      TEMP: root,
      PYTHONPATH: join(app, 'src'),
      PYTHONDONTWRITEBYTECODE: '1',
      PLAN_CONFIG_PATH: join(home, 'config.yaml'),
      PLAN_DB_PATH: join(home, 'data/planner.db'),
      PLAN_LOGS_DIR: join(home, 'logs'),
      PLAN_BACKUP_DIR: join(home, 'backups'),
      PLAN_WORKSPACE_ROOT: join(home, 'workspace'),
      PLAN_CREDENTIAL_KEY_PATH: join(home, 'data/key'),
      PLAN_DISPATCHER_LOCK_PATH: join(home, 'run/dispatcher.lock'),
      PLAN_SERVER_CONTROL_SOCKET: join(home, 'run/control.sock'),
      PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(home, 'run/lifecycle.lock'),
      PLAN_APP_ROOT: app,
      PLAN_PORT: String(address.port),
      PLAN_SERVER_URL: base,
      PLAN_TEST_MODE: '1',
      PLAN_DISPATCH_ENABLED: 'false',
      PLAN_CLAUDE_EXECUTABLE: join(home, 'bin/claude'),
      PLAN_HERMES_PYTHON: join(home, 'bin/absent-hermes'),
    },
  };
}
const python = join(app, '.venv/bin/python');
const instances = [await instance('python'), await instance('typescript')];
const prepare = `
import json, sqlite3, sys
from pathlib import Path
from planner.environments.backup import restore_database_snapshot
from planner.core.db import connect, create_schema
from planner.tickets.data import audit_ticket_registry_integrity
source, destination = map(Path, sys.argv[1:])
restore_database_snapshot(source, destination, live_stopped=True)
conn = connect(str(destination))
create_schema(conn, startup_audit=lambda c: audit_ticket_registry_integrity(c, startup=True))
removed = {}
for table in ('conversation_background_tasks', 'deploy_interruption_deliveries',
              'deploy_interruptions', 'deploy_interruption_accounts'):
    removed[table] = conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
    conn.execute('DELETE FROM ' + table)
conn.commit()
audit_ticket_registry_integrity(conn, startup=True)
if conn.execute('SELECT count(*) FROM tickets WHERE integrity_error_reason IS NOT NULL').fetchone()[0]:
    raise RuntimeError('Copied Ticket integrity audit failed; no server started')
samples = [r[0] for r in conn.execute('SELECT conversation_id FROM conversations ORDER BY created_at DESC LIMIT 3')]
conn.close()
print(json.dumps({'removed': removed, 'conversations': samples}))
`;
let conversations: string[] = [];
for (const item of instances) {
  const prepared = await execute(python, ['-c', prepare, copied, item.env.PLAN_DB_PATH], {
    cwd: app,
    env: item.env,
    timeout: 180_000,
    maxBuffer: 1024 * 1024,
  });
  conversations = (JSON.parse(prepared.stdout) as { conversations: string[] }).conversations;
  await writeFile(join(evidence, item.name + '-sanitization.json'), prepared.stdout, {
    mode: 0o600,
  });
}

// Match the existing connected comparison composition, with real records and no provider.
const typescript_bootstrap = `
import { run_application_process } from './server/lifecycle/application.ts';
import { startServer } from './server/core/start.ts';
import { create_production_conversation_core } from './server/lifecycle/production.ts';
import { compose_production_backends } from './server/lifecycle/production_bindings.ts';
import { create_backend_runtime, unavailable_backend_providers } from './server/conversation/snapshot/composition.ts';
let bound;
const refuse = () => { throw new Error('Gate 3 permits reads only'); };
await run_application_process({
  start: (onShutdownComplete) => startServer({
    onShutdownComplete,
    createConversationCore(context) {
      bound = compose_production_backends(context, {
        claude: () => refuse, codex: () => refuse, hermes: () => refuse,
        http: () => create_backend_runtime({
          database: context.database, config: context.config,
          now: () => context.clock.now(), providers: unavailable_backend_providers(),
          one_shot_children: context.one_shot_children,
        }),
        stall_sources: () => [],
      });
      return create_production_conversation_core(context, bound);
    },
    createBackendServices: () => bound.http,
  }),
}, code => process.exit(code));
`;
async function start(item: Instance) {
  item.log = await open(join(evidence, item.name + '.log'), 'wx', 0o600);
  const command = item.name === 'python' ? python : process.execPath;
  const args =
    item.name === 'python'
      ? [
          '-c',
          'from planner.server_lifecycle.application import run_application_process; run_application_process()',
        ]
      : ['--input-type=module', '-e', typescript_bootstrap];
  item.child = spawn(command, args, {
    cwd: app,
    env: item.env,
    stdio: ['ignore', item.log.fd, item.log.fd],
  });
  await writeFile(join(evidence, item.name + '-pid'), String(item.child.pid), { mode: 0o600 });
  const signal = AbortSignal.timeout(90_000);
  for (;;) {
    assert(
      item.child.exitCode === null && item.child.signalCode === null,
      item.name + ' exited before health',
    );
    signal.throwIfAborted();
    try {
      const response = await fetch(item.base + '/api/health', { signal });
      if (response.ok) return;
    } catch (error) {
      signal.throwIfAborted();
      if (!(error instanceof TypeError)) throw error;
    }
    await delay(100, undefined, { signal });
  }
}
async function stop(item: Instance) {
  if (item.child && item.child.exitCode === null && item.child.signalCode === null) {
    const exit = once(item.child, 'exit', { signal: AbortSignal.timeout(45_000) });
    item.child.kill('SIGTERM');
    try {
      await exit;
    } catch (error) {
      const killed = once(item.child, 'exit', { signal: AbortSignal.timeout(5_000) });
      item.child.kill('SIGKILL');
      await killed;
      throw error;
    }
  }
  await item.log?.close();
}
const results: { request: string; matched: boolean; status?: number[]; differences?: string[] }[] =
  [];
function differences(a: unknown, b: unknown, path = ''): string[] {
  if (isDeepStrictEqual(a, b)) return [];
  if (
    a &&
    b &&
    typeof a === 'object' &&
    typeof b === 'object' &&
    Array.isArray(a) === Array.isArray(b)
  ) {
    const left = a as Record<string, unknown>,
      right = b as Record<string, unknown>;
    return [...new Set([...Object.keys(left), ...Object.keys(right)])]
      .flatMap((key) =>
        differences(
          left[key],
          right[key],
          path + '/' + key.replaceAll('~', '~0').replaceAll('/', '~1'),
        ),
      )
      .slice(0, 10);
  }
  return [path || '/'];
}
async function compare(path: string): Promise<unknown> {
  const replies = [];
  for (const item of instances) {
    const response = await fetch(item.base + path, { signal: AbortSignal.timeout(30_000) });
    const text = await response.text();
    let body: unknown,
      json = true;
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
      json = false;
    }
    replies.push({ status: response.status, body, json });
    await writeFile(join(evidence, `${results.length}-${item.name}.json`), JSON.stringify(body), {
      mode: 0o600,
    });
  }
  const matched =
    replies[0].status === 200 &&
    replies[0].json &&
    replies[1].json &&
    isDeepStrictEqual(replies[0], replies[1]);
  results.push({
    request: path,
    matched,
    status: replies.map((value) => value.status),
    differences: differences(replies[0], replies[1]),
  });
  return replies[0].body;
}
function entries(body: unknown, key: string): Record<string, unknown>[] {
  if (typeof body !== 'object' || body === null || !(key in body)) return [];
  const list = (body as Record<string, unknown>)[key];
  return Array.isArray(list) ? list.filter((value) => value && typeof value === 'object') : [];
}
let completed = false;
try {
  // Exactly two server processes. Requests and CLI children run sequentially.
  for (const item of instances) await start(item);
  for (const path of [
    '/api/projects?detail=full',
    '/api/ideas',
    '/api/worker-types?supported=true',
    '/api/review',
  ])
    await compare(path);
  for (const goal of entries(await compare('/api/goals'), 'goals').slice(0, 2))
    await compare('/api/goals/' + encodeURIComponent(String(goal.id)));
  for (const conversation of conversations) {
    const path = '/api/conversation/conversations/' + encodeURIComponent(conversation);
    await compare(path);
    await compare(path + '/events?limit=30');
  }
  const tickets = entries(
    await compare('/api/tickets?detail=summary&limit=20&offset=0'),
    'tickets',
  );
  for (const ticket of tickets.slice(0, 3)) {
    const id = encodeURIComponent(String(ticket.id));
    await compare(`/api/tickets?detail=full&id=${id}`);
    await compare(`/api/tickets/${id}/workspace`);
  }
  const managers = entries(await compare('/api/managers?detail=full'), 'managers');
  for (const manager of managers.slice(0, 2)) {
    const id = encodeURIComponent(String(manager.id));
    await compare(`/api/managers/${id}/workspace-content`);
    await compare(`/api/managers/${id}/workspace`);
  }
  const commands = [
    ['ticket', 'list', '--json'],
    ['manager', 'list', '--json'],
    ['goal', 'list', '--json'],
    ['project', 'list', '--json'],
  ];
  if (tickets[0]) commands.push(['ticket', 'show', String(tickets[0].id), '--json']);
  for (const args of commands) {
    const outputs: { code: number | string; body: unknown; json: boolean }[] = [];
    for (const item of instances) {
      let stdout = '',
        stderr = '',
        code: number | string = 0;
      try {
        const result = await execute(
          item.name === 'python' ? python : process.execPath,
          item.name === 'python'
            ? ['-m', 'planner', ...args]
            : [join(app, 'server/cli/main.ts'), ...args],
          { cwd: app, env: item.env, timeout: 30_000, maxBuffer: 64 * 1024 * 1024 },
        );
        stdout = result.stdout;
        stderr = result.stderr;
      } catch (error) {
        const failed = error as { stdout?: string; stderr?: string; code?: number | string };
        stdout = failed.stdout ?? '';
        stderr = failed.stderr ?? '';
        code = failed.code ?? 'failed';
      }
      let body: unknown,
        json = true;
      try {
        body = JSON.parse(stdout);
      } catch {
        body = stdout;
        json = false;
      }
      const output = { code, body, json };
      outputs.push(output);
      await writeFile(
        join(evidence, `${results.length}-${item.name}.json`),
        JSON.stringify({ ...output, stderr }),
        { mode: 0o600 },
      );
    }
    results.push({
      request: 'panels ' + args.join(' '),
      matched:
        outputs.every((value) => value.code === 0 && value.json) &&
        isDeepStrictEqual(outputs[0], outputs[1]),
      differences: differences(outputs[0], outputs[1]),
    });
  }
  completed = true;
} finally {
  const stopped = await Promise.allSettled(instances.map(stop));
  const shutdown_failures = stopped.filter((result) => result.status === 'rejected').length;
  await writeFile(
    join(evidence, 'comparison.json'),
    JSON.stringify(
      {
        revision,
        snapshot,
        snapshot_sha256,
        completed,
        shutdown_failures,
        files_coverage: 'Nightly omits artifact files; no live files are read.',
        mode: 'test mode: read contracts only, not production boot',
        results,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  assert.equal(shutdown_failures, 0, 'A private server failed to stop cleanly');
}
const mismatches = results.filter((result) => !result.matched).length;
console.log(
  `${results.length} read comparisons, ${mismatches} differences. Private evidence: ${evidence}`,
);
if (mismatches) process.exitCode = 1;
