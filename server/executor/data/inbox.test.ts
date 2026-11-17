/** Gap: a transient domain refusal must roll back without sealing an executor receipt.
 * Reuse the Ticket database and real inbox savepoint; file publication is outside this case.
 */
import { expect, test, vi } from 'vitest';
import { fixture, create, edit, propose, read, now } from '../../tests/ticket_fixture.ts';
import { CHIEF, PlannerError } from '../../core/contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID, type ExecutorIdentity } from '../../execution_identity.ts';
import { buildClock } from '../../core/clock.ts';
import { loadConfig } from '../../core/config.ts';
import { InboxStore } from './inbox.ts';
import { MacInbox } from '../vps_inbox.ts';
import { write_ticket_mutation } from '../vps_agent_writes.ts';
import type { FileStaging } from '../vps_files.ts';
import type { CliWrite, QueuedEnvelope } from '../wire.generated.ts';

test('send-back commits once with its receipt and a retry-later leaves no effects or receipt', () => {
  const database = fixture();
  const parked = propose(
    database,
    edit(database, create(database, { manager_kind: 'chief' }), { ceiling_holder: CHIEF }),
    'Review this proposal',
  );
  // Reuse the existing decision-replay fixture's bound-session setup; no backend starts.
  database.write((tx) =>
    tx.run("UPDATE tickets SET conversation_id='review-conversation' WHERE id=?", [parked.id]),
  );
  const ticket = read(database, parked.id);
  const identity: ExecutorIdentity = {
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    application_revision: 'test',
    protocol_version: 26,
    spool_format_version: 5,
    connection_generation: 0,
  };
  database.write((tx) => {
    tx.run(
      'INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,machine_id) VALUES(?,0,?)',
      [identity.executor_incarnation, ORIGINAL_MAC_MACHINE_ID],
    );
    tx.run(
      'INSERT OR REPLACE INTO mac_executor_presence(machine_id,executor_incarnation) VALUES(?,?)',
      [ORIGINAL_MAC_MACHINE_ID, identity.executor_incarnation],
    );
  });
  const write: CliWrite = {
    operation: 'ticket.send_back',
    arguments: {
      ticket_id: ticket.id,
      body: {
        message: 'Exact guidance café',
        expected_proposal_revision: ticket.pending_proposal_revision,
        decision_id: 'send-back-once',
      },
    },
    child: {
      executor_incarnation: identity.executor_incarnation,
      child_id: 'reviewer',
      conversation_id: 'review-conversation',
    },
    turn: null,
  };
  const envelope: QueuedEnvelope = {
    conversation_id: write.child.conversation_id,
    kind: 'cli_write',
    payload: { ...write },
    sender_id: 'reviewer',
    sequence: 1,
    submission_id: 'send-back-once',
    file_dependencies: [],
  };
  // check_files:false selects the existing transaction seam and invokes no file methods.
  const inbox = new InboxStore(database, { machine_id: ORIGINAL_MAC_MACHINE_ID } as FileStaging);
  const options = { identity, check_files: false };
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_FAKE_NOW: new Date(now * 1000).toISOString(),
  });
  expect(() =>
    inbox.accept(
      envelope,
      (tx) => {
        tx.run("UPDATE tickets SET recap='uncommitted' WHERE id=?", [ticket.id]);
        throw new PlannerError('retry_later', 'Database contention');
      },
      now * 1000,
      options,
    ),
  ).toThrow('Database contention');
  expect(read(database, ticket.id)).toEqual(ticket);
  expect(database.read((sql) => sql.get('SELECT 1 FROM mac_inbox_positions'))).toBeUndefined();
  const receipt = inbox.accept(
    envelope,
    (tx) => write_ticket_mutation(tx, write, CHIEF, buildClock(config)),
    now * 1000,
    options,
  );
  expect(receipt.disposition, JSON.stringify(receipt.result)).toBe('accepted');
  expect(read(database, ticket.id).pending_proposal).toBeNull();
  expect(
    database
      .read((sql) =>
        sql.all<{ message: string }>(
          'SELECT message FROM ticket_decision_messages WHERE ticket_id=? AND decision_id=?',
          [ticket.id, 'send-back-once'],
        ),
      )
      .map((row) => row.message),
  ).toEqual(['Exact guidance café']);
  expect(
    inbox.accept(
      envelope,
      () => {
        throw new Error('A receipt retry must not write again');
      },
      now * 1000 + 1,
      options,
    ),
  ).toEqual(receipt);
});

