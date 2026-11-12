/** Distinct gaps: real producer commits, rollback, and attachment protocol replace idle guesses. */
import { expect, test } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import type { TransactionSql } from '../core/database/contracts.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import * as persistence from '../conversation/publication/persistence.ts';
import { field_mutation } from '../conversation/runtime/save_attempt.ts';
import { SignalBatch, SignalSnapshot } from '../conversation/contracts.ts';
import type { SignalAttachment } from '../conversation/contracts.ts';
import { installSignal, participateSignal } from '../navigation/publication.ts';

function setup() {
  let producer: SignalPublicationCoordinator | undefined;
  let fail = false;
  let owned = true;
  let changes = 0;
  const database = fixture({
    onChange: () => {
      changes += 1;
    },
    commitSteps: [
      (tx) => producer?.finalize_signal(tx),
      () => {
        if (fail) throw new Error('after publication');
      },
    ],
  });
  database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'navigation',7,'view-epoch',0)");
    tx.run(`INSERT INTO conversations
      (conversation_id,backend_key,model,workspace_folder,access,created_at)
      VALUES('c','codex','model','/synthetic','full',0)`);
  });
  producer = new SignalPublicationCoordinator({
    database,
    require_runtime_ownership: () => {
      if (!owned) throw new Error('lease closed');
    },
  });
  const runtime: {
    turn: {
      pending_permission_ask_ids: Set<string>;
      pending_user_input_questions: Map<string, unknown>;
    } | null;
  } = { turn: null };
  producer.bind_runtime(() => runtime.turn);
  producer.initialize();
  let attachment: SignalAttachment;
  const batches: SignalBatch[] = [];
  const attach = () => {
    attachment = producer!.attach_signal_publication({
      install: (tx, snapshot) =>
        installSignal(tx, snapshot, { rebuild: () => {}, finalize: () => {} }),
      participate: (tx, batch) => {
        participateSignal(tx, batch);
        batches.push(batch);
      },
      unavailable: () => {},
    });
    return attachment;
  };
  attach();
  const attention = () => database.read((sql) => persistence.values(sql)[0].attention);
  const cursor = () => database.read(persistence.cursor);
  const accepted = () =>
    database.read((sql) =>
      sql.get<{ sequence: string; last_batch: string }>(
        'SELECT sequence,last_batch FROM navigation_signal_installation',
      ),
    )!;
  const saved = (work: (tx: TransactionSql) => void) => database.write(work);
  return {
    producer,
    database,
    runtime,
    batches,
    attach,
    attention,
    cursor,
    accepted,
    saved,
    fail: (value: boolean) => {
      fail = value;
    },
    revoke: () => {
      owned = false;
    },
    changes: () => changes,
  };
}

// SQL plus the actual producer proves dirty runtime input that idle publication cannot observe.
test('runtime, asks, background work, unread, and failed acknowledgement share committed facts', () => {
  const f = setup();
  const turn = {
    pending_permission_ask_ids: new Set<string>(['ask']),
    pending_user_input_questions: new Map<string, unknown>(),
  };
  f.producer.save_attempt('c', () => f.saved(() => {}), [field_mutation(f.runtime, 'turn', turn)]);
  expect(f.attention()).toEqual({
    signal: { working: true, message_for_owner: false },
    awaiting_answer: true,
    last_turn_failed: false,
  });
  const question = {
    ...turn,
    pending_permission_ask_ids: new Set<string>(),
    pending_user_input_questions: new Map([['question', {}]]),
  };
  f.producer.save_attempt('c', () => f.saved(() => {}), [
    field_mutation(f.runtime, 'turn', question),
  ]);
  expect(f.attention().awaiting_answer).toBe(true);
  f.producer.save_attempt(
    'c',
    () =>
      f.saved((tx) => {
        tx.run("INSERT INTO conversation_background_tasks VALUES('c','task','Task')");
        tx.run(`INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}',0)`);
        tx.run(
          `INSERT INTO conversation_events VALUES('c',2,'turn_ended','{"ending":"failed"}',0)`,
        );
      }),
    [field_mutation(f.runtime, 'turn', null)],
  );
  expect(f.attention()).toEqual({
    signal: { working: true, message_for_owner: true },
    awaiting_answer: false,
    last_turn_failed: true,
  });
  f.saved((tx) => {
    tx.run("INSERT INTO conversation_error_acknowledgements VALUES('c',2)");
    tx.run("UPDATE conversations SET owner_read_through_sequence=2 WHERE conversation_id='c'");
    tx.run("DELETE FROM conversation_background_tasks WHERE conversation_id='c'");
  });
  expect(f.attention()).toEqual({
    signal: { working: false, message_for_owner: false },
    awaiting_answer: false,
    last_turn_failed: false,
  });
  const accepted = f.database.read((sql) =>
    sql.get<{ attention: string }>(
      "SELECT attention FROM navigation_signal_publications WHERE conversation_id='c'",
    ),
  )!;
  expect(JSON.parse(accepted.attention)).toEqual(f.attention());
});

