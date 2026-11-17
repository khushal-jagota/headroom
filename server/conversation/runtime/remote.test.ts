import { ORIGINAL_MAC_MACHINE_ID } from '../../execution_identity.ts';
import { expect, test, vi } from 'vitest';
import { setup } from '../../tests/conversation_core_fixture.ts';
import { SignalPublicationCoordinator } from '../publication/coordinator.ts';
import * as persistence from '../publication/persistence.ts';
import { InboxStore } from '../../executor/data/inbox.ts';
import { FileStaging } from '../../executor/vps_files.ts';
import { PlannerError } from '../../core/contracts.ts';
import { as_object } from '../../executor/protocol.ts';
import {
  BackendPermissionAsk,
  BackendPromptAccepted,
  BackendUserInputRequest,
  PermissionAskRaised,
  RetainedBackendRun,
  TurnEnded,
  UserInputRequested,
} from '../backends/contracts.ts';
import type { WireValue } from '../../executor/contracts.ts';
import { text_message_content } from '../message_content.ts';
import { Deferred } from '../../core/deferred.ts';
import { retained_open_runs } from './remote.ts';
import { PromptDeliveryInjected, PromptDeliveryStarted } from '../contracts.ts';

// An acknowledgement must wait for receipt and steer settlement, then recheck its exact source.
test('remote prompt acceptance releases only the matching live source after its barriers', async () => {
  let now = 100;
  let owned = true;
  const f = setup({
    behavior: { barrier: 'receipt' },
    core: {
      monotonic_now: () => now,
      require_runtime_ownership: () => {
        if (!owned) throw new Error('Runtime ownership revoked');
      },
    },
  });
  const reference = {
    conversation_id: 'core',
    executor_incarnation: 'remote',
    child_id: 'original',
  };
  let acknowledgement: Promise<void> | undefined;
  let child: Awaited<typeof f.child> | undefined;
  const steering = new Deferred<void>();
  const detach_entered = new Deferred<void>();
  const detach_released = new Deferred<void>();
  let closing: Promise<void> | undefined;
  try {
    await f.start();
    expect(
      f.core.start_admission.try_start(await f.core.automatic_start_request('sender', 'core')),
    ).toBe(true);
    const sent = f.send();
    child = await f.child;
    await child.wait_for_barrier('receipt');
    const state = f.core.conversations.get('core')!;
    const token = state.reserved_turn!.token;
    state.retained_child_reference = reference;
    await f.core.confirm_remote_prompt_accepted({ ...reference, child_id: 'stale' }, token);
    await f.core.confirm_remote_prompt_accepted(reference, {
      ...token,
      turn_number: token.turn_number + 1,
    });
    const bindings = () =>
      f.database.read((sql) => sql.all('SELECT identity FROM automatic_start_bindings'));
    expect(bindings()).toEqual([{ identity: 'sender' }]);
    let accepted = false;
    acknowledgement = f.core.confirm_remote_prompt_accepted(reference, token).then(() => {
      accepted = true;
    });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(accepted).toBe(false);
    const replacement = { ...reference, child_id: 'replacement' };
    state.retained_child_reference = replacement;
    await child.release('receipt');
    await sent;
    await acknowledgement;
    expect(bindings()).toEqual([{ identity: 'sender' }]);
    state.steer_settlements.add(steering);
    accepted = false;
    acknowledgement = f.core.confirm_remote_prompt_accepted(replacement, token).then(() => {
      accepted = true;
    });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(accepted).toBe(false);
    expect(bindings()).toEqual([{ identity: 'sender' }]);
    const final_source = { ...replacement, child_id: 'final-source' };
    state.retained_child_reference = final_source;
    now = 101;
    state.steer_settlements.delete(steering);
    steering.resolve();
    await acknowledgement;
    expect(bindings()).toEqual([{ identity: 'sender' }]);
    const detach = child.detach.bind(child);
    vi.spyOn(child, 'detach').mockImplementation(async () => {
      detach_entered.resolve();
      await detach_released.promise;
      await detach();
    });
    closing = f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
    await detach_entered.promise;
    expect(f.core.shutting_down).toBe(true);
    expect(state.closing).toBe(true);
    expect(() => f.core.conversation_state('core')).toThrow('runtime is shut down');
    await expect(f.core.acquire_after_steers(state)).rejects.toMatchObject({ name: 'AbortError' });
    await f.core.confirm_remote_prompt_accepted(replacement, token);
    await f.core.confirm_remote_prompt_accepted(final_source, {
      ...token,
      turn_number: token.turn_number + 1,
    });
    expect(bindings()).toEqual([{ identity: 'sender' }]);
    await f.core.confirm_remote_prompt_accepted(final_source, token);
    expect(bindings()).toEqual([]);
    expect(f.core.start_admission.snapshots()).toEqual([]);
    expect(state.last_touched_monotonic).toBe(101);
    detach_released.resolve();
    await closing;
    owned = false;
    await expect(f.core.confirm_remote_prompt_accepted(final_source, token)).rejects.toThrow(
      'Runtime ownership revoked',
    );
  } finally {
    steering.resolve();
    const state = f.core.conversations.get('core');
    state?.steer_settlements.delete(steering);
    await child?.release('receipt').catch(() => {});
    await acknowledgement;
    detach_released.resolve();
    await closing;
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});

test('retained-run admission drops explicit rejections and propagates programming errors', () => {
  const run = new RetainedBackendRun({
    reference: { conversation_id: 'remote', executor_incarnation: 'synthetic', child_id: 'child' },
    turn: { conversation_id: 'remote', turn_number: Number.MAX_SAFE_INTEGER + 1 },
    delivery_uncertain: false,
    retirement_confirmed: false,
  });
  const factory = Object.assign(
    () => {
      throw new Error('No child starts in admission');
    },
    {
      recover: () => null,
      retained_runs: () => [run],
    },
  );
  const diagnostic = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    expect(retained_open_runs([factory]).size).toBe(0);
    // A malformed internal turn triggers a real property-read TypeError in admission.
    Reflect.set(run, 'turn', undefined);
    expect(() => retained_open_runs([factory])).toThrow(TypeError);
  } finally {
    diagnostic.mockRestore();
  }
});

