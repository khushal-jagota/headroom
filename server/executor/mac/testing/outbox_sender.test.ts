import { test, expect, vi, afterEach } from 'vitest';
import { EventEmitter, once } from 'node:events';
import { setImmediate } from 'node:timers/promises';
import { MacOutboxSender } from '../transport/outbox.ts';
import type { OutboxSenderOptions } from '../transport/outbox.ts';
import { ExecutorHttpError } from '../transport/errors.ts';
import type { ItemRow } from '../storage/contracts.ts';
import type { ItemBatch, ItemBatchReply } from '../../wire.generated.ts';

const child = {
  executor_incarnation: 'incarnation',
  conversation_id: 'conversation',
  child_id: 'child',
};
function row(id: string, sequence: number, conversation_id = 'conversation'): ItemRow {
  return {
    sequence,
    submission_id: id,
    conversation_id,
    child_id: 'child',
    kind: 'backend_event',
    payload_json: Buffer.from(
      JSON.stringify({ payload: { child: { ...child, conversation_id }, event: id }, files: [] }),
    ),
    receipt_json: null,
  };
}
function accepted(batch: ItemBatch): ItemBatchReply {
  return {
    receipts: batch.items.map(({ envelope }) => ({
      sender_id: 'sender',
      submission_id: envelope.submission_id,
      disposition: 'accepted',
      result: {},
      accepted_at_vps_ms: 1,
    })),
    stop: null,
  };
}
function fixture(post: OutboxSenderOptions['client']['post_items']) {
  const rows: ItemRow[] = [],
    events = new EventEmitter(),
    starts = new Map<string, number>(),
    warnings: string[] = [];
  const receipts: { id: string; result: unknown }[] = [];
  const pending = (id?: string) =>
    rows.filter((r) => r.receipt_json === null && (id === undefined || r.conversation_id === id));
  const sender = new MacOutboxSender({
    store: {
      root: '/unused',
      sender_id: 'sender',
      pending_events: events,
      pending,
      pending_conversations: () =>
        [...new Set(pending().map((r) => r.conversation_id))].map(
          (id) => [id, pending(id)[0]!.sequence] as const,
        ),
      delivery_started(id) {
        if (!starts.has(id)) starts.set(id, Date.now());
        return starts.get(id)!;
      },
      async fail_delivery(id, receipt, _reason, done) {
        rows.find((r) => r.submission_id === id)!.receipt_json = receipt;
        done();
      },
      async acknowledge(id, _sender, body, done) {
        rows.find((r) => r.submission_id === id)!.receipt_json = body;
        done();
      },
    },
    client: { post_items: post },
    notify_confirmed(receipt) {
      receipts.push({ id: receipt.submission_id, result: receipt.result });
      events.emit('confirmed');
    },
    warn(message) {
      warnings.push(message);
      events.emit('warning');
    },
    on_failure(error) {
      events.emit('failure', error);
    },
  });
  return {
    rows,
    starts,
    sender,
    warnings,
    receipts,
    pending,
    events,
    stop: () => sender.stop({ deadline: performance.now() / 1000 + 5 }),
  };
}
async function flush() {
  await setImmediate();
  await setImmediate();
}
function fake_time() {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.setSystemTime(1_000_000);
  vi.spyOn(AbortSignal, 'timeout').mockImplementation((ms) => {
    const controller = new AbortController();
    setTimeout(
      () => controller.abort(new DOMException('Delivery deadline elapsed', 'TimeoutError')),
      ms,
    );
    return controller.signal;
  });
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test('a committed append wakes an idle sender and shutdown retains a transient item', async () => {
  const f = fixture(async (_, batch) => {
    if (batch.items[0]!.envelope.submission_id === 'retry') throw new Error('offline');
    return accepted(batch);
  });
  f.sender.start();
  await flush();
  const confirmed = once(f.events, 'confirmed');
  f.rows.push(row('item', 1));
  f.events.emit('pending');
  await confirmed;
  const failed = once(f.events, 'warning');
  f.rows.push(row('retry', 2));
  f.events.emit('pending');
  await failed;
  await f.stop();
  expect(f.pending().map((r) => r.submission_id)).toEqual(['retry']);
  expect(f.warnings[0]).toContain('sequence=2 attempt=1 retry_in=1s: Error: offline');
});

test('HTTP404 settles the head with a local refusal before the later message and turn end', async () => {
  const f = fixture(async (_, batch) => {
    if (batch.items[0]!.envelope.submission_id === 'deleted')
      throw new ExecutorHttpError(404, 'not_found', 'conversation was deleted');
    return accepted(batch);
  });
  const first = row('deleted', 1);
  first.kind = 'cli_write';
  f.rows.push(first, row('message', 2), row('turn_end', 3));
  f.sender.start();
  await flush();
  expect(f.receipts.map((r) => r.id)).toEqual(['deleted', 'message', 'turn_end']);
  expect(f.receipts[0]!.result).toMatchObject({
    local_delivery_failure: true,
    delivery_outcome: 'refused',
    error: { message: 'HTTP 404 not_found: conversation was deleted' },
  });
  expect(f.rows[0]!.receipt_json?.toString()).toContain('"accepted_at_vps_ms":0');
  await f.stop();
});

test('transient HTTP and retry_later failures keep bounded backoff and real diagnostics', async () => {
  fake_time();
  let calls = 0;
  const f = fixture(async (_, batch) => {
    calls++;
    if (calls === 1) throw new ExecutorHttpError(502, 'bad_gateway', 'upstream unavailable');
    if (calls === 2) throw new ExecutorHttpError(503, 'unavailable', 'server busy');
    if (calls === 3)
      return {
        receipts: [],
        stop: {
          sequence: batch.items[0]!.envelope.sequence,
          reason: 'retry_later',
          message: 'This item is still being saved; send it again',
          missing_files: [],
        },
      };
    return accepted(batch);
  });
  f.rows.push(row('message', 1));
  f.sender.start();
  await flush();
  expect(calls).toBe(1);
  await vi.advanceTimersByTimeAsync(1000);
  await flush();
  expect(calls).toBe(2);
  await vi.advanceTimersByTimeAsync(2000);
  await flush();
  expect(calls).toBe(3);
  await vi.advanceTimersByTimeAsync(4000);
  await flush();
  expect(calls).toBe(4);
  expect(f.pending()).toEqual([]);
  expect(f.warnings).toEqual(
    expect.arrayContaining([
      expect.stringContaining('HTTP 502 bad_gateway: upstream unavailable'),
      expect.stringContaining('HTTP 503 unavailable: server busy'),
      expect.stringContaining('retry_later: This item is still being saved; send it again'),
    ]),
  );
  await f.stop();
});

test('an expired persisted start survives restart without another delivery attempt', async () => {
  fake_time();
  const sent: string[] = [];
  const f = fixture(async (_, batch) => {
    sent.push(...batch.items.map((item) => item.envelope.submission_id));
    return accepted(batch);
  });
  const old = row('old', 1);
  old.kind = 'cli_write';
  f.rows.push(old, row('turn_end', 2));
  f.starts.set('old', Date.now() - 300_001);
  f.sender.start();
  await flush();
  expect(sent).toEqual(['turn_end']);
  expect(f.receipts[0]).toMatchObject({
    id: 'old',
    result: { local_delivery_failure: true, delivery_outcome: 'unconfirmed' },
  });
  expect(f.warnings[0]).toContain('sequence=1');
  await f.stop();
});

test('four active senders rotate after one batch before a busy conversation gets another slot', async () => {
  const calls: string[] = [],
    releases: (() => void)[] = [];
  const f = fixture(async (id, batch) => {
    calls.push(id);
    await new Promise<void>((resolve) => releases.push(resolve));
    return accepted(batch);
  });
  for (let index = 0; index < 6; index++) {
    const item = row(`item${index}`, index + 1, `c${index}`);
    item.kind = 'cli_write';
    f.rows.push(item);
  }
  const again = row('again', 7, 'c0');
  again.kind = 'cli_write';
  f.rows.push(again);
  f.sender.start();
  await flush();
  expect(calls).toEqual(['c0', 'c1', 'c2', 'c3']);
  releases.shift()!();
  await flush();
  expect(calls[4]).toBe('c4');
  releases.shift()!();
  await flush();
  expect(calls[5]).toBe('c5');
  releases.shift()!();
  await flush();
  expect(calls[6]).toBe('c0');
  for (const release of releases) release();
  await flush();
  expect(f.pending()).toEqual([]);
  await f.stop();
});

test('network retries cap their delay and fail visibly at five minutes before later items proceed', async () => {
  fake_time();
  const f = fixture(async (_, batch) => {
    if (batch.items[0]!.envelope.submission_id === 'offline')
      throw new ExecutorHttpError(503, 'unavailable', 'home unavailable');
    return accepted(batch);
  });
  const first = row('offline', 1);
  first.kind = 'cli_write';
  f.rows.push(first, row('turn_end', 2));
  f.sender.start();
  await flush();
  await vi.advanceTimersByTimeAsync(300_000);
  await flush();
  expect(f.pending()).toEqual([]);
  const delays = f.warnings.flatMap((message) => {
    const match = /retry_in=(\d+)s/.exec(message);
    return match ? [Number(match[1])] : [];
  });
  expect(Math.max(...delays)).toBe(60);
  expect(f.receipts[0]).toMatchObject({
    id: 'offline',
    result: {
      delivery_outcome: 'unconfirmed',
      error: { message: expect.stringContaining('HTTP 503 unavailable: home unavailable') },
    },
  });
  expect(f.receipts[1]!.id).toBe('turn_end');
  await f.stop();
});

test('a receipted prefix cannot expire its newer successor while the home defers it', async () => {
  fake_time();
  let deferred = false;
  const f = fixture(async (_, batch) => {
    const newer = batch.items.find((item) => item.envelope.submission_id === 'newer');
    if (newer && !deferred) {
      deferred = true;
      return {
        receipts: accepted(batch).receipts.filter((receipt) => receipt.submission_id === 'earlier'),
        stop: {
          sequence: newer.envelope.sequence,
          reason: 'retry_later',
          message: 'home is busy',
          missing_files: [],
        },
      };
    }
    return accepted(batch);
  });
  f.rows.push(row('earlier', 1), row('newer', 2));
  f.starts.set('earlier', Date.now() - 299_000);
  f.sender.start();
  await flush();
  await vi.advanceTimersByTimeAsync(4000);
  await flush();
  expect(f.pending()).toEqual([]);
  expect(f.receipts.map((receipt) => receipt.id)).toEqual(['earlier', 'newer']);
  expect(
    f.receipts.every(
      (receipt) => !(receipt.result as Record<string, unknown>).local_delivery_failure,
    ),
  ).toBe(true);
  await f.stop();
});
