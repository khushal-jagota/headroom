import { test, expect } from 'vitest';
import { EventEmitter, once } from 'node:events';
import { setImmediate } from 'node:timers/promises';
import { MacOutboxSender } from '../transport/outbox.ts';
import type { ItemRow } from '../storage/contracts.ts';
import { Deferred } from '../../../core/deferred.ts';
import type { FileManifest } from '../../wire.generated.ts';

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

// Existing coverage proves idle wakes and offline retries, but not a recap behind an artifact upload.
test('a retained recap arrives once after its artifact upload resumes', async () => {
  const events = new EventEmitter();
  const uploading = new Deferred<void>();
  const resume = new Deferred<void>();
  const confirmed = new Deferred<void>();
  const file: FileManifest = {
    scope: 'ticket',
    owner_id: 'ticket',
    relative_path: 'artifacts/evidence.txt',
    sha256: 'a'.repeat(64),
    byte_count: 1,
  };
  const child = { executor_incarnation: 'inc', child_id: 'child', conversation_id: 'conversation' };
  const row = (sequence: number, submission_id: string, files: FileManifest[]): ItemRow => ({
    sequence,
    submission_id,
    conversation_id: 'conversation',
    child_id: 'child',
    kind: 'backend_event',
    payload_json: Buffer.from(JSON.stringify({ payload: { child }, files })),
    receipt_json: null,
  });
  const rows = [row(1, 'artifact', [file]), row(2, 'retained-recap', [])];
  const accepted: string[] = [];
  const failures: unknown[] = [];
  let uploaded = false;
  const sender = new MacOutboxSender({
    store: {
      root: '/unused',
      sender_id: 'sender',
      pending_events: events,
      pending: () => [...rows],
      pending_conversations: () => (rows.length ? [['conversation', 1]] : []),
      async acknowledge(id, _sender, _body, _accepted, _invalidated, done) {
        rows.splice(
          rows.findIndex((item) => item.submission_id === id),
          1,
        );
        done();
      },
    },
    client: {
      async post_items(_, batch) {
        if (!uploaded)
          return {
            receipts: [],
            stop: {
              sequence: 1,
              reason: 'missing_files',
              message: 'upload required',
              missing_files: [file],
            },
          };
        return {
          receipts: batch.items.map(({ envelope }) => {
            accepted.push(envelope.submission_id);
            return {
              sender_id: 'sender',
              submission_id: envelope.submission_id,
              disposition: 'accepted',
              result: {},
              accepted_at_vps_ms: 1,
            };
          }),
          stop: null,
        };
      },
    },
    files: {
      async upload_file() {
        uploading.resolve();
        await resume.promise;
        uploaded = true;
      },
    },
    notify_confirmed(receipt) {
      if (receipt.submission_id === 'retained-recap') confirmed.resolve();
    },
    warn() {},
    on_failure(error) {
      failures.push(error);
    },
  });
  try {
    sender.start();
    await uploading.promise;
    expect(accepted).toEqual([]);
    expect(rows.map((item) => item.submission_id)).toEqual(['artifact', 'retained-recap']);
    resume.resolve();
    await confirmed.promise;
    events.emit('pending');
    await setImmediate();
    expect(accepted).toEqual(['artifact', 'retained-recap']);
    expect(rows).toEqual([]);
    expect(failures).toEqual([]);
  } finally {
    resume.resolve();
    await sender.stop({ deadline: performance.now() / 1000 + 5 });
    await sender.settled();
  }
});
