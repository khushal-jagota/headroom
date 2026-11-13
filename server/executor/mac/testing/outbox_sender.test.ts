import { test, expect } from 'vitest';
import { EventEmitter } from 'node:events';
import { setImmediate } from 'node:timers/promises';
import { MacOutboxSender } from '../transport/outbox.ts';
import type { ItemRow } from '../storage/contracts.ts';

test('a committed append wakes an idle sender', async () => {
  const rows: ItemRow[] = [],
    events = new EventEmitter();
  let finish!: () => void;
  const confirmed = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const sender = new MacOutboxSender({
    store: {
      root: '/unused',
      sender_id: 'sender',
      pending_events: events,
      pending: () => [...rows],
      pending_conversations: () => (rows.length ? [['conversation', 1n]] : []),
      async acknowledge(_, _sender, _body, _accepted, _invalidated, done) {
        rows.length = 0;
        done();
      },
    },
    client: {
      async post_items(_, batch) {
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
    notify_confirmed: finish,
    warn() {},
    on_failure() {},
  });
  sender.start();
  await setImmediate();
  rows.push({
    sequence: 1n,
    submission_id: 'item',
    conversation_id: 'conversation',
    child_id: 'child',
    kind: 'backend_event',
    payload_json: Buffer.from('{"payload":{},"files":[]}'),
    receipt_json: null,
  });
  events.emit('pending');
  await confirmed;
  await sender.stop({ deadline: performance.now() / 1000 + 5 });
  expect(rows).toEqual([]);
});
