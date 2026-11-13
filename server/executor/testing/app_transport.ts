/** One owned HTTP host under the existing synthetic TLS proxy; no native backend. */
import { readFileSync, writeFileSync, readdirSync, readlinkSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { setTimeout as pause } from 'node:timers/promises';
import { createDatabase } from '../../core/database/index.ts';
import { loadConfig } from '../../core/config.ts';
import { RealClock } from '../../core/clock.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import { createApp } from '../../app.ts';
import { UpdateConflict } from '../../environments/updates/attempts.ts';
import { VpsExecutor } from '../vps_transport.ts';
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
const endpoint = new VpsExecutor(database, new RealClock(), {
  db_path: spec.path,
  changes: new ChangeSignal(),
  media_signal: new AbortController().signal,
});
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
endpoint.bind_updates({
  prepare: unexpected,
  status: () => null,
  phase: () => null,
  reported_at: () => null,
  check: () => ({ schema_version: 1, target: null, prefetch: null }),
  archive: (request_id: string) => {
    if (request_id !== 'fixture') throw new UpdateConflict('unknown fixture archive');
    return spec.archive;
  },
  prefetch_archive: (sha256: string) => {
    if (sha256 !== spec.archive_sha) throw new UpdateConflict('unknown fixture archive');
    return spec.archive;
  },
  report: (report, health: ReturnType<VpsExecutor['health']> | null) => {
    reports.push([
      as_object(report, 'MacUpdateReport'),
      health === null ? null : as_object(health, 'ExecutorHealthRead'),
    ]);
    return {
      request_id: report.request_id,
      target_revision: report.application_revision,
      phase: report.phase,
      detail: null,
    };
  },
});
const app = await createApp(config, { database, executor: endpoint });
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
let closing = false;
for await (const command of createInterface({ input: process.stdin })) {
  if (command === 'continue') {
    writeFileSync(spec.continuation, JSON.stringify(await continue_core(database, endpoint)));
    continue;
  }
  if (command !== 'close' || closing) continue;
  closing = true;
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
  await endpoint.stop({ deadline: performance.now() / 1000 + 5 });
  await endpoint.settled();
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
  writeFileSync(spec.state, JSON.stringify({ reports, descriptors, catalog_state }));
  database.close();
  break;
}
