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
    confirm_remote_prompt_accepted: unused,
    reconcile_remote_command: unused,
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

// Reuse the real core/record fixture without a provider process or artifact transport.
// Earlier acknowledgement tests cannot prove that mailbox placement stays pending.
async function remote_fixture(early_result = false) {
  const { registry_of_factories } = await import('../drivers/testing/registry.ts');
  const { MacRemoteChild } = await import('./vps_remote_child.ts');
  const { Deferred } = await import('../core/deferred.ts');
  const { RetainedBackendRun } = await import('../conversation/backends/contracts.ts');
  const reference = { ...answer.child, conversation_id: 'core' };
  const placed = new Deferred<WireValue<'ChildCommand'>>();
  const put_released = new Deferred<void>();
  let may_have_sent = false;
  const proxy = new MacRemoteChild(
    reference,
    {
      async put(command) {
        if (command.operation === 'write_prompt') {
          placed.resolve(command);
          if (early_result) await put_released.promise;
        }
        return true;
      },
      wait_prompt_result(command, options) {
        return runs.wait_prompt_result(command, options);
      },
      mac_is_present: () => true,
      async deliver_steer() {
        throw new Error('unexpected steer');
      },
      async stop(command) {
        return {
          command_id: command.command_id,
          child: reference,
          disposition: 'succeeded',
          result: {},
        };
      },
      async save_answer() {
        throw new Error('unexpected answer');
      },
      async cancel_initial_delivery() {
        return may_have_sent;
      },
      retire_child() {},
    },
    () => ({
      required_skill_names: ['panels', 'panels-worker'],
      sources: ['panels', 'panels-worker'].map((skill_name) => ({
        skill_name,
        source_text: `---\nname: ${skill_name}\ndescription: Test skill\n---\nTest.`,
      })),
    }),
  );
  const factory = Object.assign(() => proxy, {
    recover: () => proxy,
    retained_runs: () => [
      new RetainedBackendRun({
        reference,
        turn: { conversation_id: 'core', turn_number: 0 },
        delivery_uncertain: false,
        retirement_confirmed: false,
        incarnation_current: true,
      }),
    ],
  });
  const f = setup({
    core: {
      backend_registry: registry_of_factories({ codex: factory, claude: factory, hermes: factory }),
    },
  });
  const mailbox = new SqliteCommandMailbox(f.database);
  const presence = new MacPresenceService(f.database, mailbox, { changes: new ChangeSignal() });
  const runs = new MacRuns(f.database, presence, mailbox);
  runs.conversations = f.core;
  await f.start();
  expect(
    f.core.start_admission.try_start(await f.core.automatic_start_request('sender', 'core')),
  ).toBe(true);
  const sending = f.send();
  const command = await placed.promise;
  const state = f.core.conversations.get('core')!;
  if (!early_result)
    await vi.waitFor(() => expect(mailbox.has_waiter(command.command_id)).toBe(true));
  const finish = async (outcome: WireValue<'CommandOutcome'>) => {
    // Native results commit before publication. An early observer can reread the row.
    f.database.write((tx) =>
      tx.run(
        "INSERT INTO executor_commands(command_id,executor_incarnation,operation,conversation_id,child_id,turn_number,command_json,state,created_at_ms,machine_id,outcome_json) VALUES(?,?,?,?,?,?,?,'finished',1,?,?)",
        [
          command.command_id,
          command.child.executor_incarnation,
          command.operation,
          'core',
          command.child.child_id,
          command.turn!.turn_number,
          JSON.stringify({
            command_id: command.command_id,
            command_sequence: 1,
            created_at_vps_ms: 1,
            child_command: command,
          }),
          runs.machine_id,
          JSON.stringify({
            command_id: command.command_id,
            child_outcome: outcome,
            model_list: null,
          }),
        ],
      ),
    );
    put_released.resolve();
    await mailbox.publish_settlements([
      [
        {
          command_id: command.command_id,
          command_sequence: 1,
          created_at_vps_ms: 1,
          child_command: command,
        },
        { command_id: command.command_id, child_outcome: outcome, model_list: null },
      ],
    ]);
    return sending;
  };
  const close = async () => {
    const options = { deadline: performance.now() / 1000 + 2 };
    await Promise.all([
      runs.stop(options),
      presence.stop(options),
      mailbox.stop(options),
      f.core.shutdown(options),
    ]);
    await Promise.all([runs.settled(), presence.settled(), mailbox.settled(), f.core.settled()]);
  };
  return {
    ...f,
    reference,
    proxy,
    command,
    state,
    sending,
    finish,
    close,
    mailbox,
    fetched: () => {
      may_have_sent = true;
    },
  };
}