// The SQL-only inbox proof cannot detect predictions or a global Signal batch
// that escapes before a permanent refusal receipt commits through the real core.
test('a refused remote take restores runtime before the global finalizer and publishes no event', async () => {
  let producer: SignalPublicationCoordinator | undefined;
  let batches = 0,
    published = 0;
  const f = setup({
    database: { commitSteps: [(tx) => producer?.finalize_signal(tx)] },
    core: {
      save_attempt: (id, work, mutations) => producer!.save_attempt(id, work, mutations),
      publish_event: () => {
        published++;
      },
    },
  });
  producer = new SignalPublicationCoordinator({
    database: f.database,
    require_runtime_ownership: () => {},
  });
  producer.bind_runtime((id) => f.core.runtime_turn(id));
  producer.initialize();
  const attachment = producer.attach_signal_publication({
    install: () => {},
    participate: () => {
      batches++;
    },
    unavailable: () => {},
  });
  const identity = {
    executor_incarnation: 'remote',
    protocol_version: 22,
    spool_format_version: 5,
    application_revision: 'fixture',
    connection_generation: 0,
  };
  f.database.write((tx) =>
    tx.run('INSERT INTO mac_executor_presence(machine_id,executor_incarnation) VALUES(?,?)', [
      ORIGINAL_MAC_MACHINE_ID,
      identity.executor_incarnation,
    ]),
  );
  try {
    await f.start();
    expect(
      f.core.start_admission.try_start(await f.core.automatic_start_request('sender', 'core')),
    ).toBe(true);
    await f.send();
    const running = f.core.runtime_turn('core')!;
    const snapshot = () => ({
      ...f.database.read((sql) => ({
        cursor: persistence.cursor(sql),
        events: sql.all('SELECT * FROM conversation_events ORDER BY sequence'),
      })),
      facts: f.store.lifecycle_facts('core'),
    });
    const before = snapshot();
    const before_publications = { batches, published };
    const envelope: WireValue<'QueuedEnvelope'> = {
      conversation_id: 'core',
      sender_id: 'sender',
      submission_id: 'refused-end',
      sequence: 1,
      kind: 'backend_event',
      payload: {},
      file_dependencies: [],
    };
    const inbox = new InboxStore(f.database, new FileStaging(f.database_path));
    const result = await f.core.take_backend_event(
      running.token,
      new TurnEnded({ ending: 'completed' }),
      {
        in_commit: (tx, take) => {
          const [receipt, taken] = inbox.accept_in_transaction(
            tx,
            envelope,
            () => {
              take();
              throw new PlannerError('validation', 'permanent refusal');
            },
            100,
            { identity },
          );
          return { result: as_object(receipt, 'AcceptanceReceipt'), taken, written: [] };
        },
      },
    );
    expect(result.taken).toBe(false);
    expect(result.result.disposition).toBe('refused');
    // Positive current-turn activity proves agent acceptance before record admission.
    expect(f.core.start_admission.snapshots()).toEqual([]);
    expect(f.database.read((sql) => sql.all('SELECT * FROM automatic_start_bindings'))).toEqual([]);
    expect(f.core.runtime_turn('core')).toBe(running);
    expect(snapshot()).toEqual(before);
    expect(f.database.read((sql) => sql.all('SELECT * FROM signal_publication_dirty'))).toEqual([]);
    expect({ batches, published }).toEqual(before_publications);
    expect(
      f.database.read((sql) =>
        sql.get<{ disposition: string }>('SELECT disposition FROM mac_inbox_positions'),
      )?.disposition,
    ).toBe('refused');
  } finally {
    attachment.close();
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});

// A cancelled inbox wait must leave no queued lock owner that blocks the retry.
test('a remote event that times out behind earlier work leaves the retry its lock', async () => {
  const f = setup();
  try {
    await f.start();
    await f.send();
    const state = f.core.conversations.get('core')!,
      token = state.running_turn!.token;
    const release = await state.lock.acquire();
    try {
      await expect(
        f.core.take_backend_event(token, new TurnEnded({ ending: 'completed' }), {
          retry_later_at: performance.now() / 1000 - 1,
        }),
      ).rejects.toMatchObject({ code: 'retry_later' });
      expect(f.core.runtime_turn('core')?.token).toEqual(token);
    } finally {
      release();
    }
    const accepted = await f.core.take_backend_event(token, new TurnEnded({ ending: 'completed' }));
    expect(accepted.taken).toBe(true);
    expect(f.core.runtime_turn('core')).toBeNull();
    expect(
      f.store.read_events_after('core', 0).filter((row) => row.kind === 'turn_ended'),
    ).toHaveLength(1);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});

// Reuse the existing real core/record/process fixture; hold only its native replay reply.
// Earlier receipt tests do not kill an already-recorded turn during a replay write.
test('kill during replay ends its turn and asks before a late successful reply', async () => {
  let now = 0;
  const close_rewritten = vi.fn();
  const f = setup({
    core: {
      monotonic_now: () => now,
      remote_recovery: {
        retire_replaced() {},
        close_rewritten,
        async recover() {},
      },
    },
  });
  const entered = new Deferred<void>(),
    release = new Deferred<void>();
  let replay: Promise<void> | undefined;
  try {
    await f.start();
    await f.send();
    const child = await f.child,
      state = f.core.conversation_state('core')!;
    const reference = {
      conversation_id: 'core',
      executor_incarnation: 'remote',
      child_id: 'replaying',
    };
    await child.finish();
    await f.core.wait_until_quiescent();
    const before_reused_turn = f.store.read_conversation('core')!.latest_sequence;
    state.retained_child_reference = reference;
    Object.defineProperty(child, 'stop_outside_lock', { value: true });
    expect(
      f.core.start_admission.try_start(
        await f.core.automatic_start_request('reused-remote', 'core'),
      ),
    ).toBe(true);
    await f.send('second write on retained child', 'reused-remote');
    const token = state.running_turn!.token;
    expect(
      f.database.read((sql) =>
        sql.get(
          'SELECT executor_incarnation,child_id FROM automatic_start_bindings WHERE identity=?',
          ['reused-remote'],
        ),
      ),
    ).toEqual({
      executor_incarnation: reference.executor_incarnation,
      child_id: reference.child_id,
    });
    vi.spyOn(child, 'withdraw_delivery').mockResolvedValue(false);
    let retirement_confirmed = false;
    Object.assign(f.core.options.backend_registry.require_live('codex').child_factory, {
      recover: () => child,
      retained_runs: () => [
        new RetainedBackendRun({
          reference,
          turn: token,
          delivery_uncertain: false,
          retirement_confirmed,
        }),
      ],
    });
    vi.spyOn(child, 'stop').mockImplementation(async () => {
      retirement_confirmed = true;
    });
    const detach = child.detach.bind(child);
    let detached = false;
    vi.spyOn(child, 'detach').mockImplementation(async () => {
      if (detached) return;
      await detach();
      detached = true;
    });
    const write = vi.spyOn(child, 'write_prompt').mockImplementation(async () => {
      entered.resolve();
      await release.promise;
      return new BackendPromptAccepted({ composed_content_delivered: true });
    });
    await f.core.take_backend_event(
      token,
      new PermissionAskRaised({
        ask: new BackendPermissionAsk({
          ask_id: 'permission',
          title: 'Permission',
          detail: null,
          options: [],
        }),
      }),
    );
    await f.core.take_backend_event(
      token,
      new UserInputRequested({
        request: new BackendUserInputRequest({ request_id: 'input', questions: [] }),
      }),
    );
    const content = text_message_content('original');
    replay = f.core.resume_undelivered_prompt(
      reference,
      {
        turn_number: token.turn_number,
        content,
        sender_content: content,
        sender_label: 'Owner',
        sender_message_count: 1,
        mode: 'queue',
        model_change: null,
        reasoning_effort_change: null,
        reasoning_effort_is_set: false,
        automatic_compaction: false,
      },
      { start_needed: false },
    );
    await entered.promise;
    expect(state.reserved_turn?.token).toEqual(token);
    const prompt_fact = f.store
      .lifecycle_facts('core')
      .find((fact) => fact.operation === 'prompt' && fact.turn_number === token.turn_number);
    await f.core.kill('core');
    expect(state.running_turn).toBeNull();
    const requests = f.store.lifecycle_facts('core').filter((fact) => fact.kind === 'request');
    expect(requests.map((fact) => fact.status)).toEqual(['unavailable', 'unavailable']);
    const endings = f.store
      .read_events_after('core', before_reused_turn)
      .filter((row) => row.kind === 'turn_ended');
    expect(endings).toHaveLength(1);
    expect(endings[0]!.payload).toMatchObject({ ending: 'interrupted' });
    expect(
      f.store
        .lifecycle_facts('core')
        .find((fact) => fact.operation === 'prompt' && fact.turn_number === token.turn_number),
    ).toEqual(prompt_fact);
    release.resolve();
    await replay;
    expect(close_rewritten).toHaveBeenCalledExactlyOnceWith(
      'core',
      token.turn_number,
      'The turn ended before its message was written again.',
    );
    expect(write).toHaveBeenCalledTimes(1);
    expect(f.children).toHaveLength(1);
    // Source-bound reservations can lack a transport child column during preparation.
    now = 1;
    expect(
      f.core.start_admission.try_start(await f.core.automatic_start_request('source-only', 'core')),
    ).toBe(true);
    const source_turn = { conversation_id: 'core', turn_number: token.turn_number + 1 };
    f.core.start_admission.bind(['source-only'], source_turn);
    f.core.start_admission.persist_bindings(f.store.start_admission_database, source_turn);
    f.core.start_admission.bind_source(source_turn, child);
    Object.assign(f.core.options.backend_registry.require_live('codex').child_factory, {
      recover: () => child,
      retained_runs: () => [
        new RetainedBackendRun({
          reference,
          turn: token,
          delivery_uncertain: false,
          retirement_confirmed: true,
        }),
      ],
    });
    await f.core.wait_until_quiescent();
    // The earlier confirmed Stop cleared the active source. This separate retirement
    // names the same retained historical receipt for its source-only binding.
    state.retained_child_reference = reference;
    expect(await f.core.retire_child_for_rebind(state, child)).toBe(true);
    expect(f.core.start_admission.snapshots()).toEqual([]);
    expect(f.database.read((sql) => sql.all('SELECT * FROM automatic_start_bindings'))).toEqual([]);
  } finally {
    release.resolve();
    await replay;
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});

// Risk: a retained remote child whose backend has no live instance on this home loses its
// prompts to "unknown agent backend" and its steer to a refusal.
// Gap: every other test runs a remote child on a backend that is live here.
test('a retained remote child takes prompts and steers without a live instance here', async () => {
  const f = setup();
  try {
    await f.start();
    await f.send('first', 'first');
    const child = await f.child,
      state = f.core.conversation_state('core')!;
    await child.finish();
    await f.core.wait_until_quiescent();
    state.retained_child_reference = {
      conversation_id: 'core',
      executor_incarnation: 'remote',
      child_id: 'retained',
    };
    Object.defineProperty(child, 'stop_outside_lock', { value: true });
    const registry = f.core.options.backend_registry;
    vi.spyOn(registry, 'get').mockImplementation((id) => ({
      kind: 'unavailable',
      instance_id: id as never,
      driver: null,
      reason: 'No live instance on this home.',
    }));
    expect(await f.send('second', 'second')).toBeInstanceOf(PromptDeliveryStarted);
    expect(
      await f.core.send('core', text_message_content('steer'), {
        sender_label: 'Owner',
        sender_message_id: 'steer',
        mode: 'steer',
      }),
    ).toBeInstanceOf(PromptDeliveryInjected);
    const calls = (await child.inspect()).calls.map((call) => call.command);
    expect(calls.filter((command) => command === 'write_prompt')).toHaveLength(2);
    expect(calls.at(-1)).toBe('steer');
  } finally {
    vi.restoreAllMocks();
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});
