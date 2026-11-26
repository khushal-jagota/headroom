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

// A target can change after insertion and before the next machine poll.
// The real fetch transaction must withhold that exact stale native attempt.
test('machine fetch withdraws an obsolete recipient attempt before the machine can act', async () => {
  const database = fixture();
  const incarnation = '11111111-1111-4111-8111-111111111111';
  const child = { executor_incarnation: incarnation, child_id: 'child', conversation_id: 'core' };
  const command = {
    command_id: 'stale-command',
    command_sequence: 1,
    created_at_vps_ms: 1,
    child_command: {
      command_id: 'stale-command',
      child,
      operation: 'write_prompt',
      turn: { conversation_id: 'core', turn_number: 1 },
      arguments: {
        kind: 'write_prompt',
        content: [{ piece: 'text', text: 'old input' }],
        sender_content: [{ piece: 'text', text: 'old input' }],
        sender_label: 'Owner',
        sender_message_count: 1,
        mode: 'queue',
        model_change: null,
        reasoning_effort_change: null,
        reasoning_effort_is_set: false,
        automatic_compaction: false,
      },
      file_dependencies: [],
    },
  };
  database.write((tx) => {
    tx.run(
      'INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,machine_id) VALUES(?,0,?)',
      [incarnation, ORIGINAL_MAC_MACHINE_ID],
    );
    tx.run(
      'INSERT OR REPLACE INTO mac_executor_presence(machine_id,executor_incarnation) VALUES(?,?)',
      [ORIGINAL_MAC_MACHINE_ID, incarnation],
    );
    tx.run(
      "INSERT INTO recipient_deliveries(submission_id,recipient_kind,recipient_id,conversation_id,generation,payload,state,attempt_id) VALUES('submission','conversation','core','core','{}','{}','dispatching','new-attempt')",
    );
    tx.run(
      "INSERT INTO executor_commands(command_id,executor_incarnation,operation,conversation_id,child_id,turn_number,command_json,state,created_at_ms,machine_id) VALUES('stale-command',?,'write_prompt','core','child',1,?,'waiting',1,?)",
      [incarnation, JSON.stringify(command), ORIGINAL_MAC_MACHINE_ID],
    );
    tx.run(
      "INSERT INTO recipient_delivery_commands VALUES('stale-command','submission','old-attempt')",
    );
  });
  const prepared = await prepare_fetch(
    database.read((sql) => read_fetch_rows(sql, incarnation, 0)),
  );
  const polled = database.write((tx) => fetch(tx, incarnation, 0, 2, 2, prepared));
  expect(polled?.reply?.commands).toEqual([]);
  expect(
    database.read((sql) => settled_row(sql, 'stale-command'))?.result?.child_outcome,
  ).toMatchObject({ disposition: 'refused', result: { reason: 'delivery_withdrawn' } });
});
