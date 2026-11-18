/** Risk: the home refuses an executor's empty model list answer as malformed. That happened
 * on 17 November 2026, and no test read this rule. The Ticket database and the real mailbox rows.
 */
import { expect, test } from 'vitest';
import { fixture } from '../../tests/ticket_fixture.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../../execution_identity.ts';
import { insert_model_list_request, record } from './mailbox.ts';

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
