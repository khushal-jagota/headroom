// A key request stops on the owner as a permission ask or a question does, so the Ticket and
// manager rows must read awaiting_answer while it is pending, and stop once it is answered.
// Working is left to the turn: a pending key request does not change it.
import { expect, test } from 'vitest';
import type { Database } from '../core/database/contracts.ts';
import { add_work_attention } from '../work_attention.ts';
import { fixture, now } from './ticket_fixture.ts';

function seed(database: Database, state: string): void {
  database.write((tx) => {
    tx.run(
      "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) VALUES('c-key','claude','/tmp','full',?)",
      [now],
    );
    tx.run("INSERT OR REPLACE INTO navigation_records(kind,id,value) VALUES('ticket','t-key',?)", [
      JSON.stringify({ conversation_id: 'c-key', ticket_status: 'agent', stage: 'build' }),
    ]);
    tx.run(
      "INSERT INTO credential_requests VALUES('r-1','c-key','ticket','t-key','{}',?,?,?)",
      [JSON.stringify({ kind: 'credential_request', request_id: 'r-1', state }), now, now],
    );
  });
}

async function attention(database: Database) {
  const rows: Record<string, unknown>[] = [{ id: 't-key' }];
  await add_work_attention(database, undefined, rows);
  return rows[0]!;
}

test('a pending key request reads as awaiting your answer, not as working', async () => {
  const database = fixture();
  seed(database, 'pending');
  const row = await attention(database);
  expect(row.awaiting_answer).toBe(true);
  expect(row.conversation_signal).toEqual({ working: false, message_for_owner: false });
});

test('an answered key request no longer waits on you', async () => {
  for (const state of ['saved', 'rejected', 'replaced', 'reused']) {
    const database = fixture();
    seed(database, state);
    expect((await attention(database)).awaiting_answer).toBe(false);
  }
});
