import { expect, test } from 'vitest';
import { fixture } from '../tests/ticket_fixture.ts';
import type { ReadSql, TransactionSql } from '../core/database/index.ts';
import {
  advance_conversation_attention,
  conversation_attention,
  conversation_attention_snapshot,
  replay_conversation_attention,
} from './attention.ts';

const ID = 'kept-attention';
function insert_conversation(tx: TransactionSql): void {
  tx.run(
    `INSERT INTO conversations
    (conversation_id,backend_key,model,workspace_folder,access,created_at)
    VALUES(?,'codex','fixture','/synthetic','full',100)`,
    [ID],
  );
}
/** The same steps the conversation store takes: prepare, insert, then advance. */
function append(tx: TransactionSql, payloads: Record<string, unknown>[], at: number) {
  const snapshot = conversation_attention_snapshot(tx, ID)!;
  const { latest_sequence } = tx.get<{ latest_sequence: number }>(
    'SELECT latest_sequence FROM conversations WHERE conversation_id=?',
    [ID],
  )!;
  const written = payloads.map((payload, index) => {
    const sequence = latest_sequence + index + 1;
    tx.run('INSERT INTO conversation_events VALUES (?,?,?,?,?)', [
      ID,
      sequence,
      payload.kind as string,
      JSON.stringify(payload),
      at,
    ]);
    return { sequence, created_at: at, payload: payload as { kind: string } };
  });
  tx.run('UPDATE conversations SET latest_sequence=? WHERE conversation_id=?', [
    latest_sequence + payloads.length,
    ID,
  ]);
  return advance_conversation_attention(tx, snapshot, written, {
    owner_read_through_sequence: 0,
    occurred_at: at,
  });
}
function replayed(sql: ReadSql) {
  const snapshot = replay_conversation_attention(sql, ID)!;
  return {
    unread_message: snapshot.latest_message_sequence > snapshot.owner_read_through_sequence,
    pending_ask: !!(
      snapshot.pending_permission_ask_ids.size || snapshot.pending_user_input_request_ids.size
    ),
    errored: snapshot.errored,
    latest_sequence: snapshot.latest_sequence,
    occurred_at: snapshot.latest_event_at,
  };
}
const EVENTS: Record<string, unknown>[] = [
  { kind: 'prompt' },
  { kind: 'permission_asked', ask_id: 'a' },
  { kind: 'permission_answered', ask_id: 'a' },
  { kind: 'user_input_requested', request_id: 'r' },
  { kind: 'user_input_failed', request_id: 'r' },
  { kind: 'user_input_answered', request_id: 'r' },
  { kind: 'message_to_owner' },
  { kind: 'turn_ended', ending: 'failed' },
  { kind: 'turn_ended', ending: 'completed' },
  { kind: 'assistant_text' },
];

// Risk: the kept state drifts from the history, including after a rolled-back write.
test('kept attention equals the full replay after each write and after a rollback', () => {
  const db = fixture();
  db.write(insert_conversation);
  let seed = 7;
  const next = () => (seed = (seed * 1103515245 + 12345) % 2147483648);
  for (let step = 0; step < 300; step++) {
    const payloads = Array.from(
      { length: 1 + (next() % 3) },
      () => EVENTS[next() % EVENTS.length]!,
    );
    if (step % 50 === 25) {
      // The kept state advances inside the transaction, then the write rolls back.
      expect(() =>
        db.write((tx) => {
          append(tx, [{ kind: 'permission_asked', ask_id: `rolled-${step}` }], 1000 + step);
          throw new Error('roll back');
        }),
      ).toThrow('roll back');
    }
    const advanced = db.write((tx) => append(tx, payloads, 1000 + step));
    db.read((sql) => {
      const replay = replayed(sql);
      expect({ ...advanced, occurred_at: replay.occurred_at }).toEqual(replay);
      expect(conversation_attention(sql, ID).get(ID)).toEqual(replay);
    });
  }
});

// Risk: an append still reads the whole history.
test('one append to a 20,000-event conversation reads only the head rows', () => {
  const db = fixture();
  db.write((tx) => {
    insert_conversation(tx);
    for (let sequence = 1; sequence <= 20_000; sequence++)
      tx.run('INSERT INTO conversation_events VALUES (?,?,?,?,?)', [
        ID,
        sequence,
        'assistant_text',
        '{"kind":"assistant_text","text":"x"}',
        200,
      ]);
    tx.run('UPDATE conversations SET latest_sequence=20000 WHERE conversation_id=?', [ID]);
  });
  db.read((sql) => conversation_attention_snapshot(sql, ID)); // The first use after a start replays.
  let rows = 0;
  db.write((tx) => {
    const counted: TransactionSql = {
      ...tx,
      get: (statement, parameters) => {
        const row = tx.get(statement, parameters);
        rows += row === undefined ? 0 : 1;
        return row as never;
      },
      all: (statement, parameters) => {
        const all = tx.all(statement, parameters);
        rows += all.length;
        return all as never;
      },
    };
    expect(append(counted, [{ kind: 'message_to_owner' }], 300)).toMatchObject({
      unread_message: true,
      latest_sequence: 20_001,
    });
  });
  // The kept-state check, the sequence lookup in this test, and the head after the write.
  expect(rows).toBe(3);
});