test.each([
  [
    'succeeded',
    { kind: 'prompt_accepted', composed_content_delivered: true, agent_acceptance_proven: true },
    'PromptDeliveryStarted',
    'started',
  ],
  [
    'succeeded',
    { kind: 'prompt_accepted', composed_content_delivered: true, agent_acceptance_proven: false },
    'PromptDeliveryStarted',
    'started',
  ],
  [
    'refused',
    { error_type: 'PromptWriteRefused', error_message: 'input failed before native send' },
    'PromptDeliveryRefused',
    'refused',
  ],
  [
    'uncertain',
    { error_type: 'PromptWriteFailed', error_message: 'reply lost after native send' },
    'PromptDeliveryUncertain',
    'unknown',
  ],
] as const)(
  'remote admission waits for native %s without an artifact receipt',
  async (disposition, result, fate, status) => {
    const f = await remote_fixture();
    try {
      expect(f.state.phase).toBe('starting');
      expect(await f.core.is_running('core')).toBe(false);
      expect(
        await f.core.run_if_idle('core', () => {
          throw new Error('pending admission is busy');
        }),
      ).toBe(false);
      expect(
        f.store.lifecycle_facts('core').find((fact) => fact.operation === 'prompt')?.status,
      ).toBe('intent');
      expect(
        (
          await f.finish({
            command_id: f.command.command_id,
            child: f.reference,
            disposition,
            result,
          })
        ).constructor.name,
      ).toBe(fate);
      expect(
        f.store.lifecycle_facts('core').find((fact) => fact.operation === 'prompt')?.status,
      ).toBe(status);
      expect(await f.core.is_running('core')).toBe(disposition === 'succeeded');
      expect(
        f.database.read((sql) => sql.all('SELECT identity FROM automatic_start_bindings')),
      ).toEqual(
        disposition === 'refused' ||
          (disposition === 'succeeded' &&
            'agent_acceptance_proven' in result &&
            result.agent_acceptance_proven)
          ? []
          : [{ identity: 'sender' }],
      );
      if (disposition === 'uncertain') {
        expect(f.state.delivery_uncertain).toBe(true);
        expect((await f.send('later', 'later')).constructor.name).toBe('PromptDeliveryQueued');
        expect(f.state.held_prompts).toHaveLength(1);
      }
    } finally {
      await f.close();
    }
  },
);

// A native reply can commit before the initial put observer returns. The result
// wait must reread that durable result instead of missing its notification.
test('a proven native result before waiter registration still completes its receipt', async () => {
  const f = await remote_fixture(true);
  try {
    expect(f.mailbox.has_waiter(f.command.command_id)).toBe(false);
    const fate = await f.finish({
      command_id: f.command.command_id,
      child: f.reference,
      disposition: 'succeeded',
      result: {
        kind: 'prompt_accepted',
        composed_content_delivered: true,
        agent_acceptance_proven: true,
      },
    });
    expect(fate.constructor.name).toBe('PromptDeliveryStarted');
    expect(await f.core.is_running('core')).toBe(true);
  } finally {
    await f.close();
  }
});

// A fetched prompt lacks a result, but Stop must still withdraw observation.
test('cancel unblocks fetched remote admission without claiming non-delivery', async () => {
  const f = await remote_fixture();
  try {
    f.fetched();
    await expect(f.core.kill('core')).rejects.toMatchObject({ code: 'retry_later' });
    expect((await f.sending).constructor.name).toBe('PromptDeliveryUncertain');
    expect(f.state.reserved_turn).toBe(null);
    expect(await f.core.is_running('core')).toBe(false);
    expect(f.state.delivery_uncertain).toBe(true);
  } finally {
    await f.close();
  }
});

