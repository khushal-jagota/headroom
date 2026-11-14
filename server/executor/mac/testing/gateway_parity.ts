/** The real gateway and spool, with scripted authority, catalog and HTTPS ports. */
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { AgentGateway } from '../ipc/gateway.ts';
import { GatewayOutbox } from '../ipc/outbox.ts';
import { OutboxStore } from '../storage/outbox.ts';
import { ExecutorPaths } from '../configuration.ts';
import { as_object, decode_value, encode_bounded } from '../../protocol.ts';
import { request as socket_request } from '../ipc_client.ts';
import { ExecutorHttpError } from '../transport/errors.ts';
import type { MacRunningChild } from '../runner/contracts.ts';
import { MacPermissionDenied } from '../runner/contracts.ts';
import type { GatewayOptions } from '../ipc/contracts.ts';
import type { IpcOperation, IpcRequest, IpcResponse, JsonObject } from '../../wire.generated.ts';
import { Deferred } from '../../../conversation/runtime/state.ts';
import { ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
const root = process.argv[2];
const input: any = JSON.parse(readFileSync(join(root, 'input.json'), 'utf8'));
const started = performance.now();
const shutdown_options = () => ({ deadline: performance.now() / 1000 + 5 });
const faults: Error[] = [];
const store = await OutboxStore.open(
  join(root, 'spool'),
  { monotonic: () => 100, now_ns: () => 1700000000000000000n },
  shutdown_options(),
  (e) => faults.push(e),
);
const spool = new GatewayOutbox(store);
const child = {
  reference: input.child,
  ticket_id: 'ticket',
  active_turn: input.turn,
} as MacRunningChild;
const other = { ...child, reference: { ...input.child, child_id: 'other' } };
const calls: unknown[] = [];
let stopped = false,
  source_gate = new Deferred<void>();
const sources: Promise<unknown>[] = [];
let source_entered = new Deferred<void>();
const catalog_entered = new Deferred<void>(),
  catalog_release = new Deferred<void>();
let exit_stop: Promise<void> | undefined;
const options: GatewayOptions = {
  outbox: spool,
  runner: {
    get admission_stopped() {
      return stopped;
    },
    seal_admission() {
      calls.push(['seal']);
      stopped = true;
    },
    authenticate(id, capability) {
      if (capability !== 'capability' || !['child', 'other'].includes(id ?? ''))
        throw new MacPermissionDenied('unknown executor child capability');
      return id === 'child' ? child : other;
    },
    source_work<T>(_reference: unknown, work: () => Promise<T>) {
      source_entered.resolve();
      const task = source_gate.promise.then(work);
      sources.push(task);
      return task;
    },
    async quiesce() {
      calls.push(['quiesce']);
      return {
        admission_stopped: true,
        idle_children_stopped: true,
        active_turn_count: 0,
        pending_callback_count: 0,
        uncertain_operation_count: 0,
        unsent_entry_count: 0,
      };
    },
  },
  presence: {
    identity: input.identity,
    status(admission_stopped) {
      return {
        identity: input.identity,
        process_id: process.pid,
        lease_accepted_at_mac_ms: null,
        last_lease_failure: null,
        refusal: null,
        admission_stopped,
        going_to_sleep: false,
      };
    },
  },
  client: {
    async read(read) {
      calls.push(['read', read]);
      if (read.path === '/ok') return { text: 'fresh VPS result', high: input.high };
      if (read.path === '/timeout')
        throw Object.assign(new Error('timeout'), { name: 'TimeoutError' });
      throw new ExecutorHttpError(Number(read.path.slice(1)), 'refused', 'scripted read refusal');
    },
  },
  settle_catalogs: async () => {
    assert.equal(stopped, true);
    calls.push(['catalog']);
    catalog_entered.resolve();
    await catalog_release.promise;
  },
  request_exit: () => {
    calls.push(['exit']);
    exit_stop = gateway.stop(shutdown_options());
    void exit_stop.catch(() => {});
  },
  on_failure: (e) => faults.push(e),
};
const gateway = new AgentGateway(options);
const make = (
  id: string,
  operation: IpcOperation,
  payload: JsonObject,
  extra: Partial<IpcRequest> = {},
): IpcRequest => ({
  request_id: id,
  operation,
  payload,
  child_id: 'child',
  capability: 'capability',
  response_deadline: null,
  ...extra,
});
const replies = [];
mkdirSync(join(root, 'artifacts/tickets/ticket'), { recursive: true });
writeFileSync(join(root, 'artifacts/tickets/ticket/file.txt'), 'original artifact');
const path = new ExecutorPaths(root).ipc_socket;
await gateway.start(path);
try {
  const health = await socket_request('health', {}, { ...process.env, PLAN_EXECUTOR_ROOT: root });
  delete health.result!.process_id;
  replies.push(health.result);
  replies.push(
    await gateway.handle(make('bad', 'retry', { submission_id: 'first' }, { capability: 'wrong' })),
  );
  for (const route of ['/ok', '/400', '/403', '/500', '/timeout'])
    replies.push(
      await gateway.handle(make(route, 'read', { path: route, params: { q: input.high } })),
    );
  replies.push(
    await gateway.handle(
      make(
        'first',
        'write',
        { operation: 'ticket.edit', arguments: { text: 'first' } },
        { response_deadline: 0 },
      ),
    ),
  );
  replies.push(await gateway.handle(make('preparing', 'retry', { submission_id: 'first' })));
  replies.push(
    await gateway.handle(
      make('foreign-preparing', 'retry', { submission_id: 'first' }, { child_id: 'other' }),
    ),
  );
  child.active_turn = null;
  source_gate.resolve();
  await Promise.all(sources);
  writeFileSync(join(root, 'artifacts/tickets/ticket/file.txt'), 'changed artifact');
  replies.push(await gateway.handle(make('queued', 'retry', { submission_id: 'first' })));
  const record = spool.lookup('first')!;
  const envelope = as_object(record.envelope, 'QueuedEnvelope');
  envelope.sender_id = 'sender';
  const receipt = decode_value(
    {
      sender_id: store.sender_id,
      submission_id: 'first',
      disposition: 'accepted',
      result: { id: 'saved', high: input.high },
      accepted_at_vps_ms: 1700000000123,
    },
    'AcceptanceReceipt',
  );
  const waiting = spool.wait_confirmed('first', 1, gateway.controller.signal);
  await store.acknowledge(
    'first',
    store.sender_id,
    Buffer.from(encode_bounded(receipt, 'AcceptanceReceipt')),
    true,
    [],
    () => spool.notify_confirmed(receipt),
  );
  const confirmed = await waiting;
  assert.equal(confirmed!.submission_id, 'first');
  replies.push(await gateway.handle(make('accepted', 'retry', { submission_id: 'first' })));
  replies.push(
    await gateway.handle(
      make('foreign-stored', 'retry', { submission_id: 'first' }, { child_id: 'other' }),
    ),
  );
  replies.push(await gateway.handle(make('unknown', 'retry', { submission_id: 'absent' })));
  const quiescing = socket_request(
    'quiesce',
    { exit_when_ready: true },
    { ...process.env, PLAN_EXECUTOR_ROOT: root },
  );
  await catalog_entered.promise;
  const sealed = await gateway.handle(
    make('sealed', 'write', { operation: 'ticket.edit', arguments: {} }),
  );
  catalog_release.resolve();
  const quiesced = await quiescing;
  assert.equal(quiesced.result!.exit_committed, true);
  quiesced.request_id = 'quiesce';
  delete quiesced.result!.process_id;
  replies.push(quiesced);
  replies.push(sealed);
  await exit_stop;
  await gateway.settled();
  assert.equal(existsSync(path), false);
  // Same store and scripted ports prove actual publication custody beyond a stop deadline.
  stopped = false;
  source_gate = new Deferred<void>();
  source_entered = new Deferred<void>();
  const retained = new AgentGateway(options);
  const preparing = await retained.handle(
    make(
      'retained',
      'write',
      { operation: 'ticket.edit', arguments: {} },
      { response_deadline: 0 },
    ),
  );
  assert.equal(preparing.unconfirmed!.publication, 'preparing');
  await source_entered.promise;
  let joined = false;
  const actual = retained.settled().then(() => {
    joined = true;
  });
  await assert.rejects(
    retained.stop({ deadline: performance.now() / 1000 + 0.005 }),
    ShutdownDeadlineExceeded,
  );
  assert.equal(joined, false);
  source_gate.resolve();
  await actual;
  assert.equal(store.pending_count(), 1);
  assert.throws(() => retained.handle(make('late', 'health', {})));
  if (faults.length) throw new AggregateError(faults);
  process.stdout.write(
    JSON.stringify(
      {
        parity: {
          replies: replies.map((reply, index) =>
            index === 0 ? reply : as_object(reply as IpcResponse, 'IpcResponse'),
          ),
          envelope,
          calls,
        },
        lifecycle: {
          actual_publication_join: joined,
          listener_closed: !existsSync(path),
          no_late_admission: true,
        },
        elapsed_seconds: (performance.now() - started) / 1000,
      },
      (_key, value) => (typeof value === 'bigint' ? value.toString() : value),
    ),
  );
} finally {
  await gateway.stop(shutdown_options()).catch(() => {});
  await store.stop(shutdown_options());
  await store.settled();
}
