/** Risk: the home refuses an executor's empty model list answer as malformed. That happened
 * on 17 November 2026, and no test read this rule. The Ticket database and the real mailbox rows.
 */
import { expect, test } from 'vitest';
import { fixture } from '../../tests/ticket_fixture.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../../execution_identity.ts';
import {
  insert_model_list_request,
  insert_work_move_request,
  read_fetch_rows,
  prepare_fetch,
  fetch,
  record,
  settled_row,
} from './mailbox.ts';

test('an empty model list answer is recorded', () => {
  const database = fixture();
  const incarnation = '11111111-1111-4111-8111-111111111111';
  const command = database.write((tx) => {
    tx.run(
      'INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,machine_id) VALUES(?,0,?)',
      [incarnation, ORIGINAL_MAC_MACHINE_ID],
    );
    tx.run(
      'INSERT OR REPLACE INTO mac_executor_presence(machine_id,executor_incarnation) VALUES(?,?)',
      [ORIGINAL_MAC_MACHINE_ID, incarnation],
    );
    return insert_model_list_request(tx, { backend_key: 'claude', refresh: false }, 1);
  });
  // The executor could not read its list, or its admission was stopped.
  const result = database.write((tx) =>
    record(
      tx,
      incarnation,
      [{ command_id: command.command_id, model_list: null, child_outcome: null }],
      2,
    ),
  );
  expect(result.reply).toEqual({ recorded: [command.command_id], rejected: [] });
  expect(
    database.read((sql) =>
      sql.get('SELECT state FROM executor_commands WHERE command_id=?', [command.command_id]),
    ),
  ).toEqual({ state: 'finished' });
});

// Gap: a source inspection has no child. Existing model-list records did not exercise its poll path.
test('a childless move inspection crosses the real mailbox poll and retains its source proof', async () => {
  const database = fixture();
  const incarnation = '11111111-1111-4111-8111-111111111111';
  const command = database.write((tx) => {
    tx.run(
      'INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,machine_id) VALUES(?,0,?)',
      [incarnation, ORIGINAL_MAC_MACHINE_ID],
    );
    tx.run(
      'INSERT OR REPLACE INTO mac_executor_presence(machine_id,executor_incarnation) VALUES(?,?)',
      [ORIGINAL_MAC_MACHINE_ID, incarnation],
    );
    return insert_work_move_request(
      tx,
      { owner_kind: 'ticket', owner_id: 't_01234567' },
      1,
      ORIGINAL_MAC_MACHINE_ID,
    );
  });
  const prepared = await prepare_fetch(
    database.read((sql) => read_fetch_rows(sql, incarnation, 0)),
  );
  const polled = database.write((tx) => fetch(tx, incarnation, 0, 2, 2, prepared));
  expect(polled?.reply?.commands).toEqual([command]);
  const proof = { published_branch: 'ticket/published', published_revision: 'a'.repeat(40) };
  const outcome = { command_id: command.command_id, work_move_result: { proof, error: null } };
  const recorded = database.write((tx) => record(tx, incarnation, [outcome], 3));
  expect(recorded.reply).toEqual({ recorded: [command.command_id], rejected: [] });
  expect(
    database.read((sql) => settled_row(sql, command.command_id))?.result?.work_move_result,
  ).toEqual({ proof, error: null });
});
