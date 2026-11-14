import { test, expect } from 'vitest';
import { EventEmitter, once } from 'node:events';
import { setImmediate } from 'node:timers/promises';
import { MacOutboxSender } from '../transport/outbox.ts';
import type { ItemRow } from '../storage/contracts.ts';

test('a committed append wakes an idle sender', async () => {
  const rows: ItemRow[] = [],
    events = new EventEmitter();
  const confirmed = once(events, 'confirmed'),
    backoff = once(events, 'retrying');
  const sender = new MacOutboxSender({
    store: {
      root: '/unused',
      sender_id: 'sender',
      pending_events: events,
      pending: () => [...rows],
      pending_conversations: () => (rows.length ? [['conversation', 1]] : []),
      async acknowledge(_, _sender, _body, _accepted, _invalidated, done) {
        rows.length = 0;
        done();
      },
    },
    client: {
      async post_items(_, batch) {
        if (batch.items[0]!.envelope.submission_id === 'retry') {
          events.emit('retrying');
          throw new Error('offline');
        }
        return {
          receipts: batch.items.map((item) => ({
            sender_id: 'sender',
            submission_id: item.envelope.submission_id,
            disposition: 'accepted',
            result: {},
            accepted_at_vps_ms: 1,
          })),
          stop: null,
        };
      },
    },
    files: { async upload_file() {} },
    notify_confirmed() {
      events.emit('confirmed');
    },
    warn() {},
    on_failure() {},
  });
  sender.start();
  await setImmediate();
  const item: ItemRow = {
    sequence: 1,
    submission_id: 'item',
    conversation_id: 'conversation',
    child_id: 'child',
    kind: 'backend_event',
    payload_json: Buffer.from('{"payload":{},"files":[]}'),
    receipt_json: null,
  };
  rows.push(item);
  events.emit('pending');
  await confirmed;
  rows.push({ ...item, sequence: 2, submission_id: 'retry' });
  events.emit('pending');
  await backoff;
  await setImmediate();
  await sender.stop({ deadline: performance.now() / 1000 + 5 });
  expect(rows.map((row) => row.submission_id)).toEqual(['retry']);
});