// Empty replacements still carry source boundaries. This closes a synthetic-idle-cursor gap.
test('unchanged attention and ordered association-only input advance accepted producer cursors', () => {
  const f = setup();
  const before = f.cursor();
  f.saved((tx) => tx.run("UPDATE conversations SET model='other' WHERE conversation_id='c'"));
  expect(f.cursor().sequence).toBe(before.sequence + 1);
  expect(f.batches.at(-1)?.replacements).toEqual([]);
  expect(f.accepted().sequence).toBe(String(f.cursor().sequence));
  f.saved((tx) => {
    for (const owner_id of ['first', 'second'])
      tx.run('INSERT INTO signal_publication_associations(value) VALUES(?)', [
        persistence.encode({
          owner_kind: 'ticket',
          owner_id,
          relation: 'current',
          before_conversation_id: null,
          after_conversation_id: 'c',
        }),
      ]);
  });
  expect(f.cursor().sequence).toBe(before.sequence + 2);
  expect(f.batches.at(-1)?.associations.map((item) => item.owner_id)).toEqual(['first', 'second']);
  expect(f.database.read((sql) => sql.all('SELECT * FROM signal_publication_dirty'))).toEqual([]);
  expect(
    f.database.read((sql) => sql.all('SELECT * FROM signal_publication_associations')),
  ).toEqual([]);
  expect(
    f.database.read((sql) =>
      sql.get<{ revision: number }>(
        "SELECT revision FROM signal_publication_values WHERE conversation_id='c'",
      ),
    )?.revision,
  ).toBe(0);
});

// A later participant failure must undo SQL and runtime before storage-only retry.
test('failed commit restores runtime and both cursors, then one retry publishes once', () => {
  const f = setup();
  const before = f.cursor();
  const accepted = f.accepted();
  const changes = f.changes();
  const next = {
    pending_permission_ask_ids: new Set<string>(),
    pending_user_input_questions: new Map<string, unknown>(),
  };
  const attempt = () =>
    f.producer.save_attempt(
      'c',
      () =>
        f.saved((tx) => tx.run("UPDATE conversations SET model='next' WHERE conversation_id='c'")),
      [field_mutation(f.runtime, 'turn', next)],
    );
  f.fail(true);
  expect(attempt).toThrow('after publication');
  expect(f.runtime.turn).toBeNull();
  expect(f.cursor()).toEqual(before);
  expect(f.accepted()).toEqual(accepted);
  expect(f.attention().signal.working).toBe(false);
  expect(f.changes()).toBe(changes);
  f.fail(false);
  // A later unrelated write proves the failed attempt context did not leak.
  f.saved((tx) => tx.run("UPDATE projects SET name='Renamed' WHERE id='project_other'"));
  expect(f.cursor()).toEqual(before);
  attempt();
  expect(f.runtime.turn).toBe(next);
  expect(f.cursor().sequence).toBe(before.sequence + 1);
  expect(f.accepted().sequence).toBe(String(f.cursor().sequence));
  // Nested synchronous scopes must restore their parent context, not clear it.
  f.producer.save_attempt(
    'c',
    () => {
      expect(() =>
        f.producer.save_attempt(
          'missing',
          () => {
            throw new Error('inner');
          },
          [],
        ),
      ).toThrow('inner');
      f.saved(() => {});
    },
    [field_mutation(f.runtime, 'turn', null)],
  );
  expect(f.attention().signal.working).toBe(false);
});

