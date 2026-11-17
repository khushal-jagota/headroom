/** Distinct gap: list attention reads durable facts for named conversations only. */
import { expect, test } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { readAllDurableFacts, readDurableFactsFor } from '../conversation/publication/durable.ts';

// The read-through mark is read only beside an owner message, and only named rows return.
test('named durable facts match the complete read and omit other conversations', () => {
  const database = fixture();
  database.write((tx) => {
    for (const [id, read_through] of [
      ['read', 1],
      ['unread', 1],
      ['failed', 0],
      ['acknowledged', 0],
      ['quiet', 0],
    ] as const)
      tx.run(
        `INSERT INTO conversations
          (conversation_id,backend_key,model,workspace_folder,access,created_at,owner_read_through_sequence)
          VALUES(?,'codex','model','/synthetic','full',0,?)`,
        [id, read_through],
      );
    const event = (id: string, sequence: number, kind: string, payload = '{}') =>
      tx.run('INSERT INTO conversation_events VALUES(?,?,?,?,0)', [id, sequence, kind, payload]);
    event('read', 1, 'message_to_owner');
    event('unread', 2, 'message_to_owner');
    event('failed', 1, 'turn_ended', '{"ending":"failed"}');
    tx.run("INSERT INTO conversation_background_tasks VALUES('failed','task','Task')");
    event('acknowledged', 1, 'turn_ended', '{"ending":"failed"}');
    tx.run("INSERT INTO conversation_error_acknowledgements VALUES('acknowledged',1)");
  });
  const named = database.read((sql) =>
    readDurableFactsFor(sql, ['unread', 'read', 'failed', 'acknowledged', 'missing']),
  );
  const facts = (message_for_owner: boolean, last_turn_failed: boolean, task: boolean) => ({
    message_for_owner,
    last_turn_failed,
    has_live_background_task: task,
  });
  expect(named).toEqual([
    { conversation_id: 'acknowledged', durable: facts(false, false, false) },
    { conversation_id: 'failed', durable: facts(false, true, true) },
    { conversation_id: 'read', durable: facts(false, false, false) },
    { conversation_id: 'unread', durable: facts(true, false, false) },
  ]);
  expect(database.read(readAllDurableFacts)).toEqual(
    [...named, { conversation_id: 'quiet', durable: facts(false, false, false) }].sort((a, b) =>
      a.conversation_id.localeCompare(b.conversation_id),
    ),
  );
});
