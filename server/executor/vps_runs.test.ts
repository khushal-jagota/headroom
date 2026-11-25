import { expect, test, vi } from 'vitest';
import { dirname } from 'node:path';
import { fixture, fixture_path, create, read } from '../tests/ticket_fixture.ts';
import { setup } from '../tests/conversation_core_fixture.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { OWNER } from '../core/contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import { configure_conversation_start } from '../runtime/conversation_start.ts';
import { start_ready_worker_step } from '../runtime/worker_step_readiness_loop.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { explicit_start } from '../conversation/start_admission/index.ts';
import { restart_worker } from '../tickets/worker_restart.ts';
import { for_every_built_in_backend, registry_of_factories } from '../drivers/testing/registry.ts';
import type {
  BackendChildFactory,
  BackendEventSink,
  ChildReference,
  RetainedBackendChildFactory,
} from '../conversation/backends/contracts.ts';
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
  const { for_every_built_in_backend, registry_of_factories } =
    await import('../drivers/testing/registry.ts');
  const { MacRemoteChild } = await import('./vps_remote_child.ts');
  const { Deferred } = await import('../core/deferred.ts');
  const { RetainedBackendRun } = await import('../conversation/backends/contracts.ts');
  const reference = { ...answer.child, conversation_id: 'core' };
  const placed = new Deferred<WireValue<'ChildCommand'>>();
  const put_released = new Deferred<void>();
  const prompts: WireValue<'ChildCommand'>[] = [];
  let steer = async (
    _command: WireValue<'ChildCommand'>,
  ): Promise<WireValue<'CommandOutcome'> | null> => {
    throw new Error('unexpected steer');
  };
  let may_have_sent = false;
  let present = true;
  const commands: import('./vps_remote_child.ts').RemoteChildCommands = {
    async put(command) {
      if (command.operation === 'write_prompt') {
        prompts.push(command);
        placed.resolve(command);
        if (early_result) await put_released.promise;
      }
      return true;
    },
    wait_prompt_result(command, options) {
      return runs.wait_prompt_result(command, options);
    },
    mac_is_present: () => present,
    deliver_steer(command) {
      return steer(command);
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
  };
  const skill_snapshot = () => ({
    required_skill_names: ['panels', 'panels-worker'],
    sources: ['panels', 'panels-worker'].map((skill_name) => ({
      skill_name,
      source_text: `---\nname: ${skill_name}\ndescription: Test skill\n---\nTest.`,
    })),
  });
  let retained_pending: WireValue<'ChildCommand'> | null = null;
  const proxy = new MacRemoteChild(reference, commands, skill_snapshot);
  const factory = Object.assign(() => proxy, {
    recover: () =>
      new MacRemoteChild(reference, commands, skill_snapshot, { pending_prompt: retained_pending }),
    retained_runs: () => [
      new RetainedBackendRun({
        reference,
        turn: retained_pending?.turn ?? { conversation_id: 'core', turn_number: 0 },
        pending_prompt: retained_pending,
        delivery_uncertain: false,
        retirement_confirmed: false,
        incarnation_current: true,
      }),
    ],
  });
  const f = setup({
    core: {
      backend_registry: registry_of_factories(for_every_built_in_backend(() => factory)),
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
  const finish = async (outcome: WireValue<'CommandOutcome'>, target = command) => {
    const envelope = {
      command_id: target.command_id,
      command_sequence: prompts.indexOf(target) + 1,
      created_at_vps_ms: 1,
      child_command: target,
    };
    // Native results commit before publication. An early observer can reread the row.
    f.database.write((tx) =>
      tx.run(
        "INSERT INTO executor_commands(command_id,executor_incarnation,operation,conversation_id,child_id,turn_number,command_json,state,created_at_ms,machine_id,outcome_json) VALUES(?,?,?,?,?,?,?,'finished',1,?,?) ON CONFLICT(command_id) DO UPDATE SET state='finished',outcome_json=excluded.outcome_json",
        [
          target.command_id,
          target.child.executor_incarnation,
          target.operation,
          'core',
          target.child.child_id,
          target.turn!.turn_number,
          JSON.stringify(envelope),
          runs.machine_id,
          JSON.stringify({
            command_id: target.command_id,
            child_outcome: outcome,
            model_list: null,
          }),
        ],
      ),
    );
    put_released.resolve();
    await mailbox.publish_settlements([
      [envelope, { command_id: target.command_id, child_outcome: outcome, model_list: null }],
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
    restore: () => {
      retained_pending = command;
      return setup({
        restart_of: f.database,
        core: {
          backend_registry: registry_of_factories(for_every_built_in_backend(() => factory)),
        },
      });
    },
    reference,
    proxy,
    command,
    state,
    sending,
    finish,
    close,
    mailbox,
    prompts,
    offline: () => {
      present = false;
    },
    on_steer: (answer: typeof steer) => {
      steer = answer;
    },
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

// The fixture has no artifact transport, so artifact delivery stays blocked throughout.
// The Mac's prompt failed before its native send while the home still showed that turn
// running. A recap promoted into that turn must wait for the next turn, not disappear.
// The uncertain case is the control: a send that may have arrived is never repeated.
test.each(['refused', 'uncertain'] as const)(
  'a promoted recap whose remote steer is %s after a remote pre-send failure',
  async (steer_fate) => {
    const f = await remote_fixture();
    const publish = (command: WireValue<'ChildCommand'>, outcome: WireValue<'CommandOutcome'>) =>
      f.mailbox.publish_settlements([
        [
          {
            command_id: command.command_id,
            command_sequence: 100,
            created_at_vps_ms: 1,
            child_command: command,
          },
          { command_id: command.command_id, child_outcome: outcome, model_list: null },
        ],
      ]);
    const recap_rows = () =>
      f.store
        .read_events_after('core', 0)
        .filter(
          (row) => 'sender_message_id' in row.payload && row.payload.sender_message_id === 'recap',
        )
        .map((row) => row.kind);
    try {
      await f.finish({
        command_id: f.command.command_id,
        child: f.reference,
        disposition: 'succeeded',
        result: {
          kind: 'prompt_accepted',
          composed_content_delivered: true,
          agent_acceptance_proven: true,
        },
      });
      expect((await f.send('recap', 'recap')).constructor.name).toBe('PromptDeliveryQueued');
      const held = (await f.core.held_prompts('core'))[0];
      let steer_command: WireValue<'ChildCommand'> | null = null;
      let steer_outcome: WireValue<'CommandOutcome'> | null = null;
      f.on_steer(async (command) => {
        steer_command = command;
        steer_outcome = {
          command_id: command.command_id,
          child: f.reference,
          ...(steer_fate === 'refused'
            ? {
                disposition: 'succeeded',
                result: { kind: 'steer_refused', refusal_reason: 'no_running_turn_to_steer_into' },
              }
            : {
                disposition: 'uncertain',
                result: { error_type: 'PromptWriteFailed', error_message: 'reply lost' },
              }),
        };
        await publish(command, steer_outcome);
        return steer_outcome;
      });
      const promoted = await f.core.promote_held_prompt('core', held.held_prompt_id, 'steer');
      expect(promoted?.constructor.name).toBe(
        steer_fate === 'refused' ? 'PromptDeliveryQueued' : 'PromptDeliveryUncertain',
      );
      // The definite native failure of the earlier prompt reaches the core and ends its turn.
      await publish(f.command, {
        command_id: f.command.command_id,
        child: f.reference,
        disposition: 'refused',
        result: { error_type: 'PromptWriteRefused', error_message: 'input failed before send' },
      });
      if (steer_fate === 'refused') {
        await vi.waitFor(() => expect(f.prompts).toHaveLength(2));
        expect(f.prompts[1].arguments).toMatchObject({ content: [{ text: 'recap' }] });
        await f.finish(
          {
            command_id: f.prompts[1].command_id,
            child: f.reference,
            disposition: 'succeeded',
            result: {
              kind: 'prompt_accepted',
              composed_content_delivered: true,
              agent_acceptance_proven: true,
            },
          },
          f.prompts[1],
        );
        await vi.waitFor(() => expect(recap_rows()).toEqual(['prompt']));
        expect(await f.core.held_prompts('core')).toEqual([]);
        // A late copy of the same native refusal changes nothing.
        await publish(steer_command!, steer_outcome!);
        await f.core.wait_until_quiescent();
        expect(recap_rows()).toEqual(['prompt']);
      } else {
        await vi.waitFor(() => expect(f.state.running_turn).toBe(null));
        await f.core.wait_until_quiescent();
        expect(recap_rows()).toEqual(['prompt_delivery_uncertain']);
        expect(await f.core.held_prompts('core')).toEqual([]);
      }
      expect(f.prompts).toHaveLength(steer_fate === 'refused' ? 2 : 1);
    } finally {
      await f.close();
    }
  },
);

// Risk: unit fakes reuse the same retained-run object across calls, hiding identity bugs in
// confirm_killed_source when restarting a running Mac Ticket through ExecutorStore.retained_runs().
test('restart-worker completes for a running Mac Ticket through the real admitted_runs path', async () => {
  const database = fixture();
  database.write((tx) =>
    tx.run('INSERT INTO conversation_runtime_epoch VALUES (1,?)', ['test-runtime']),
  );
  const mailbox = new SqliteCommandMailbox(database);
  const presence = new MacPresenceService(database, mailbox, { changes: new ChangeSignal() });
  const runs = new MacRuns(database, presence, mailbox);
  const factory = runs.as_factory();
  const f = setup({
    restart_of: database,
    core: {
      backend_registry: registry_of_factories(for_every_built_in_backend(() => factory)),
    },
  });
  configure_conversation_start(f.database, {
    db_path: f.database_path,
    workspace_root: dirname(f.database_path),
  });
  runs.conversations = f.core;
  f.core.configure_worker_cleanup_recheck((source, ticket_id) =>
    runs.recheck_cleanup(source, ticket_id),
  );
  const identity: WireValue<'ExecutorIdentity'> = {
    protocol_version: PROTOCOL_VERSION,
    spool_format_version: SPOOL_FORMAT_VERSION,
    application_revision: 'synthetic-mac',
    executor_incarnation: answer.child.executor_incarnation,
    connection_generation: 1,
  };
  let polling = true;
  let after = 0;
  const run_poll_loop = async () => {
    while (polling) {
      const reply = await mailbox.poll(identity, after, 0.05);
      if (!reply.commands.length) continue;
      const outcomes: WireValue<'CommandOutcomeReport'>['outcomes'] = [];
      const stops: WireValue<'ChildCommand'>[] = [];
      for (const envelope of reply.commands) {
        after = Number(envelope.command_sequence);
        const command = envelope.child_command;
        if (command == null) continue;
        if (command.operation === 'stop') {
          stops.push(command);
          continue;
        }
        const result =
          command.operation === 'write_prompt'
            ? {
                kind: 'prompt_accepted' as const,
                composed_content_delivered: true,
                agent_acceptance_proven: true,
              }
            : command.operation === 'recheck_cleanup'
              ? { complete: true, reason: 'No active child' }
              : {};
        outcomes.push({
          command_id: command.command_id,
          child_outcome: {
            command_id: command.command_id,
            child: command.child,
            disposition: 'succeeded',
            result,
          } as WireValue<'CommandOutcome'>,
          model_list: null,
        });
      }
      if (outcomes.length) await mailbox.record_results(identity, { outcomes });
      // The Mac proves a stop with a child_stopped item, not a command result.
      for (const stop of stops) {
        const outcome = {
          command_id: stop.command_id,
          child: stop.child,
          disposition: 'succeeded',
          result: {},
        } as WireValue<'CommandOutcome'>;
        await runs.validate_stop(outcome);
        await runs.accept_stop_receipt(outcome, {
          disposition: 'accepted',
          result: { recorded: true, stop_command_id: stop.command_id, stopped_child: stop.child },
        } as unknown as WireValue<'AcceptanceReceipt'>);
      }
    }
  };
  let poll_loop: Promise<void> = Promise.resolve();
  try {
    await presence.accept_lease({
      identity,
      process_id: 4242,
      mac_wall_clock_ms: Date.now(),
      queue_depth: 0,
      oldest_item_age_ms: null,
      waiting_conversations: [],
      waiting_conversations_truncated: false,
      working_turns: [],
      admission_stopped: false,
      cleanup_uncertainties: [],
      driver_kinds: ['codex', 'claude'],
    });
    poll_loop = run_poll_loop();
    const ticket = create(f.database, { now: 100 });
    f.database.write((tx) => {
      tx.run("UPDATE tickets SET locale='macos',machine_id=? WHERE id=?", [
        ORIGINAL_MAC_MACHINE_ID,
        ticket.id,
      ]);
      tx.run("UPDATE ticket_decision_messages SET delivery_status='delivered' WHERE ticket_id=?", [
        ticket.id,
      ]);
    });
    const step_options = {
      connect_database: () => f.database,
      conversation_system: f.core,
      worker_type_registry: configured_worker_type_registry(),
      now: () => 100,
    };
    const initial = await explicit_start(() => start_ready_worker_step(ticket.id, step_options));
    expect(initial.started).toBe(true);
    const first_conversation_id = read(f.database, ticket.id).conversation_id!;
    expect(first_conversation_id).toBeTruthy();
    expect(await f.core.is_running(first_conversation_id)).toBe(true);
    const restarted = await restart_worker(f.core, f.database, OWNER, ticket.id, {
      now: () => 101,
      start_worker_step: () =>
        explicit_start(() => start_ready_worker_step(ticket.id, step_options)),
    });
    expect(restarted).toMatchObject({
      ticket_id: ticket.id,
      killed_conversation_id: first_conversation_id,
      killed_conversation_looked_running: true,
      started: true,
      not_started_because: null,
    });
    const next_conversation_id = read(f.database, ticket.id).conversation_id;
    expect(next_conversation_id).toBeTruthy();
    expect(next_conversation_id).not.toBe(first_conversation_id);
    expect(restarted.conversation_id).toBe(next_conversation_id);
    expect(await f.core.is_running(first_conversation_id)).toBe(false);
    expect(await f.core.is_running(next_conversation_id!)).toBe(true);
  } finally {
    polling = false;
    await poll_loop.catch(() => {});
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

// Gap: adjacent peers ignore unknown fields, so answer attachments must refuse before a pre-28 dispatch.
test('attachment answers refuse old or unknown executor identity before any native answer', async () => {
  const { database } = observe(fixture());
  const mailbox = new SqliteCommandMailbox(database);
  const presence = new MacPresenceService(database, mailbox, { changes: new ChangeSignal() });
  const runs = new MacRuns(database, presence, mailbox);
  const identity = vi.spyOn(presence, 'current_identity');
  const command: WireValue<'ChildCommand'> = {
    ...answer,
    operation: 'answer_user_input',
    arguments: {
      kind: 'answer_user_input',
      request_id: 'q',
      answers: [
        {
          question_id: 'proof',
          answers: [],
          attachments: [
            {
              stored_file_id: 'f_proof',
              file_name: 'proof.txt',
              media_type: 'text/plain',
              byte_count: 3,
            },
          ],
        },
      ],
    },
  };
  try {
    identity.mockReturnValue({
      protocol_version: 27,
      spool_format_version: 5,
      application_revision: 'old',
      executor_incarnation: answer.child.executor_incarnation,
      connection_generation: 0,
    });
    await expect(runs.save_answer(command)).rejects.toThrow('Update the executor');
    identity.mockReturnValue(null);
    await expect(runs.save_answer(command)).rejects.toThrow('Update the executor');
    identity.mockReturnValue({
      protocol_version: 28,
      spool_format_version: 5,
      application_revision: 'current',
      executor_incarnation: answer.child.executor_incarnation,
      connection_generation: 0,
    });
    // A current peer passes capability admission and reaches the ordinary core attachment guard.
    await expect(runs.save_answer(command)).rejects.toThrow('require attached conversations');
  } finally {
    const options = { deadline: performance.now() / 1000 + 2 };
    await Promise.all([runs.stop(options), presence.stop(options), mailbox.stop(options)]);
    await Promise.all([runs.settled(), presence.settled(), mailbox.settled()]);
  }
});

// Gap: native wait tests do not reconstruct a pending submission after Home recovery.
// Reuse their core/mailbox fixture. No second native input or additional process host.
test('a late result after recovery settles its original submission without another native prompt', async () => {
  const { resume_recipient_deliveries } = await import('../message_delivery/recovery.ts');
  const f = await remote_fixture();
  const envelope = {
    command_id: f.command.command_id,
    command_sequence: 1,
    created_at_vps_ms: 1,
    child_command: f.command,
  };
  f.database.write((tx) => {
    tx.run(
      "INSERT INTO executor_commands(command_id,executor_incarnation,operation,conversation_id,child_id,turn_number,command_json,state,created_at_ms,machine_id) VALUES(?,?,?,?,?,?,?,'waiting',1,?)",
      [
        f.command.command_id,
        f.reference.executor_incarnation,
        'write_prompt',
        'core',
        f.reference.child_id,
        f.command.turn!.turn_number,
        JSON.stringify(envelope),
        ORIGINAL_MAC_MACHINE_ID,
      ],
    );
    tx.run(
      'INSERT INTO recipient_delivery_commands SELECT ?,submission_id,attempt_id FROM recipient_deliveries WHERE submission_id=?',
      [f.command.command_id, 'core:sender'],
    );
  });
  void f.sending.catch(() => {});
  f.offline();
  const restarted = f.restore();
  try {
    resume_recipient_deliveries(restarted.core);
    expect(restarted.store.sender_message_outcome('core', 'sender')).toBeNull();
    // Retire only the old observer. The exact native command remains owned by recovery.
    await f.proxy.detach();
    await f.sending.catch(() => {});
    const { Deferred } = await import('../core/deferred.ts');
    const { text_message_content } = await import('../conversation/message_content.ts');
    const admitted = new Deferred<void>();
    const correction = restarted.core.send('core', text_message_content('offline correction'), {
      sender_label: 'Owner',
      sender_message_id: 'offline-correction',
      mode: 'steer',
      recording_receipt: {
        sender_id: '',
        submission_id: 'offline-correction',
        admitted: () => admitted.resolve(),
      },
    });
    await admitted.promise;
    await f
      .finish({
        command_id: f.command.command_id,
        child: f.reference,
        disposition: 'succeeded',
        result: {
          kind: 'prompt_accepted',
          composed_content_delivered: true,
          agent_acceptance_proven: true,
        },
      })
      .catch(() => {});
    await vi.waitFor(() =>
      expect(restarted.store.sender_message_outcome('core', 'sender')?.payload.kind).toBe('prompt'),
    );
    expect((await correction).constructor.name).toBe('PromptDeliveryQueued');
    expect((await restarted.core.held_prompts('core')).map((row) => row.sender_message_id)).toEqual(
      ['offline-correction'],
    );
    expect(f.prompts).toHaveLength(1);
    expect(
      f.database.read((sql) =>
        sql.get<{ state: string }>('SELECT state FROM recipient_deliveries WHERE submission_id=?', [
          'core:sender',
        ]),
      )?.state,
    ).toBe('delivered');
  } finally {
    await restarted.core.shutdown({ deadline: performance.now() / 1000 + 2 });
    await f.close();
  }
});
