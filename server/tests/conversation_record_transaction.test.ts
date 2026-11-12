import { expect, test } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ResolvedConversationStart, PromptDeliveryMode } from '../conversation/contracts.ts';
import { PromptEventPayload } from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import type { ReadSql } from '../core/database/contracts.ts';

// The output fixture cannot prove that a failed participant rolls back source rows,
// sequence allocation, lifecycle, receipts, and projection before a safe retry.
test('a rejected participant rolls the whole append back and retry commits once', () => {
  const database = fixture();
  let reject = true;
  const store = new ConversationStore(database, {
    integer_now: () => 100,
    transaction_participant: (tx, candidate) => {
      expect(candidate.receipt_sequences).toEqual([1]);
      expect(
        tx.get<{ total: number }>('SELECT COUNT(*) AS total FROM conversation_events')?.total,
      ).toBe(1);
      expect(
        tx.get<{ through_sequence: number }>('SELECT through_sequence FROM conversation_view_state')
          ?.through_sequence,
      ).toBe(1);
      expect(candidate.facts).toHaveLength(1);
      tx.run('INSERT INTO conversation_background_tasks VALUES (?,?,?)', [
        'atomic',
        'participant',
        'participant write',
      ]);
      if (reject) {
        expect(database.read(snapshot)).toEqual(before);
        throw new Error('reject candidate');
      }
    },
  });
  store.create_conversation(
    new ResolvedConversationStart({
      conversation_id: 'atomic',
      backend_key: 'codex',
      model: 'model',
      reasoning_effort: null,
      role_materials: null,
      workspace_folder: '/workspace',
      access: 'full',
    }),
  );
  const snapshot = (sql: ReadSql) => ({
    record: sql.all('SELECT latest_sequence FROM conversations ORDER BY conversation_id'),
    events: sql.all('SELECT * FROM conversation_events ORDER BY conversation_id,sequence'),
    facts: sql.all(
      'SELECT * FROM conversation_lifecycle_facts ORDER BY conversation_id,kind,identity',
    ),
    views: ['state', 'rows', 'turns', 'groups', 'lookups'].map((name) =>
      sql.all(`SELECT * FROM conversation_view_${name} ORDER BY rowid`),
    ),
    tasks: sql.all('SELECT * FROM conversation_background_tasks ORDER BY conversation_id,task_id'),
  });
  const before = database.read(snapshot);
  const prompt = new PromptEventPayload({
    content: text_message_content('one delivered prompt'),
    sender_label: 'owner',
    mode: PromptDeliveryMode.queue,
    sender_message_id: 'sender',
  });
  const options = {
    lifecycle: [
      {
        kind: 'turn',
        identity: '1',
        turn_number: 1,
        operation: 'turn',
        status: 'running',
        provider_identity: null,
      },
    ] as const,
    recording_receipt: {
      sender_id: 'owner',
      submission_id: 'sender',
      in_commit: (tx: import('../core/database/contracts.ts').TransactionSql) => {
        tx.run('INSERT INTO conversation_background_tasks VALUES (?,?,?)', [
          'atomic',
          'receipt',
          'receipt write',
        ]);
      },
    },
  };
  expect(() => store.append_events('atomic', [prompt], options)).toThrow('reject candidate');
  expect(database.read(snapshot)).toEqual(before);
  reject = false;
  const committed = store.append_events('atomic', [prompt], options);
  expect(committed.map((row) => row.sequence)).toEqual([1]);
  expect(store.read_events_after('atomic', 0)).toEqual(committed);
  expect(store.read_conversation('atomic')?.latest_sequence).toBe(1);
  expect(store.lifecycle_facts('atomic')).toEqual(options.lifecycle);
  expect(
    database.read(
      (sql) =>
        sql.get<{ through_sequence: number }>(
          'SELECT through_sequence FROM conversation_view_state',
        )?.through_sequence,
    ),
  ).toBe(1);
  expect(store.read_live_background_task_ids().get('atomic')).toEqual(['receipt', 'participant']);
});
