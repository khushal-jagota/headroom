import { ConversationStartAborted } from '../runtime/abort.ts';
import { expect, test, vi } from 'vitest';
import { fixture, fixture_path, create } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ResolvedConversationStart, PromptDeliveryMode } from '../conversation/contracts.ts';
import { PromptEventPayload } from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import * as data from '../manager_wakes/data.ts';
import type {
  Database,
  ReadSql,
  TransactionSql,
  WriteOptions,
} from '../core/database/contracts.ts';
import { TestClock } from '../core/clock.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import {
  ConversationStartRequest,
  PromptDeliveryStarted,
  type PromptDeliveryFate,
} from '../conversation/contracts.ts';
import {
  configure_conversation_start,
  conversation_link_lock,
} from '../runtime/conversation_start.ts';
import { ManagerWakeLoop, reconcile_batch_outcomes } from '../manager_wakes/runtime.ts';

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

// Neither the HTTP fake nor a settled receipt proves a hung provider cannot retain
// the wake observer past shutdown or cause a late write after database ownership ends.
test('the wake deadline records uncertainty and detaches a hung send without a late write', async () => {
  const source = fixture();
  const ticket = create(source);
  let access_count = 0;
  const database: Database = {
    read<T>(work: (sql: ReadSql) => T): T {
      access_count += 1;
      return source.read(work);
    },
    write<T>(work: (sql: TransactionSql) => T, options?: WriteOptions): T {
      access_count += 1;
      return source.write(work, options);
    },
    close: () => source.close(),
  };
  configure_conversation_start(database, {
    db_path: fixture_path(source),
    workspace_root: '/workspace',
  });
  const system = new InMemoryConversationSystem();
  await system.start_conversation(
    new ConversationStartRequest({
      conversation_id: 'hung-wake',
      backend_key: 'codex',
      model: 'gpt-6.1-sol',
      workspace_folder: '/workspace',
    }),
  );
  const store = new ConversationStore(database, { integer_now: () => 100 });
  store.create_conversation(
    new ResolvedConversationStart({
      conversation_id: 'hung-wake',
      backend_key: 'codex',
      model: 'gpt-6.1-sol',
      reasoning_effort: null,
      role_materials: null,
      workspace_folder: '/workspace',
      access: 'full',
    }),
  );
  database.write((sql) => {
    sql.run('INSERT INTO agents(agent_key,conversation_id) VALUES (?,?)', [
      'chief_of_staff',
      'hung-wake',
    ]);
    data.create_worker_error_wake(sql, {
      target: { kind: 'chief', id: 'chief' },
      ticket_id: ticket.id,
      ticket_title: ticket.title,
      claim_revision: 1,
      error_reason: 'stopped',
      now: 100,
    });
  });
  let started!: () => void;
  const entered = new Promise<void>((resolve) => {
    started = resolve;
  });
  let finish!: (fate: PromptDeliveryFate) => void;
  const provider = new Promise<PromptDeliveryFate>((resolve) => {
    finish = resolve;
  });
  let completed = false;
  vi.spyOn(system, 'send').mockImplementation(async () => {
    started();
    const result = await provider;
    completed = true;
    return result;
  });
  const kill = vi.spyOn(system, 'kill');
  let owned = true;
  const loop = new ManagerWakeLoop({
    workspace_context: { db_path: fixture_path(source), workspace_root: '/workspace' },
    database,
    clock: new TestClock('2026-07-04T12:00:00'),
    conversation_system: system,
    assert_database_owned: () => {
      if (!owned) throw new Error('database ownership ended');
    },
  });
  const pass = loop.poll_once().catch((error: unknown) => error);
  await entered;
  expect(
    database.read((sql) => sql.get<{ status: string }>('SELECT status FROM manager_wake_batches'))
      ?.status,
  ).toBe('offering');
  const before = performance.now();
  await loop.stop({ deadline: performance.now() / 1000 + 0.02 });
  expect(performance.now() - before).toBeLessThan(1000);
  expect(await pass).toBeInstanceOf(ConversationStartAborted);
  expect(
    database.read((sql) =>
      sql.get('SELECT status,conversation_id,process_token FROM manager_wake_batches'),
    ),
  ).toEqual({
    status: 'uncertain',
    conversation_id: 'hung-wake',
    process_token: loop.process_token,
  });
  expect(
    database.read((sql) =>
      sql.get<{ closed_at: number | null }>('SELECT closed_at FROM manager_wakes'),
    )?.closed_at,
  ).toBeNull();
  // Detach must release the actual shared link lock while the core promise stays live.
  let lock_timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    expect(
      await Promise.race([
        conversation_link_lock('agent:chief_of_staff').run(async () => 'lock released'),
        new Promise<never>((_resolve, reject) => {
          lock_timeout = setTimeout(
            () => reject(new Error('wake retained the conversation link lock')),
            1000,
          );
        }),
      ]),
    ).toBe('lock released');
    expect(completed).toBe(false);
  } finally {
    if (lock_timeout !== undefined) clearTimeout(lock_timeout);
  }
  owned = false;
  database.close();
  const after_close = access_count;
  finish(new PromptDeliveryStarted());
  await provider;
  await new Promise<void>((resolve) => setImmediate(resolve));
  expect(completed).toBe(true);
  expect(kill).not.toHaveBeenCalled();
  expect(access_count).toBe(after_close);
});