// Gap: a durable receipt can precede completion of an item's post-commit work.
// Hold that tail at the consumer seam; reuse the real inbox/database without a host.
test('a duplicate reads its committed receipt while new work still waits for the original task', async () => {
  const database = fixture();
  const identity: ExecutorIdentity = {
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    application_revision: 'test',
    protocol_version: 26,
    spool_format_version: 5,
    connection_generation: 0,
  };
  database.write((tx) => {
    tx.run(
      'INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,machine_id) VALUES(?,0,?)',
      [identity.executor_incarnation, ORIGINAL_MAC_MACHINE_ID],
    );
    tx.run(
      'INSERT OR REPLACE INTO mac_executor_presence(machine_id,executor_incarnation) VALUES(?,?)',
      [ORIGINAL_MAC_MACHINE_ID, identity.executor_incarnation],
    );
  });
  const envelope: QueuedEnvelope = {
    conversation_id: 'delivery-conversation',
    kind: 'cli_write',
    payload: {
      child: {
        executor_incarnation: identity.executor_incarnation,
        child_id: 'worker',
        conversation_id: 'delivery-conversation',
      },
      operation: 'message.send',
      arguments: { body: { target: { kind: 'owner' }, message: 'Deliver once' } },
      turn: null,
    },
    sender_id: 'sender',
    sequence: 1,
    submission_id: 'delivered-once',
    file_dependencies: [],
  };
  const store = new InboxStore(database, { machine_id: ORIGINAL_MAC_MACHINE_ID } as FileStaging);
  const inbox = new MacInbox(store, buildClock(loadConfig(undefined, { PLAN_TEST_MODE: '1' })), {
    authority: () => CHIEF,
    validate_stop() {},
    async accept_stop_receipt() {},
  });
  const gate = Promise.withResolvers<void>();
  const committed = Promise.withResolvers<void>();
  const consume = vi.spyOn(inbox.agent_writes, 'accept').mockImplementation(async (item) => {
    const receipt = store.accept(item, () => ({ delivered: true }), now * 1000, {
      identity,
      check_files: false,
    });
    committed.resolve();
    await gate.promise;
    return receipt;
  });
  try {
    const initial = inbox.accept(envelope, identity, {
      retry_later_at: performance.now() / 1000 + 0.01,
    });
    // Observe the failure before awaiting the commit, to avoid an unhandled rejection.
    const timed_out = expect(initial).rejects.toMatchObject({ code: 'retry_later' });
    await committed.promise;
    await timed_out;
    const stored = store.preflight(envelope, { identity });
    expect(stored?.disposition).toBe('accepted');
    await expect(
      inbox.accept(envelope, identity, { retry_later_at: performance.now() / 1000 }),
    ).resolves.toEqual(stored);
    await expect(
      inbox.accept(envelope, { ...identity, executor_incarnation: 'not-current' }),
    ).rejects.toMatchObject({ code: 'executor_incarnation_not_current' });
    await expect(
      inbox.accept({ ...envelope, sequence: 2, submission_id: 'following' }, identity, {
        retry_later_at: performance.now() / 1000,
      }),
    ).rejects.toMatchObject({ code: 'retry_later' });
    // Stop replays must still run their retained stop consequence through the normal path.
    await expect(
      inbox.accept({ ...envelope, kind: 'child_stopped' }, identity, {
        retry_later_at: performance.now() / 1000,
      }),
    ).rejects.toMatchObject({ code: 'retry_later' });
    expect(consume).toHaveBeenCalledTimes(1);
  } finally {
    gate.resolve();
    await inbox.stop({ deadline: performance.now() / 1000 + 2 });
    await inbox.settled();
  }
});
