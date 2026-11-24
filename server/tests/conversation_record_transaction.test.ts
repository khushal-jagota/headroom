import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { expect, test } from 'vitest';
import { fixture, fixture_path, create, advance } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ResolvedConversationStart, PromptDeliveryMode } from '../conversation/contracts.ts';
import {
  PromptEventPayload,
  ContextCompactedEventPayload,
  PromptDiscardedEventPayload,
} from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import type { ReadSql, SqlParameters, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { InboxStore } from '../executor/data/inbox.ts';
import { FileStaging } from '../executor/vps_files.ts';
import { as_object } from '../executor/protocol.ts';
import type { WireValue } from '../executor/contracts.ts';

// First-prompt behavior alone cannot prove that SQLite avoids other conversations' prompts.
test('delivered prompt existence searches only the requested conversation across store instances', () => {
  const database = fixture();
  const store = new ConversationStore(database);
  for (const conversation_id of ['requested', 'other']) {
    store.create_conversation(
      new ResolvedConversationStart({
        conversation_id,
        backend_key: parse_backend_instance_id('codex'),
        model: 'model',
        reasoning_effort: null,
        role_materials: null,
        workspace_folder: '/workspace',
        access: 'full',
      }),
    );
  }
  expect(store.has_delivered_prompt('requested')).toBe(false);
  const content = text_message_content('prompt');
  const prompt = new PromptEventPayload({
    content,
    sender_label: 'owner',
    mode: PromptDeliveryMode.queue,
  });
  store.append_events('other', [prompt]);
  store.append_events('requested', [
    new PromptDiscardedEventPayload({ content, sender_label: 'owner' }),
  ]);
  const read = database.read.bind(database);
  const plans: { detail: string }[] = [];
  const observed_read = <T>(work: (sql: ReadSql) => T): T =>
    read((sql) =>
      work({
        all: sql.all.bind(sql),
        get<Row>(query: string, parameters?: SqlParameters): Row | undefined {
          plans.push(...sql.all<{ detail: string }>(`EXPLAIN QUERY PLAN ${query}`, parameters));
          return sql.get<Row>(query, parameters);
        },
      }),
    );
  const observed_database = {
    read: observed_read,
    write: database.write.bind(database),
    close: database.close.bind(database),
  };
  expect(new ConversationStore(observed_database).has_delivered_prompt('requested')).toBe(false);
  store.append_events('requested', [prompt]);
  expect(new ConversationStore(observed_database).has_delivered_prompt('requested')).toBe(true);
  expect(plans).toHaveLength(2);
  for (const { detail } of plans) {
    expect(detail).toContain('SEARCH conversation_events');
    expect(detail).toContain(
      'USING INDEX sqlite_autoindex_conversation_events_1 (conversation_id=?)',
    );
  }
});

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
      backend_key: parse_backend_instance_id('codex'),
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
      backend_key: parse_backend_instance_id('codex'),
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
    tx.run('INSERT INTO mac_executor_presence(machine_id,executor_incarnation) VALUES (?,?)', [
      ORIGINAL_MAC_MACHINE_ID,
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

// Dispatch can follow a user-owned opener's claim release. Its durable intent still owns the wake.
test('dispatch memory reads keep a current wake after its claim is released', () => {
  const database = fixture();
  const ticket = create(database);
  const store = new ConversationStore(database);
  const conversation_id = 'released-wake';
  const wake = `worker_step_message_${ticket.id}:1`;
  store.create_conversation(
    new ResolvedConversationStart({
      conversation_id,
      backend_key: parse_backend_instance_id('codex'),
      model: 'model',
      reasoning_effort: null,
      role_materials: null,
      workspace_folder: '/synthetic',
      access: 'full',
    }),
  );
  store.append_events(conversation_id, [
    new PromptEventPayload({
      content: text_message_content('original work'),
      sender_label: 'Panels',
      sender_message_id: 'worker_step_message_first',
      mode: PromptDeliveryMode.queue,
    }),
    new ContextCompactedEventPayload(),
  ]);
  database.write((tx) => {
    tx.run(
      'UPDATE conversations SET automatically_compacted_through_sequence=2 WHERE conversation_id=?',
      [conversation_id],
    );
    tx.run(
      "UPDATE tickets SET conversation_id=?,worker_step_claim='none',worker_step_claim_revision=2 WHERE id=?",
      [conversation_id, ticket.id],
    );
    tx.run("INSERT INTO worker_step_dispatch_intents VALUES (?,1,?,'dispatch_started')", [
      ticket.id,
      wake,
    ]);
  });
  const note = store.worker_step_memory_note(conversation_id, ['unregistered', wake]);
  expect(note?.after_sender_message_id).toBe(wake);
  expect(note?.sender_message_id).toMatch(/^worker_step_memory_notice_[0-9a-f]{32}$/);
});

// An automatic prompt can remain queued while the Ticket reaches Done. The
// final admission must discard it, while an ordinary message stays eligible.
test('terminal Ticket discards queued step and decision prompts at admission', () => {
  const database = fixture();
  const ticket = create(database);
  const store = new ConversationStore(database, { integer_now: () => 100 });
  const conversation_id = 'terminal-admission';
  store.create_conversation(
    new ResolvedConversationStart({
      conversation_id,
      backend_key: parse_backend_instance_id('codex'),
      model: 'model',
      reasoning_effort: null,
      role_materials: null,
      workspace_folder: '/workspace',
      access: 'full',
    }),
  );
  const decision_id = database.write((tx) => {
    tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', [conversation_id, ticket.id]);
    tx.run("INSERT INTO worker_step_dispatch_intents VALUES (?,?,?,'dispatch_started')", [
      ticket.id,
      100,
      'queued-step',
    ]);
    return tx.get<{ sender_message_id: string }>(
      'SELECT sender_message_id FROM ticket_decision_messages WHERE ticket_id=? LIMIT 1',
      [ticket.id],
    )!.sender_message_id;
  });
  const message = (sender_message_id: string) =>
    new PromptDiscardedEventPayload({
      content: text_message_content('queued'),
      sender_label: 'system',
      sender_message_id,
    });
  expect(
    store.admit_sender_messages(conversation_id, [message('queued-step')])[0]!.suppression_reason,
  ).toBeNull();
  advance(database, ticket);
  expect(
    store
      .admit_sender_messages(conversation_id, [
        message('queued-step'),
        message(decision_id),
        message('ordinary-message'),
      ])
      .map((decision) => decision.suppression_reason),
  ).toEqual(['ticket_stage_is_terminal', 'ticket_stage_is_terminal', null]);
  expect(
    store
      .read_events_after(conversation_id, 0)
      .filter((event) => event.kind === 'prompt_discarded'),
  ).toHaveLength(2);
});