// A restored turn can carry the old mailbox-placement receipt. A failed result
// must end that active turn without replacing its historical prompt receipt.
test.each(['refused', 'uncertain'] as const)(
  'a matching %s prompt result ends a stale active turn once',
  async (disposition) => {
    const { TurnEnded } = await import('../conversation/backends/contracts.ts');
    const f = await remote_fixture();
    const outcome = {
      command_id: f.command.command_id,
      child: f.reference,
      disposition,
      result: { error_type: 'PromptWriteRefused', error_message: 'native outcome' },
    };
    try {
      await f.finish({
        ...outcome,
        disposition: 'succeeded',
        result: { kind: 'prompt_accepted', composed_content_delivered: true },
      });
      const token = f.state.running_turn!.token;
      await f.core.reconcile_remote_command(f.command, { ...outcome, command_id: 'stale-command' });
      await f.core.reconcile_remote_command(
        { ...f.command, child: { ...f.reference, child_id: 'stale-child' } },
        { ...outcome, child: { ...f.reference, child_id: 'stale-child' } },
      );
      await f.core.reconcile_remote_command(
        { ...f.command, turn: { ...token, turn_number: token.turn_number + 1 } },
        outcome,
      );
      expect(await f.core.is_running('core')).toBe(true);
      const publish = () =>
        f.mailbox.publish_settlements([
          [
            {
              command_id: f.command.command_id,
              command_sequence: 1,
              created_at_vps_ms: 1,
              child_command: f.command,
            },
            { command_id: f.command.command_id, child_outcome: outcome, model_list: null },
          ],
        ]);
      await publish();
      await vi.waitFor(() => expect(f.state.running_turn).toBe(null));
      const count = f.store.read_events_after('core', 0).length;
      expect(
        f.store.lifecycle_facts('core').find((fact) => fact.operation === 'prompt')?.status,
      ).toBe('started');
      expect(f.store.lifecycle_facts('core').find((fact) => fact.kind === 'turn')?.status).toBe(
        'failed',
      );
      await publish();
      await f.core.reconcile_remote_command(f.command, outcome);
      await f.core.take_backend_event(
        token,
        new TurnEnded({ ending: 'failed', error_summary: 'late synthetic ending' }),
      );
      expect(f.store.read_events_after('core', 0)).toHaveLength(count);
      if (disposition === 'uncertain') expect(f.state.child_is_quarantined).toBe(true);
    } finally {
      await f.close();
    }
  },
);

// This exercises the actual steer receipt barrier, rather than calling its
// reconciliation method after the barrier already completed.
test('a native no-turn refusal crosses the steer barrier and leaves a newer turn intact', async () => {
  const { BackendSteerRefused, TurnEnded } = await import('../conversation/backends/contracts.ts');
  const { text_message_content } = await import('../conversation/message_content.ts');
  const f = setup();
  const mailbox = new SqliteCommandMailbox(f.database);
  const presence = new MacPresenceService(f.database, mailbox, { changes: new ChangeSignal() });
  const runs = new MacRuns(f.database, presence, mailbox);
  runs.conversations = f.core;
  try {
    await f.start();
    await f.send();
    const child = await f.child;
    const state = f.core.conversations.get('core')!;
    const token = state.running_turn!.token;
    const reference = { ...answer.child, conversation_id: 'core' };
    state.retained_child_reference = reference;
    const command: WireValue<'ChildCommand'> = {
      command_id: 'no-turn-steer',
      child: reference,
      turn: token,
      operation: 'steer',
      arguments: {
        kind: 'steer',
        sender_label: 'Owner',
        content: [{ text: 'more' }],
        sender_content: null,
      },
    };
    const outcome: WireValue<'CommandOutcome'> = {
      command_id: command.command_id,
      child: reference,
      disposition: 'succeeded',
      result: { kind: 'steer_refused', refusal_reason: 'no_running_turn_to_steer_into' },
    };
    vi.spyOn(child, 'steer').mockImplementation(async () => {
      await mailbox.publish_settlements([
        [
          {
            command_id: command.command_id,
            command_sequence: 1,
            created_at_vps_ms: 1,
            child_command: command,
          },
          { command_id: command.command_id, child_outcome: outcome, model_list: null },
        ],
      ]);
      expect(state.steer_settlements.size).toBe(1);
      return new BackendSteerRefused({ refusal_reason: 'no_running_turn_to_steer_into' });
    });
    await f.core.send('core', text_message_content('more'), {
      sender_label: 'Owner',
      sender_message_id: 'more',
      mode: 'steer',
    });
    await vi.waitFor(() =>
      expect(state.running_turn?.token.turn_number).toBe(token.turn_number + 1),
    );
    const count = f.store.read_events_after('core', 0).length;
    await f.core.reconcile_remote_command(command, outcome);
    await f.core.take_backend_event(token, new TurnEnded({ ending: 'failed' }));
    expect(state.running_turn?.token.turn_number).toBe(token.turn_number + 1);
    expect(f.store.read_events_after('core', 0)).toHaveLength(count);
    expect(
      f.store
        .lifecycle_facts('core')
        .find((fact) => fact.operation === 'prompt' && fact.turn_number === token.turn_number)
        ?.status,
    ).toBe('started');
    state.retained_child_reference = null;
  } finally {
    const options = { deadline: performance.now() / 1000 + 2 };
    await Promise.all([
      runs.stop(options),
      presence.stop(options),
      mailbox.stop(options),
      f.core.shutdown(options),
    ]);
    await Promise.all([runs.settled(), presence.settled(), mailbox.settled(), f.core.settled()]);
  }
});