// A first Chief send uses the canonical new-agent cleanup branch. Unlike the existing
// conversation case, cancellation must kill and unlink it while database ownership lasts.
test('an aborted first Chief send keeps canonical cleanup while database ownership lasts', async () => {
  const source = fixture();
  const ticket = create(source);
  let access_count = 0;
  const database: Database = {
    read<T>(work: (sql: ReadSql) => T): T {
      access_count += 1;
      return source.read(work);
    },
    write<T>(work: (sql: TransactionSql) => T, options?: WriteOptions): T {
      access_count += 1;
      return source.write(work, options);
    },
    close: () => source.close(),
  };
  configure_conversation_start(database, {
    db_path: fixture_path(source),
    workspace_root: '/workspace',
  });
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
  const system = new InMemoryConversationSystem();
  let started!: () => void;
  const entered = new Promise<void>((resolve) => {
    started = resolve;
  });
  let finish!: (fate: PromptDeliveryFate) => void;
  const provider = new Promise<PromptDeliveryFate>((resolve) => {
    finish = resolve;
  });
  let completed = false;
  vi.spyOn(system, 'send').mockImplementation(async () => {
    started();
    const result = await provider;
    completed = true;
    return result;
  });
  const kill = vi.spyOn(system, 'kill');
  let owned = true;
  const loop = new ManagerWakeLoop({
    workspace_context: { db_path: fixture_path(source), workspace_root: '/workspace' },
    database,
    clock: new TestClock('2026-07-04T12:00:00'),
    conversation_system: system,
    assert_database_owned: () => {
      if (!owned) throw new Error('database ownership ended');
    },
  });
  const pass = loop.poll_once().catch((error: unknown) => error);
  await entered;
  const intended = database.read((sql) =>
    sql.get<{ conversation_id: string }>('SELECT conversation_id FROM manager_wake_batches'),
  )!.conversation_id;
  expect(
    database.read((sql) =>
      sql.get<{ conversation_id: string }>(
        "SELECT conversation_id FROM agents WHERE agent_key='chief_of_staff'",
      ),
    )?.conversation_id,
  ).toBe(intended);
  await loop.stop({ deadline: performance.now() / 1000 + 0.02 });
  expect(await pass).toBeInstanceOf(ConversationStartAborted);
  let lock_timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      conversation_link_lock('agent:chief_of_staff').run(async () => {}),
      new Promise<never>((_resolve, reject) => {
        lock_timeout = setTimeout(
          () => reject(new Error('new-Chief cleanup retained the link lock')),
          1000,
        );
      }),
    ]);
  } finally {
    if (lock_timeout !== undefined) clearTimeout(lock_timeout);
  }
  expect(kill).toHaveBeenCalledExactlyOnceWith(intended);
  expect(
    database.read((sql) =>
      sql.get<{ conversation_id: string | null }>(
        "SELECT conversation_id FROM agents WHERE agent_key='chief_of_staff'",
      ),
    )?.conversation_id,
  ).toBeNull();
  expect(
    database.read((sql) =>
      sql.get('SELECT status,conversation_id,process_token FROM manager_wake_batches'),
    ),
  ).toEqual({
    status: 'uncertain',
    conversation_id: intended,
    process_token: loop.process_token,
  });
  expect(
    database.read((sql) =>
      sql.get<{ closed_at: number | null }>('SELECT closed_at FROM manager_wakes'),
    )?.closed_at,
  ).toBeNull();
  expect(completed).toBe(false);
  owned = false;
  database.close();
  const after_release = access_count;
  finish(new PromptDeliveryStarted());
  await provider;
  await new Promise<void>((resolve) => setImmediate(resolve));
  expect(completed).toBe(true);
  expect(access_count).toBe(after_release);
  expect(kill).toHaveBeenCalledTimes(1);
});
