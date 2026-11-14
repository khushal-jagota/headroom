import { expect, test, vi } from 'vitest';
import { fixture } from '../tests/ticket_fixture.ts';
import { setup } from '../tests/conversation_core_fixture.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { admit_remote_turn } from '../conversation/runtime/remote_turn.ts';
import { MacPresenceService } from './vps_presence.ts';
import { SqliteCommandMailbox } from './vps_mailbox.ts';
import { MacRuns } from './vps_runs.ts';
import type { WireValue } from './contracts.ts';
import type { Database } from '../core/database/contracts.ts';

function observe(real: Database) {
  const reads = vi.fn(),
    writes = vi.fn();
  const database: Database = {
    read(work) {
      reads();
      return real.read(work);
    },
    write(work, options) {
      writes();
      return real.write(work, options);
    },
    close() {
      real.close();
    },
  };
  return { database, reads, writes };
}

const answer: WireValue<'ChildCommand'> = {
  command_id: 'answer',
  operation: 'answer_permission_ask',
  child: {
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    conversation_id: 'remote',
    child_id: 'child',
  },
  turn: { conversation_id: 'remote', turn_number: 1 },
  arguments: { kind: 'answer_permission_ask', ask_id: 'ask', option_id: 'allow' },
  file_dependencies: [],
};

// A missing attachment must fail before it can reconstruct drains or observe a lease.
test('run startup refuses missing conversations before effects', async () => {
  const { database, reads, writes } = observe(fixture());
  const mailbox = new SqliteCommandMailbox(database);
  const presence = new MacPresenceService(database, mailbox, { changes: new ChangeSignal() });
  const runs = new MacRuns(database, presence, mailbox);
  const lease = vi.spyOn(presence, 'current_identity');
  try {
    await expect(runs.resume_replacement_drains()).rejects.toThrow(
      'require attached conversations',
    );
    await expect(runs.incarnation_replaced('old', 'fresh')).rejects.toThrow(
      'require attached conversations',
    );
    await expect(runs.save_answer(answer)).rejects.toThrow('require attached conversations');
    expect([reads.mock.calls.length, writes.mock.calls.length, lease.mock.calls.length]).toEqual([
      0, 0, 0,
    ]);
    expect(runs.children.size).toBe(0);
  } finally {
    const options = { deadline: performance.now() / 1000 + 2 };
    await Promise.all([runs.stop(options), presence.stop(options), mailbox.stop(options)]);
    await Promise.all([runs.settled(), presence.settled(), mailbox.settled()]);
  }
});

// The helper predicate alone did not prove that save_answer applies it before the core call.
test('answer admission refuses an exact unsafe turn before the core callback', async () => {
  const { database: real, core } = setup();
  const { database, reads, writes } = observe(real);
  const mailbox = new SqliteCommandMailbox(database);
  const presence = new MacPresenceService(database, mailbox, { changes: new ChangeSignal() });
  const runs = new MacRuns(database, presence, mailbox);
  const emit = vi.spyOn(core, 'emit_answer');
  const unused = async () => {
    throw new Error('No other core callback belongs to answer admission');
  };
  runs.conversations = {
    emit_answer: core.emit_answer.bind(core),
    retire_replaced_remote_child: unused,
    resume_undelivered_prompt: unused,
    await_late_steer: unused,
    confirm_remote_child_termination: unused,
    stop_interrupted_remote_child: unused,
  };
  try {
    await expect(
      runs.save_answer({
        ...answer,
        turn: { conversation_id: 'remote', turn_number: Number.MAX_SAFE_INTEGER + 1 },
      }),
    ).rejects.toThrow('Remote turn number exceeds the safe integer range');
    expect(emit).not.toHaveBeenCalled();
    expect([reads.mock.calls.length, writes.mock.calls.length]).toEqual([0, 0]);
    expect(
      admit_remote_turn({ conversation_id: 'remote', turn_number: Number.MAX_SAFE_INTEGER }),
    ).toEqual({ conversation_id: 'remote', turn_number: Number.MAX_SAFE_INTEGER });
  } finally {
    const options = { deadline: performance.now() / 1000 + 2 };
    await Promise.all([
      runs.stop(options),
      presence.stop(options),
      mailbox.stop(options),
      core.shutdown(options),
    ]);
    await Promise.all([runs.settled(), presence.settled(), mailbox.settled(), core.settled()]);
  }
});
