/** The populated fixture: a fresh TypeScript database filled through the comparison host's HTTP API. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { cp, lstat, mkdir, open, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import type { ApplicationSchemaSeed, Database } from '../../core/database/contracts.ts';
import {
  checkpointDatabase,
  createDatabase,
  createDatabaseFromSeed,
  onlineBackup,
} from '../../core/database/index.ts';
import { bootstrap_application } from '../../environments/bootstrap.ts';
import { issue_launch_token } from '../../agent_identity/data.ts';
import { DEFAULT_LAUNCH } from '../../drivers/built_in.ts';
import { load_worker_runtime_definitions } from '../../worker_types/configuration.ts';
import { HOME_VARIABLES, reserve_loopback_base } from './environment.ts';
import { smoke_worker_token } from './identity.ts';

type Json = Record<string, unknown>;
type SeedRequest = (
  method: string,
  path: string,
  body?: unknown,
  options?: { status?: number; authorization?: string },
) => Promise<Json>;

const backend_key = DEFAULT_LAUNCH.employee_backend;
const model = DEFAULT_LAUNCH.employee_launch_model;
const proposal = { body: 'Navigation proof proposal' };
const ticket = (title: string, project_id: string, extra: Json = {}) => ({
  title,
  project_id,
  ceiling: 'needs_brief',
  worker_type: 'coding',
  ...extra,
});
const workflow = (title: string, project_id: string, with_input: boolean, extra: Json = {}) => ({
  title,
  project_id,
  ceiling: 'needs_consequences',
  workflow: {
    label: 'Navigation fixture',
    guidance: '',
    fields: [
      ...(with_input ? [{ id: 'input', label: 'Input' }] : []),
      { id: 'consequences', label: 'Consequences' },
    ],
    stages: [
      ...(with_input
        ? [
            {
              id: 'needs_input',
              label: 'Input',
              gating_field: 'input',
              is_terminal: false,
              ownership_mode: 'user',
              instructions: '',
            },
          ]
        : []),
      {
        id: 'needs_consequences',
        label: 'Consequences',
        gating_field: 'consequences',
        is_terminal: false,
        ownership_mode: 'worker',
        instructions: '',
      },
      {
        id: 'done',
        label: 'Done',
        gating_field: null,
        is_terminal: true,
        ownership_mode: null,
        instructions: '',
      },
    ],
  },
  employee_backend: backend_key,
  employee_launch_model: model,
  ...extra,
});
const send = (text: string) => ({
  sender_label: 'Owner',
  backend_key,
  model,
  mode: 'queue',
  content: [{ piece: 'text', text }],
});

/** The ordinary populated records of the retired navigation comparison seed. */
async function populate(
  request: SeedRequest,
  database: Database,
  workspace: string,
): Promise<void> {
  const id = (value: Json) => {
    assert.ok(typeof value.id === 'string');
    return value.id;
  };
  const worker = (ticket_id: string) =>
    'Bearer ' + smoke_worker_token(database, ticket_id, { model, workspace });
  const p1 = id(
    await request('POST', '/api/projects', {
      name: 'Navigation 2',
      summary: 'Populated navigation proof',
    }),
  );
  const p2 = id(
    await request('POST', '/api/projects', {
      name: 'Navigation 10',
      summary: 'Populated navigation proof',
    }),
  );
  const manager = async (title: string, project_id: string) =>
    id(await request('POST', '/api/managers', { title, goal: '', project_id }));
  const ma = await manager('Alpha 2', p1);
  const mb = await manager('Alpha 10', p1);
  await manager('Äpfel', p2);
  await manager('Öl', p2);
  const ms = await manager('Settled Manager', p2);
  await request('PATCH', `/api/managers/${ms}/settled`, { settled: true });
  await request('PATCH', `/api/managers/${ma}`, {
    recap: 'The populated smoke Manager awaits its next authorized step.',
  });
  const managed = { manager: { kind: 'manager', id: ma } };
  const create = async (body: Json) => id(await request('POST', '/api/tickets', body));
  await create(ticket('Chief empty', p1));
  const a2 = await create(ticket('Alpha 2', p1, managed));
  for (const title of ['Alpha 10', 'Äpfel', 'Éclair', 'Zebra'])
    await create(ticket(title, p1, managed));
  await create(ticket('Other Manager Ticket', p1, { manager: { kind: 'manager', id: mb } }));
  const backlog = await create(ticket('Backlog', p1, managed));
  const review = await create(ticket('Review owner', p1, managed));
  const manager_review = await create(
    ticket('Manager review', p1, { ...managed, ceiling_holder: { kind: 'manager', id: ma } }),
  );
  const blocker = await create(ticket('Blocker', p1));
  const blocked = await create(ticket('Blocked', p1, managed));
  await request('POST', `/api/tickets/${backlog}/backlog`, {});
  for (const ticket_id of [review, manager_review])
    await request('POST', `/api/tickets/${ticket_id}/propose`, proposal, {
      authorization: worker(ticket_id),
    });
  await request('PUT', `/api/collections/blockers/${blocked}/${blocker}`, {});
  await create(workflow('User stage', p1, true, managed));
  await create(workflow('Chief user', p1, true));
  await create(workflow('Closeout', p1, false, managed));
  await create(workflow('Chief closeout', p1, false));
  for (const ticket_id of [
    await create(workflow('Done', p1, false, managed)),
    await create(workflow('Chief done', p1, false)),
  ]) {
    const proposed = await request('POST', `/api/tickets/${ticket_id}/propose`, proposal, {
      authorization: worker(ticket_id),
    });
    await request('POST', `/api/tickets/${ticket_id}/accept/consequences`, {
      expected_proposal_revision: (proposed.pending_proposal as Json).revision,
      next_holder: { kind: 'owner', id: 'owner' },
      next_ceiling: 'none',
    });
  }
  await request('PATCH', `/api/tickets/${a2}`, {
    recap: 'The populated smoke Ticket awaits its next authorized step.',
  });
  const goal = (title: string, extra: Json) =>
    request('POST', '/api/goals', { title, project_id: p1, kind: 'binary', ...extra });
  const binary = { primary_checklist_block_id: null, completed: false, active: true };
  const held = { holder_kind: 'manager', holder_id: ma };
  await goal('Chief goal', binary);
  await goal('Manager goal', { ...binary, ...held });
  await goal('Completed goal', { ...binary, ...held, completed: true });
  await goal('Inactive goal', { ...binary, active: false });
  const checklist = (state: string) => ({
    type: 'checklist',
    id: 'steps',
    title: 'Steps',
    items: [{ id: 'one', label: 'One', state }],
  });
  const page = (block: Json) => ({
    kind: 'headroom.page',
    version: 1,
    title: 'Checklist',
    summary: '',
    blocks: [block],
  });
  await goal('Nested checklist done', {
    primary_checklist_block_id: 'steps',
    page: page({
      type: 'section',
      id: 'section',
      title: 'Section',
      blocks: [checklist('done')],
    }),
  });
  await goal('Open checklist', {
    primary_checklist_block_id: 'steps',
    page: page(checklist('open')),
  });
  const agent = await create({
    ...ticket('Agent claim', p1, managed),
    ceiling: 'needs_implementation',
    brief: 'Ready for the synthetic step',
    employee_backend: backend_key,
    employee_launch_model: model,
  });
  // The owner restart runs the one per-Ticket worker step flow for this Ticket alone.
  await request('POST', `/api/tickets/${agent}/restart-worker`, {});
  const claimed = await request('GET', `/api/tickets?detail=full&id=${agent}`);
  assert.ok(typeof claimed.conversation_id === 'string', 'The worker step started no conversation');
  const interrupt = (conversation_id: unknown) =>
    request(
      'POST',
      `/api/conversation/conversations/${String(conversation_id)}/interrupt`,
      {},
      {
        status: 204,
      },
    );
  await interrupt(claimed.conversation_id);
  await request('POST', `/api/tickets/${a2}/conversation/send`, send('Historical turn'));
  await request('POST', `/api/tickets/${a2}/conversation/reset`, {});
  const current = await request(
    'POST',
    `/api/tickets/${a2}/conversation/send`,
    send('Current turn'),
  );
  assert.ok(typeof current.conversation_id === 'string');
  await request(
    'POST',
    '/api/messages/send',
    { target: { kind: 'owner', id: 'owner' }, message: 'Unread navigation proof', mode: 'queue' },
    { authorization: 'Bearer ' + issue_launch_token(database, current.conversation_id) },
  );
  const chief = await request('POST', '/api/chief/conversation/send', send('Chief conversation'));
  await interrupt(current.conversation_id);
  await interrupt(chief.conversation_id);
}