// Replay and deletion fences cannot be reached through valid ordinary HTTP writers.
test('navigation accepts exact replay, rejects gaps/conflicts/stale revisions, and retains tombstones', () => {
  const f = setup();
  f.saved((tx) => tx.run("UPDATE conversations SET model='other' WHERE conversation_id='c'"));
  const batch = f.batches.at(-1)!;
  f.saved((tx) => participateSignal(tx, batch));
  expect(() =>
    f.saved((tx) =>
      participateSignal(
        tx,
        new SignalBatch({
          ...batch,
          associations: [
            {
              owner_kind: 'ticket',
              owner_id: 'different',
              relation: 'current',
              before_conversation_id: null,
              after_conversation_id: 'c',
            },
          ],
        }),
      ),
    ),
  ).toThrow('conflicting Signal batch');
  expect(() =>
    f.saved((tx) =>
      participateSignal(
        tx,
        new SignalBatch({
          ...batch,
          base_cursor: { ...batch.cursor, sequence: batch.cursor.sequence + 1 },
          cursor: { ...batch.cursor, sequence: batch.cursor.sequence + 2 },
        }),
      ),
    ),
  ).toThrow('Signal cursor gap');
  expect(() =>
    f.saved((tx) =>
      participateSignal(
        tx,
        new SignalBatch({
          base_cursor: batch.cursor,
          cursor: { ...batch.cursor, sequence: batch.cursor.sequence + 1 },
          replacements: [],
          associations: [],
          deletions: [
            {
              conversation_id: 'c',
              runtime_epoch: batch.cursor.runtime_epoch,
              conversation_revision: 0,
            },
          ],
        }),
      ),
    ),
  ).toThrow('obsolete Signal conversation fence');
  f.saved((tx) => tx.run("DELETE FROM conversations WHERE conversation_id='c'"));
  expect(
    f.database.read((sql) => sql.get('SELECT * FROM navigation_signal_publications')),
  ).toMatchObject({ conversation_id: 'c', attention: null, conversation_revision: '1' });
});

// Real attachment permits retain producer history, while failed installs never admit a new generation.
test('failed install, detach, and reattach fence permits without resetting producer history', () => {
  const f = setup();
  const old = f.producer.capture();
  const invalidated: string[] = [];
  f.producer.watch(old, () => {
    invalidated.push('failed');
    throw new Error('watch failed');
  });
  f.producer.watch(old, () => {
    invalidated.push('observed');
  });
  expect(() =>
    f.producer.attach_signal_publication({
      install: () => {
        throw new Error('install failed');
      },
      participate: participateSignal,
      unavailable: () => {},
    }),
  ).toThrow('install failed');
  f.producer.validate(old);
  expect(invalidated).toEqual([]);
  const empty = new SignalSnapshot({
    installation_id: 'incomplete',
    cursor: f.cursor(),
    values: [],
  });
  expect(() =>
    f.saved((tx) => installSignal(tx, empty, { rebuild: () => {}, finalize: () => {} })),
  ).toThrow('complete conversation set');
  // Successful replacement immediately notifies the prior generation, without
  // waiting for a later close or source write. Each watch fires exactly once.
  const current = f.attach();
  expect(invalidated).toEqual(['failed', 'observed']);
  expect(() => f.producer.validate(old)).toThrow();
  current.close();
  expect(invalidated).toEqual(['failed', 'observed']);
  f.saved((tx) => tx.run("UPDATE conversations SET model='detached' WHERE conversation_id='c'"));
  const before = f.cursor();
  const replacement = f.attach();
  expect(f.cursor()).toEqual(before);
  expect(f.accepted().sequence).toBe(String(before.sequence));
  expect(replacement.capture_permit().installation_id).not.toBe(old.permit.installation_id);
  expect(() => current.capture_permit()).toThrow('attachment is not active');
  f.producer.validate(f.producer.capture());
  f.revoke();
  expect(() => replacement.capture_permit()).toThrow('lease closed');
});
