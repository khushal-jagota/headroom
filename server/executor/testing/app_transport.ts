/** One owned HTTP host under the existing synthetic TLS proxy; no native backend. */
import { readFileSync, writeFileSync, readdirSync, readlinkSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { request as http_request } from 'node:http';
import { setTimeout as pause } from 'node:timers/promises';
import { createDatabase } from '../../core/database/index.ts';
import { loadConfig } from '../../core/config.ts';
import { RealClock } from '../../core/clock.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import { createApp } from '../../app.ts';
import { UpdateConflict } from '../../environments/updates/attempts.ts';
import type { MacUpdateServiceContract } from '../../environments/updates/mac/contracts.ts';
import { VpsExecutor } from '../vps_transport.ts';
import { VpsExecutorRegistry } from '../registry.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../../execution_identity.ts';
import { as_object } from '../protocol.ts';
import { continue_core } from './transport_core.ts';

const spec = JSON.parse(readFileSync(process.argv[2]!, 'utf8')) as {
  path: string;
  archive: string;
  archive_sha: string;
  ready: string;
  state: string;
  fd: number;
  continuation: string;
  auth_mode?: boolean;
};
Date.now = () => 1_760_000_000_000;
const database = createDatabase(spec.path);
const config = loadConfig(undefined, {
  PLAN_DB_PATH: spec.path,
  PLAN_TEST_MODE: '1',
  PLAN_EXECUTOR_ID: 'test-executor',
  PLAN_TRUSTED_INGRESS_PROVIDER: 'tailscale',
  PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN: 'owner@example.com',
  PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN: 'https://localhost',
});
const registry = new VpsExecutorRegistry(database, new RealClock(), {
  db_path: spec.path,
  changes: new ChangeSignal(),
  media_signal: new AbortController().signal,
  original_enabled: true,
});
database.read((sql) => registry.load(sql));
const endpoint = registry.get(ORIGINAL_MAC_MACHINE_ID)!;
endpoint.presence.returned = null;
const unexpected = async () => {
  throw new Error('No core work belongs to the raw transport fixture');
};
endpoint.runs.conversations = {
  emit_answer: unexpected,
  retire_replaced_remote_child: unexpected,
  resume_undelivered_prompt: unexpected,
  await_late_steer: unexpected,
  confirm_remote_child_termination: unexpected,
  stop_interrupted_remote_child: unexpected,
};
const reports: unknown[] = [];
const services = new Map<string, { platform: string; service: MacUpdateServiceContract }>();
function service_for(machine_id: string): MacUpdateServiceContract {
  const platform = database.read((sql) =>
    sql.get<{ platform: string }>(
      'SELECT platform FROM machine_executor_credentials WHERE machine_id=?',
      [machine_id],
    ),
  )!.platform;
  const kept = services.get(machine_id);
  if (kept?.platform === platform) return kept.service;
  const service: MacUpdateServiceContract = {
    for_machine: service_for,
    prepare: unexpected,
    status: () => null,
    phase: () => null,
    reported_at: () => null,
    check: () => ({
      schema_version: 1,
      target: null,
      prefetch: spec.auth_mode
        ? {
            application_revision: (platform === 'linux' ? 'b' : 'a').repeat(40),
            archive_sha256: spec.archive_sha,
            archive_size: readFileSync(spec.archive).byteLength,
            archive_path: '/api/executor/update/prefetch/' + spec.archive_sha,
          }
        : null,
    }),
    archive: (request_id: string) => {
      if (request_id !== 'fixture') throw new UpdateConflict('unknown fixture archive');
      return spec.archive;
    },
    prefetch_archive: (sha256: string) => {
      if (sha256 !== spec.archive_sha) throw new UpdateConflict('unknown fixture archive');
      return spec.archive;
    },
    report: (report, health: ReturnType<VpsExecutor['health']> | null) => {
      const value = [
        as_object(report, 'MacUpdateReport'),
        health === null ? null : as_object(health, 'ExecutorHealthRead'),
      ];
      reports.push(spec.auth_mode ? [machine_id, platform, ...value, credential_rows()] : value);
      if (spec.auth_mode && report.request_id === 'refused')
        throw new UpdateConflict('fixture report refused');
      return {
        request_id: report.request_id,
        target_revision: report.application_revision,
        phase: report.phase,
        detail: null,
      };
    },
  };
  services.set(machine_id, { platform, service });
  return service;
}
function credential_rows() {
  return database.read((sql) =>
    sql.all('SELECT * FROM machine_executor_credentials ORDER BY machine_id'),
  );
}
registry.bind_updates(service_for(ORIGINAL_MAC_MACHINE_ID));
const app = await createApp(config, { database, executor: registry });
// The source raw-transport fixture uses this same recording allowlisted read.
app.get('/api/tickets', async (request) => ({
  actor: request.headers['x-plan-actor'] ?? null,
  ticket: request.headers['x-plan-ticket-id'] ?? null,
  status: (request.query as { status?: string }).status ?? null,
}));
endpoint.app = app;
await app.ready();
await new Promise<void>((resolve, reject) => {
  app.server.once('error', reject);
  app.server.listen({ fd: spec.fd }, () => {
    app.server.off('error', reject);
    resolve();
  });
});
const address = app.server.address();
if (address === null || typeof address === 'string' || address.address !== '127.0.0.1')
  throw new Error('fixture listener is not its inherited loopback socket');
writeFileSync(spec.ready, JSON.stringify(address));
let closing = false,
  poll_disconnected = false;
for await (const command of createInterface({ input: process.stdin })) {
  if (command === 'continue') {
    const poll = endpoint.mailbox.poll.bind(endpoint.mailbox);
    let entered!: () => void, finished!: () => void, signal: AbortSignal | undefined;
    const started = new Promise<void>((resolve) => {
      entered = resolve;
    });
    const settled = new Promise<void>((resolve) => {
      finished = resolve;
    });
    endpoint.mailbox.poll = (...args) => {
      signal = args[3]?.signal;
      const result = poll(...args);
      entered();
      return result.finally(finished);
    };
    // This listener was bound and verified above; no input URL can select a target.
    const connection = http_request({
      host: address.address,
      port: address.port,
      path: '/api/executor/commands?after=999999&wait=50',
      headers: {
        'Tailscale-User-Login': 'owner@example.com',
        'X-Panels-Executor-ID': 'test-executor',
        'X-Panels-Executor-Incarnation': '11111111-1111-4111-8111-111111111111',
        'X-Panels-Executor-Protocol': '22',
      },
    });
    connection.on('error', () => {});
    try {
      connection.end();
      await started;
      connection.destroy();
      await settled;
      if (!signal?.aborted) throw new Error('Disconnected poll retained its mailbox wait');
      poll_disconnected = true;
    } finally {
      connection.destroy();
      endpoint.mailbox.poll = poll;
    }
    writeFileSync(spec.continuation, JSON.stringify(await continue_core(database, endpoint)));
    continue;
  }
  if (command !== 'close' || closing) continue;
  closing = true;
  if (spec.auth_mode) {
    await registry.stop({ deadline: performance.now() / 1000 + 5 });
    await registry.settled();
    await app.close();
    writeFileSync(spec.state, JSON.stringify({ reports, credentials: credential_rows() }));
    database.close();
    break;
  }
  // The isolated catalog proof cannot prove facade ordering against mailbox sealing.
  const catalog_read = endpoint.catalog.snapshot('codex', { refresh: true });
  void catalog_read.catch(() => {});
  const limit = performance.now() + 5000;
  let catalog_command: { command_id: string } | undefined;
  while (
    !(catalog_command = database.read((sql) =>
      sql.get<{ command_id: string }>(
        "SELECT command_id FROM executor_commands WHERE operation='read_model_list' AND state='waiting' ORDER BY command_sequence DESC LIMIT 1",
      ),
    ))
  ) {
    if (performance.now() >= limit) throw new Error('Catalog read did not enter its waiting row');
    await pause(1);
  }
  const catalog_id = catalog_command.command_id;
  await registry.stop({ deadline: performance.now() / 1000 + 5 });
  await registry.settled();
  await Promise.allSettled([catalog_read]);
  const catalog_state = database.read((sql) =>
    sql.get<{ state: string }>('SELECT state FROM executor_commands WHERE command_id=?', [
      catalog_id,
    ]),
  )?.state;
  if (catalog_state !== 'withdrawn') throw new Error(`Catalog stop left ${catalog_state}`);
  await app.close();
  const descriptors = readdirSync('/proc/self/fd')
    .flatMap((fd) => {
      try {
        return [readlinkSync(`/proc/self/fd/${fd}`)];
      } catch {
        return [];
      }
    })
    .filter((path) => path.includes('/files/conversations/'));
  writeFileSync(
    spec.state,
    JSON.stringify({ reports, descriptors, catalog_state, poll_disconnected }),
  );
  database.close();
  break;
}