/** Seeds root/data from a fresh database and returns the private homes the fixture used. */
export async function seed_populated_fixture(
  root: string,
  source: string,
): Promise<Record<string, string>> {
  const home = join(root, 'homes/home');
  const homes: Record<string, string> = {
    HOME: home,
    ...Object.fromEntries(HOME_VARIABLES.map(([name, directory]) => [name, join(home, directory)])),
  };
  for (const directory of Object.values(homes))
    await mkdir(directory, { recursive: true, mode: 0o700 });
  const host = join(root, 'seed-source');
  for (const name of ['logs', 'backups', 'workspace', 'run'])
    await mkdir(join(host, name), { recursive: true, mode: 0o700 });
  await writeFile(join(host, 'config.yaml'), '{}\n', { flag: 'wx', mode: 0o600 });
  const database_path = join(host, 'planning.db');
  const schema = JSON.parse(
    await readFile(join(source, 'server/core/database/application_schema.json'), 'utf8'),
  ) as ApplicationSchemaSeed;
  const fresh = createDatabaseFromSeed(database_path, schema, (tx) =>
    bootstrap_application(tx, Math.floor(Date.now() / 1000)),
  );
  try {
    checkpointDatabase(fresh, 'TRUNCATE');
  } finally {
    fresh.close();
  }
  const base = await reserve_loopback_base();
  const log = await open(join(host, 'server.log'), 'a');
  const child = spawn(process.execPath, [join(source, 'server/test_host.ts')], {
    cwd: source,
    env: {
      ...homes,
      PATH: dirname(process.execPath) + ':/usr/bin:/bin',
      LANG: 'C.UTF-8',
      // Test mode admits databases only inside the temporary folder.
      TMPDIR: root,
      TMP: root,
      TEMP: root,
      PLAN_CONFIG_PATH: join(host, 'config.yaml'),
      PLAN_DB_PATH: database_path,
      PLAN_LOGS_DIR: join(host, 'logs'),
      PLAN_BACKUP_DIR: join(host, 'backups'),
      PLAN_WORKSPACE_ROOT: join(host, 'workspace'),
      PLAN_CREDENTIAL_KEY_PATH: join(host, 'credential-key'),
      PLAN_DISPATCHER_LOCK_PATH: join(host, 'run/dispatcher.lock'),
      PLAN_SERVER_CONTROL_SOCKET: join(host, 'run/control.sock'),
      PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(host, 'run/lifecycle.lock'),
      PLAN_SERVER_URL: base,
      PLAN_PORT: new URL(base).port,
      PLAN_TEST_MODE: '1',
      PLAN_DISPATCH_ENABLED: 'false',
    },
    stdio: ['ignore', log.fd, log.fd],
  });
  try {
    const ready = AbortSignal.timeout(30_000);
    while (true) {
      if (child.exitCode !== null || child.signalCode !== null)
        throw new Error(`Seed host exited: ${child.exitCode ?? child.signalCode}`);
      ready.throwIfAborted();
      try {
        if ((await fetch(`${base}/api/meta`, { signal: ready })).ok) break;
      } catch (error) {
        ready.throwIfAborted();
        if (!(error instanceof TypeError)) throw error;
      }
      await delay(100, undefined, { signal: ready });
    }
    let requests = 0;
    const request: SeedRequest = async (method, path, body, options = {}) => {
      requests++;
      const response = await fetch(base + path, {
        method,
        headers: {
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
          ...(options.authorization ? { authorization: options.authorization } : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        redirect: 'error',
        signal: AbortSignal.timeout(30_000),
      });
      const text = await response.text();
      assert.equal(
        response.status,
        options.status ?? 200,
        `${method} ${path}: ${response.status}: ${text.slice(0, 1000)}`,
      );
      return text ? (JSON.parse(text) as Json) : {};
    };
    const database = createDatabase(database_path);
    try {
      database.read(load_worker_runtime_definitions);
      await populate(request, database, join(host, 'workspace'));
    } finally {
      database.close();
    }
    const exited = once(child, 'exit', { signal: AbortSignal.timeout(45_000) });
    child.kill('SIGTERM');
    const [code, signal] = await exited;
    assert.equal(code, 0, `Seed host shutdown: ${code ?? signal}`);
    console.log(JSON.stringify({ database: database_path, requests }));
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const killed = once(child, 'exit');
      child.kill('SIGKILL');
      await killed;
    }
    await log.close();
  }
  await mkdir(join(root, 'data'), { mode: 0o700 });
  await onlineBackup(database_path, join(root, 'data/planning.db'));
  for (const name of ['files', 'skills', 'credential-key']) {
    try {
      await lstat(join(host, name));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') continue;
      throw error;
    }
    await cp(join(host, name), join(root, 'data', name), { recursive: true, errorOnExist: true });
  }
  return homes;
}
