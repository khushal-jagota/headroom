import { expect, test } from 'vitest';
import { fixture, create } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ResolvedConversationStart, PromptDeliveryMode } from '../conversation/contracts.ts';
import { PromptEventPayload } from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import * as data from '../manager_wakes/data.ts';
import { reconcile_batch_outcomes } from '../manager_wakes/runtime.ts';

// The in-memory HTTP host cannot produce an interrupted native send or durable
// receipts. Reuse the existing SQLite fixture and record writer without a host.
// A foreign offering must never replay, and another sender's prompt cannot close it.
test('an interrupted offering stays uncertain until its exact durable prompt exists', () => {
  const database = fixture();
  const ticket = create(database);
  const store = new ConversationStore(database, { integer_now: () => 100 });
  store.create_conversation(
    new ResolvedConversationStart({
      conversation_id: 'notice-recovery',
      backend_key: 'codex',
      model: 'model',
      reasoning_effort: null,
      role_materials: null,
      workspace_folder: '/workspace',
      access: 'full',
    }),
  );
  database.write((sql) =>
    data.create_worker_error_wake(sql, {
      target: { kind: 'chief', id: 'chief' },
      ticket_id: ticket.id,
      ticket_title: ticket.title,
      claim_revision: 1,
      error_reason: 'stopped',
      now: 100,
    }),
  );
  const batch = database.write((sql) =>
    data.claim_next_batch(sql, { process_token: 'old-owner', now: 100 }),
  )!;
  database.write((sql) =>
    data.record_batch_offering(sql, batch.id, {
      conversation_id: 'notice-recovery',
      process_token: 'old-owner',
      now: 100,
    }),
  );
  expect(
    database.write((sql) =>
      data.preserve_interrupted_dispatches(sql, {
        process_token: 'new-owner',
        now: 101,
      }),
    ),
  ).toBe(1);
  expect(
    database.write((sql) =>
      data.recover_accepted_batches_from_other_processes(sql, {
        process_token: 'new-owner',
        now: 101,
      }),
    ),
  ).toBe(0);
  expect(database.read(data.pending_batches)).toEqual([]);
  expect(
    database.write((sql) => data.claim_next_batch(sql, { process_token: 'new-owner', now: 101 })),
  ).toBeNull();
  const prompt = (sender_message_id: string) =>
    new PromptEventPayload({
      content: text_message_content('delivered'),
      sender_label: 'Panels',
      mode: PromptDeliveryMode.queue,
      sender_message_id,
    });
  store.append_events('notice-recovery', [prompt('another-sender')]);
  expect(reconcile_batch_outcomes(database, { now: 102 })).toBe(0);
  expect(database.read(data.batches_waiting_for_outcome)).toMatchObject([{ status: 'uncertain' }]);
  expect(
    database.read((sql) =>
      sql.get<{ closed_at: number | null }>('SELECT closed_at FROM manager_wakes'),
    )?.closed_at,
  ).toBeNull();
  store.append_events('notice-recovery', [prompt(batch.sender_message_id)]);
  expect(reconcile_batch_outcomes(database, { now: 103 })).toBe(1);
  expect(
    database.read((sql) =>
      sql.get<{ status: string }>('SELECT status FROM manager_wake_batches WHERE id=?', [batch.id]),
    )?.status,
  ).toBe('delivered');
  expect(
    database.read((sql) =>
      sql.get<{ closed_at: number | null }>('SELECT closed_at FROM manager_wakes'),
    )?.closed_at,
  ).toBe(103);
  expect(reconcile_batch_outcomes(database, { now: 104 })).toBe(0);
});
