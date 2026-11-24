/** Risks: spontaneous work must enter the same controls and accounting as prompted work. */
import { expect, test } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import {
  AgentTurnStarted,
  BackgroundTaskStarted,
  BackgroundTaskEnded,
  TurnEnded,
  TokenUsageReported,
} from '../conversation/backends/contracts.ts';
import {
  PromptDeliveryInjected,
  PromptDeliveryQueued,
  PromptDeliveryStarted,
} from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { scripted_instance_controls } from '../drivers/testing/registry.ts';

// Existing control tests exercise prompted turns only. Reuse their process and real record host.
test('an agent-started turn owns usage, steer, queue, and send now', async () => {
  const f = setup();
  try {
    await f.start();
    await f.send();
    const state = f.core.conversations.get('core')!;
    const original = state.running_turn!.token;
    await f.core.take_local_backend_event(state, original, new TurnEnded({ ending: 'completed' }));
    const token = await state.child_event_sink!.start_agent_turn!('Sub-agent finished');
    expect(token).toEqual({ conversation_id: 'core', turn_number: 2 });
    expect(state.phase).toBe('running');
    await f.core.take_local_backend_event(
      state,
      token,
      new TokenUsageReported({ input_tokens: 17, output_tokens: 9, cost_usd: 0.01 }),
    );
    expect(await state.child_event_sink!.start_agent_turn!('duplicate')).toBeNull();
    expect(
      await f.core.send('core', text_message_content('steer'), {
        sender_label: 'Owner',
        sender_message_id: 'steer',
        mode: 'steer',
      }),
    ).toBeInstanceOf(PromptDeliveryInjected);
    expect(
      await f.core.send('core', text_message_content('queued'), {
        sender_label: 'Owner',
        sender_message_id: 'queued',
        mode: 'queue',
      }),
    ).toBeInstanceOf(PromptDeliveryQueued);
    expect(state.running_turn!.token).toEqual(token);
    expect(
      await f.core.send('core', text_message_content('now'), {
        sender_label: 'Owner',
        sender_message_id: 'now',
        mode: 'send_now',
      }),
    ).toBeInstanceOf(PromptDeliveryStarted);
    const events = f.store.read_events_after('core', 0);
    expect(
      events.filter((row) => row.kind === 'token_usage').map((row) => row.payload),
    ).toMatchObject([{ input_tokens: 17, output_tokens: 9, cost_usd: 0.01 }]);
    expect(
      events.filter((row) => row.kind === 'agent_turn_started').map((row) => row.payload),
    ).toMatchObject([{ reason: 'Sub-agent finished', turn_number: 2 }]);
    expect(
      events.filter((row) => row.kind === 'turn_ended').map((row) => row.payload),
    ).toMatchObject([{ ending: 'completed' }, { ending: 'interrupted' }]);
    expect((await f.core.held_prompts('core')).map((row) => row.sender_message_id)).toEqual([
      'queued',
    ]);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// Task history and wake notices did not previously exist. The existing task table remains canonical.
test('background history survives task removal and completion wakes only a driver that needs it', async () => {
  const f = setup();
  try {
    await f.start();
    await f.send();
    const state = f.core.conversations.get('core')!;
    await f.core.take_local_backend_event(
      state,
      state.running_turn!.token,
      new TurnEnded({ ending: 'completed' }),
    );
    const capabilities = scripted_instance_controls(
      f.core.options.backend_registry,
      state.record.backend_key,
    ).capabilities.turns;
    capabilities.wakesOnBackgroundCompletion = true;
    await f.core.take_local_backend_event(
      state,
      null,
      new BackgroundTaskStarted({ task_id: 'first', task_description: 'First task' }),
    );
    await f.core.take_local_backend_event(
      state,
      null,
      new BackgroundTaskEnded({ task_id: 'first', task_status: 'completed' }),
    );
    expect(state.running_turn).toBeNull();
    capabilities.wakesOnBackgroundCompletion = false;
    await f.core.take_local_backend_event(
      state,
      null,
      new BackgroundTaskStarted({ task_id: 'second', task_description: 'Second task' }),
    );
    await f.core.take_local_backend_event(
      state,
      null,
      new BackgroundTaskEnded({ task_id: 'second', task_status: 'completed' }),
    );
    await expect.poll(() => state.running_turn).not.toBeNull();
    const events = f.store.read_events_after('core', 0);
    expect(
      events.filter((row) => row.kind === 'background_task_ended').map((row) => row.payload),
    ).toMatchObject([{ task_description: 'First task' }, { task_description: 'Second task' }]);
    expect(
      events.filter((row) => row.kind === 'prompt').map((row) => JSON.stringify(row.payload)),
    ).toHaveLength(2);
    expect(JSON.stringify(events.at(-1)!.payload)).toContain('Second task');
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// The native inbox wraps intake results in a durable receipt. That wrapper must not suppress runtime installation.
test('a native start receipt installs its turn and a repeated receipt cannot allocate another', async () => {
  const f = setup();
  try {
    await f.start();
    await f.send();
    const state = f.core.conversations.get('core')!;
    await f.core.take_local_backend_event(
      state,
      state.running_turn!.token,
      new TurnEnded({ ending: 'completed' }),
    );
    const result = await f.core.take_backend_event(
      null,
      new AgentTurnStarted({ reason: 'Task done' }),
      {
        conversation_id: 'core',
        in_commit: (_tx, take) => {
          const accepted = take();
          return {
            taken: true,
            result: { disposition: 'accepted', result: accepted },
            written: [],
          };
        },
      },
    );
    expect(result.taken).toBe(true);
    expect(result.result.result).toMatchObject({ applied: true, turn: { turn_number: 2 } });
    expect(state.running_turn!.token.turn_number).toBe(2);
    expect(state.phase).toBe('running');
    await f.core.take_backend_event(null, new AgentTurnStarted({ reason: 'Task done' }), {
      conversation_id: 'core',
      in_commit: () => ({ taken: false, result: result.result, written: [] }),
    });
    expect(
      f.store.read_events_after('core', 0).filter((row) => row.kind === 'agent_turn_started'),
    ).toHaveLength(1);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// Prior coverage keeps only prompt-issued run tokens. Reuse the existing record host and real native inbox.
test('an accepted native agent turn survives startup reconciliation with its receipt', async () => {
  const { MacInbox } = await import('../executor/vps_inbox.ts');
  const { InboxStore } = await import('../executor/data/inbox.ts');
  const { ExecutorStore } = await import('../executor/data/runs.ts');
  const { FileStaging } = await import('../executor/vps_files.ts');
  const { buildClock } = await import('../core/clock.ts');
  const { loadConfig } = await import('../core/config.ts');
  const { OWNER } = await import('../core/contracts.ts');
  const { ORIGINAL_MAC_MACHINE_ID } = await import('../execution_identity.ts');
  const { PROTOCOL_VERSION } = await import('../execution_configuration.ts');
  const { retained_open_runs } = await import('../conversation/runtime/remote.ts');
  const { adopted_running_turn } = await import('../conversation/logic/retained_turn.ts');
  const f = setup();
  const identity = {
    executor_incarnation: 'native',
    application_revision: 'test',
    protocol_version: PROTOCOL_VERSION,
    spool_format_version: 5,
    connection_generation: 0,
  };
  const reference = { executor_incarnation: 'native', child_id: 'child', conversation_id: 'core' };
  const store = new ExecutorStore(f.database);
  const inbox = new MacInbox(
    new InboxStore(f.database, new FileStaging(f.database_path)),
    buildClock(loadConfig(undefined, { PLAN_TEST_MODE: '1' })),
    { authority: () => OWNER, validate_stop() {}, async accept_stop_receipt() {} },
  );
  inbox.conversations = f.core;
  try {
    await f.start();
    await f.send();
    const state = f.core.conversations.get('core')!;
    await f.core.take_local_backend_event(
      state,
      state.running_turn!.token,
      new TurnEnded({ ending: 'completed' }),
    );
    f.database.write((tx) => {
      tx.run(
        'INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,machine_id) VALUES(?,0,?)',
        [identity.executor_incarnation, ORIGINAL_MAC_MACHINE_ID],
      );
      tx.run('INSERT INTO mac_executor_presence(machine_id,executor_incarnation) VALUES(?,?)', [
        ORIGINAL_MAC_MACHINE_ID,
        identity.executor_incarnation,
      ]);
    });
    store.register_child(reference, 'conversation', 'core', 0);
    expect(store.retained_runs()[0]!.turn.turn_number).toBe(1);
    const envelope = {
      conversation_id: 'core',
      sender_id: 'native-sender',
      sequence: 1,
      submission_id: 'autonomous-start',
      kind: 'backend_event' as const,
      payload: {
        child: reference,
        turn: null,
        method: 'agent_turn_started',
        arguments: { reason: 'Task done' },
      },
      file_dependencies: [],
    };
    const receipt = await inbox.accept(envelope, identity);
    expect(receipt.result).toMatchObject({
      applied: true,
      turn: { conversation_id: 'core', turn_number: 2 },
    });
    expect(await inbox.accept(envelope, identity)).toEqual(receipt);
    const factory = Object.assign(
      () => {
        throw new Error('reconciliation starts no child');
      },
      { recover: () => null, retained_runs: () => store.retained_runs() },
    );
    const open = retained_open_runs([factory]);
    expect(open.get('core')!.turn_number).toBe(2);
    await f.store.prepare_reconciliation(open);
    f.database.write((tx) => f.store.reconcile_in_transaction(tx, open));
    const facts = f.store.lifecycle_facts('core');
    expect(facts.find((fact) => fact.kind === 'turn' && fact.turn_number === 2)!.status).toBe(
      'running',
    );
    expect(
      adopted_running_turn({ conversation_id: 'core', turn_number: 2 }, facts, new Map(), {
        executor_process_current: true,
      })!.token.turn_number,
    ).toBe(2);
  } finally {
    await inbox.stop({ deadline: performance.now() / 1000 + 10 });
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// A start needs the prior end, but it must not await unrelated work queued after it.
test('local activation takes a FIFO position after its prior ending', async () => {
  const { ComposerCatalogReported } = await import('../conversation/backends/contracts.ts');
  const { Deferred } = await import('../core/deferred.ts');
  const f = setup();
  const later = new Deferred<void>();
  try {
    await f.start();
    await f.send();
    const state = f.core.conversations.get('core')!;
    const sink = state.child_event_sink!;
    const original = state.running_turn!.token;
    state.backend_events.push(async () => {
      await f.core.take_local_backend_event(
        state,
        original,
        new TurnEnded({ ending: 'completed' }),
      );
    });
    await sink.report(null, new ComposerCatalogReported({ composer_catalog: [] }));
    const activation = sink.start_agent_turn!('Background finished');
    state.backend_events.push(() => later.promise);
    const { observe_promise } = await import('../core/promise_observer.ts');
    const observed = await observe_promise(activation, 1000);
    expect(observed).toEqual({
      completed: true,
      value: { conversation_id: 'core', turn_number: 2 },
    });
    expect(later.settled).toBe(false);
  } finally {
    later.resolve();
    await f.core.wait_until_quiescent();
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// The native sink observes reader cancellation. Local activation must not open a turn after that reader stops.
test('aborted local activation returns before a held core lock and creates no late turn', async () => {
  const f = setup();
  let release: (() => void) | undefined;
  try {
    await f.start();
    await f.send();
    const state = f.core.conversations.get('core')!;
    await f.core.take_local_backend_event(
      state,
      state.running_turn!.token,
      new TurnEnded({ ending: 'completed' }),
    );
    release = await state.lock.acquire();
    const cancelled = new AbortController();
    const activation = state.child_event_sink!.start_agent_turn!(
      'Background finished',
      cancelled.signal,
    );
    await new Promise<void>((resolve) => setImmediate(resolve));
    cancelled.abort();
    expect(await activation).toBeNull();
    state.closing = true;
    release();
    release = undefined;
    await state.backend_event_pump;
    expect(state.backend_event_failures).toBe(0);
    state.closing = false;
    expect(state.running_turn).toBeNull();
    expect(
      f.store.read_events_after('core', 0).some((event) => event.kind === 'agent_turn_started'),
    ).toBe(false);
  } finally {
    release?.();
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});
