/** One real-mailbox exchange; no listener or backend provider. */
import { readFileSync } from 'node:fs';
import { createDatabase } from '../../core/database/index.ts';
import { stringify_python_json } from '../../core/http/numeric_json.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import { VpsCatalogRelay } from '../catalog_relay.ts';
import { SqliteCommandMailbox } from '../vps_mailbox.ts';
import { MacPresenceService } from '../vps_presence.ts';
import { parse_executor_json } from '../protocol_json.ts';
import { as_object, decode_value, parse_identity, integer_value } from '../protocol.ts';
import type { WireTypes } from '../wire.generated.ts';
const spec = parse_executor_json(readFileSync(0)) as unknown as {
  path: string;
  now: number;
  identity: WireTypes['ExecutorIdentity'];
  lease: WireTypes['LeaseRequest'];
  snapshots: WireTypes['BackendSnapshot'][];
};
const now = Number(integer_value(spec.now));
const database = createDatabase(spec.path),
  mailbox = new SqliteCommandMailbox(database, { clock: () => now });
const presence = new MacPresenceService(database, mailbox, {
  changes: new ChangeSignal(),
  clock: () => now,
});
const runs = { owns_conversation: () => false };
let relay = new VpsCatalogRelay(database, runs, presence, mailbox),
  cursor: WireTypes['MailboxCommand']['command_sequence'] = 0;
const flags: boolean[] = [];
async function reply(
  snapshot: WireTypes['BackendSnapshot'],
  caller?: AbortController,
): Promise<void> {
  const command = (await mailbox.poll(parse_identity(spec.identity), cursor, 1)).commands.at(-1)!;
  cursor = command.command_sequence;
  flags.push(command.model_list_request!.refresh ?? false);
  const owned = (
    relay as unknown as { tasks: Map<string, { promise: Promise<unknown> }> }
  ).tasks.get('codex')!.promise;
  caller?.abort(new DOMException('caller left', 'AbortError'));
  await mailbox.record_results(parse_identity(spec.identity), {
    outcomes: [
      {
        command_id: command.command_id,
        child_outcome: null,
        model_list: as_object(snapshot, 'BackendSnapshot'),
      },
    ],
  });
  await owned;
}
let output: Record<string, unknown>;
try {
  const cold = relay.snapshot('codex');
  await reply(spec.snapshots[0]!);
  const first = await cold;
  const cached = await relay.snapshot('codex');
  const refreshed = relay.snapshot('codex', { refresh: true });
  await reply(spec.snapshots[1]!);
  await reply(spec.snapshots[2]!);
  const fallback = await refreshed;
  const controller = new AbortController();
  const caller = relay.snapshot('codex', { refresh: true, signal: controller.signal }).then(
    () => false,
    (error) => error.name === 'AbortError',
  );
  await reply(spec.snapshots[3]!, controller);
  const cancelled = await caller;
  await relay.stop({ deadline: performance.now() / 1000 + 2 });
  await presence.accept_lease(
    decode_value({ ...spec.lease, going_to_sleep: true }, 'LeaseRequest'),
  );
  relay = new VpsCatalogRelay(database, runs, presence, mailbox);
  const offline = await relay.snapshot('codex');
  const rows = database
    .read((sql) =>
      sql.all<{ snapshot_json: string }>(
        'SELECT * FROM machine_backend_snapshots ORDER BY machine_id,backend_key',
      ),
    )
    .map((row) => ({ ...row, snapshot_json: parse_executor_json(Buffer.from(row.snapshot_json)) }));
  output = {
    values: [first, cached, fallback, offline].map((value) => as_object(value, 'BackendSnapshot')),
    flags,
    cancelled,
    rows,
  };
  await relay.stop({ deadline: performance.now() / 1000 + 2 });
  try {
    await relay.snapshot('codex');
  } catch (error) {
    output.sealed = (error as Error).name;
  }
} finally {
  await relay.stop({ deadline: performance.now() / 1000 + 2 });
  await presence.stop({ deadline: performance.now() / 1000 + 2 });
  await mailbox.stop({ deadline: performance.now() / 1000 + 2 });
  database.close();
}
process.stdout.write(stringify_python_json(output));
