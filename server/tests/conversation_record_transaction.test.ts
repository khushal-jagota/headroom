import { expect, test } from 'vitest';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ResolvedConversationStart, PromptDeliveryMode } from '../conversation/contracts.ts';
import { PromptEventPayload } from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { InboxStore } from '../executor/data/inbox.ts';
import { FileStaging } from '../executor/vps_files.ts';
import { as_object } from '../executor/protocol.ts';
import type { WireValue } from '../executor/contracts.ts';

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

// A whole-write rejection cannot prove the executor's permanent refusal: its
// receipt must survive while its taken event, lifecycle and projection roll back.
test('executor refusal after take commits only its receipt and replays without another event', () => {
  const observed: number[] = [];
  const database = fixture({
    onChange: () =>
      observed.push(
        database.read(
          (sql) =>
            sql.get<{ total: number }>('SELECT COUNT(*) AS total FROM conversation_events')!.total,
        ),
      ),
  });
  const store = new ConversationStore(database, { integer_now: () => 100 });
  const id = 'executor-atomic';
  store.create_conversation(
    new ResolvedConversationStart({
      conversation_id: id,
      backend_key: 'codex',
      model: 'model',
      reasoning_effort: null,
      role_materials: null,
      workspace_folder: '/workspace',
      access: 'full',
    }),
  );
  const identity = {
    protocol_version: 22,
    application_revision: 'synthetic',
    spool_format_version: 5,
    executor_incarnation: 'test-incarnation',
    connection_generation: 0,
  };
  database.write((tx) =>
    tx.run('INSERT INTO mac_executor_presence(singleton,executor_incarnation) VALUES (1,?)', [
      identity.executor_incarnation,
    ]),
  );
  const inbox = new InboxStore(database, new FileStaging(fixture_path(database)));
  const envelope: WireValue<'QueuedEnvelope'> = {
    conversation_id: id,
    sender_id: 'sender',
    submission_id: 'submission',
    sequence: 1,
    kind: 'backend_event',
    payload: {},
    file_dependencies: [],
  };
  const prompt = new PromptEventPayload({
    content: text_message_content('must not escape'),
    sender_label: 'owner',
    mode: PromptDeliveryMode.queue,
    sender_message_id: 'held',
  });
  let takes = 0;
  const attempt = () =>
    store.commit_backend_event<WireValue<'JsonObject'>>(
      () => {
        takes++;
        return {
          result: {},
          written: store.append_in_transaction(current!, id, [prompt], {
            lifecycle: [
              {
                kind: 'turn',
                identity: '1',
                turn_number: 1,
                operation: 'turn',
                status: 'running',
                provider_identity: null,
              },
            ],
          }),
        };
      },
      (tx, take) => {
        current = tx;
        const [receipt, taken] = inbox.accept_in_transaction(
          tx,
          envelope,
          () => {
            take();
            throw new PlannerError('validation', 'permanent refusal');
          },
          100,
          { identity },
        );
        return { result: as_object(receipt, 'AcceptanceReceipt'), taken, written: [] };
      },
    );
  let current: TransactionSql | null = null;
  observed.length = 0;
  const first = attempt();
  expect(first.taken).toBe(false);
  expect(first.written).toEqual([]);
  expect(first.result.disposition).toBe('refused');
  expect(store.read_conversation(id)?.latest_sequence).toBe(0);
  expect(store.read_events_after(id, 0)).toEqual([]);
  expect(store.lifecycle_facts(id)).toEqual([]);
  expect(
    database.read((sql) =>
      ['state', 'rows', 'turns', 'groups', 'lookups'].map((name) =>
        sql.all(`SELECT * FROM conversation_view_${name} WHERE conversation_id=?`, [id]),
      ),
    ),
  ).toEqual([[], [], [], [], []]);
  expect(attempt()).toEqual(first);
  expect(takes).toBe(1);
  expect(observed.every((total) => total === 0)).toBe(true);
  expect(
    database.read((sql) =>
      sql.get<{ disposition: string }>(
        'SELECT disposition FROM mac_inbox_positions WHERE sender_id=? AND conversation_id=?',
        ['sender', id],
      ),
    )?.disposition,
  ).toBe('refused');
  // A participant without actual savepoint rollback retains the original refusal guard.
  expect(() =>
    store.commit_backend_event(
      (tx) => ({ result: {}, written: store.append_in_transaction(tx, id, [prompt]) }),
      (_tx, take) => {
        take();
        return { result: {}, taken: false, written: [] };
      },
    ),
  ).toThrow('commit participant refused after taking the conversation event');
  expect(store.read_events_after(id, 0)).toEqual([]);
  expect(store.read_conversation(id)?.latest_sequence).toBe(0);
});
