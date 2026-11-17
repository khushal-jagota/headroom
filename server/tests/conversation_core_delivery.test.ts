import { setup } from './conversation_core_fixture.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';
/** Internal ordering risks that HTTP request lists cannot pause at their exact boundary. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import {
  ConversationRoleMaterials,
  ConversationStartRequest,
  PromptDeliveryQueued,
  PromptDeliveryStarted,
  PromptDeliveryUncertain,
  PromptDeliveryRefused,
  PromptDeliveryRefusalReason,
  ResolvedConversationStart,
} from '../conversation/contracts.ts';
import {
  AgentMessageCompleted,
  AgentMessageDelta,
  BackendSpawnFailed,
  TurnEnded,
  ToolCallStarted,
} from '../conversation/backends/contracts.ts';
import type { BackendChild, BackendChildFactory } from '../conversation/backends/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { ScriptedChild } from '../conversation/testing/scripted_child.ts';
import type { ScriptedBehavior } from '../conversation/testing/scripted_protocol.ts';
import type { ConversationStoreOptions } from '../conversation/record/contracts.ts';
import type { ConversationCoreOptions } from '../conversation/runtime/contracts.ts';

// Inventory #8: the provider reports news while its prompt receipt is still withheld.
// The real scripted process and real record together prove the provider/core/store boundary.
test('Manager role text appears only on the first prompt in the conversation history', async () => {
  const f = setup({
    role_actor: 'manager',
    behavior: { events: [{ event: { kind: 'TurnEnded', values: { ending: 'completed' } } }] },
  });
  await f.start();
  expect(await f.send('first', 'first')).toBeInstanceOf(PromptDeliveryStarted);
  await f.core.wait_until_quiescent();
  expect([PromptDeliveryStarted, PromptDeliveryQueued]).toContain(
    (await f.send('second', 'second')).constructor,
  );
  await f.core.wait_until_quiescent();
  const child = await f.child;
  const prompts = (await child.inspect()).calls.filter((call) => call.command === 'write_prompt');
  expect(prompts).toHaveLength(2);
  expect(JSON.stringify(prompts[0])).toContain('Role');
  expect(JSON.stringify(prompts[1])).not.toContain('Role');
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toContain('prompt');
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

test('backend news waits for the accepted prompt receipt and prose stays transient', async () => {
  let revoked = false;
  const signalFailure = new Error('Signal is not current');
  const f = setup({
    core: {
      require_signal_current() {
        if (revoked) throw signalFailure;
      },
    },
    behavior: {
      barrier: 'receipt',
      events: [
        {
          event: {
            kind: 'ToolCallStarted',
            values: { tool_call_id: 'tool', title: 'Tool', tool_kind: 'read' },
          },
        },
        { event: { kind: 'TurnEnded', values: { ending: 'completed' } } },
      ],
    },
  });
  await f.start();
  expect(
    f.core.start_admission.try_start(await f.core.automatic_start_request('sender', 'core')),
  ).toBe(true);
  const sent = f.send();
  const child = await f.child;
  await child.wait_for_barrier('receipt');
  expect(f.store.read_events_after('core', 0)).toEqual([]);
  expect(
    f.database.read((sql) => sql.all('SELECT identity,turn_number FROM automatic_start_bindings')),
  ).toEqual([{ identity: 'sender', turn_number: 1 }]);
  expect(f.core.start_admission.snapshots()).toHaveLength(1);
  // Admission remains visible before the prompt receipt reaches durable storage.
  expect(await f.core.sender_message_ids_awaiting_outcome('core')).toEqual(new Set(['sender']));
  expect(await f.core.sender_message_ids_awaiting_outcome('unknown')).toEqual(new Set());
  const release = await f.core.conversations.get('core')!.lock.acquire();
  try {
    expect(await f.core.is_running('core')).toBe(false);
    expect(await f.core.has_pending_permission_ask('core')).toBe(false);
    expect(await f.core.has_pending_user_input('core')).toBe(false);
    expect(await f.core.is_running('unknown')).toBe(false);
    expect(f.core.conversations.has('unknown')).toBe(false);
  } finally {
    release();
  }
  await child.release('receipt');
  expect(await sent).toBeInstanceOf(PromptDeliveryStarted);
  expect(await f.core.sender_message_ids_awaiting_outcome('core')).toEqual(new Set());
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'tool_call_started',
    'turn_ended',
  ]);
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(1);
  expect(await f.core.is_running('core')).toBe(false);
  expect(f.core.start_admission.snapshots()).toEqual([]);
  expect(f.database.read((sql) => sql.all('SELECT * FROM automatic_start_bindings'))).toEqual([]);
  // The existing live-state proof also fences absent IDs after Signal revocation.
  // The three Boolean readers must not read/adopt a stored conversation.
  const read = vi.spyOn(f.store, 'read_conversation');
  read.mockClear();
  for (const id of ['core', 'unknown']) {
    expect(await f.core.is_running(id)).toBe(false);
    expect(await f.core.has_pending_permission_ask(id)).toBe(false);
    expect(await f.core.has_pending_user_input(id)).toBe(false);
  }
  expect(read).not.toHaveBeenCalled();
  revoked = true;
  for (const id of ['core', 'unknown']) {
    await expect(f.core.is_running(id)).rejects.toBe(signalFailure);
    await expect(f.core.has_pending_permission_ask(id)).rejects.toBe(signalFailure);
    await expect(f.core.has_pending_user_input(id)).rejects.toBe(signalFailure);
    await expect(f.core.pending_permission_ask_ids(id)).rejects.toBe(signalFailure);
    await expect(f.core.pending_user_input_request_ids(id)).rejects.toBe(signalFailure);
  }
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Inventory #11: possible emission must retain identity and must not send a retry.
test('possible emission stays uncertain and a duplicate never writes again', async () => {
  const f = setup({ behavior: { error: { name: 'PromptWriteFailed', message: 'receipt lost' } } });
  await f.start();
  expect(
    f.core.start_admission.try_start(await f.core.automatic_start_request('sender', 'core')),
  ).toBe(true);
  expect(await f.send()).toBeInstanceOf(PromptDeliveryUncertain);
  expect(await f.send()).toBeInstanceOf(PromptDeliveryUncertain);
  const child = await f.child;
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(1);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt_delivery_uncertain',
  ]);
  expect(f.store.lifecycle_facts('core').find((fact) => fact.operation === 'prompt')?.status).toBe(
    'unknown',
  );
  expect(f.core.workspace_activity()).toBe('busy');
  expect(f.core.start_admission.snapshots()).toMatchObject([
    { state: 'uncertain', turn: { conversation_id: 'core', turn_number: 1 } },
  ]);
  expect(
    f.database.read((sql) => sql.all('SELECT identity FROM automatic_start_bindings')),
  ).toEqual([{ identity: 'sender' }]);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Inventory #35: a record failure after native acceptance retries storage, not the provider.
// The same test exposes runtime prediction during commit and its rollback after rejection.
test('a failed save restores runtime and retains one accepted sender through storage retry', async () => {
  const rejected = new Deferred<void>();
  let first = true;
  let core: SqliteProcessConversationSystem;
  const f = setup({
    record: {
      transaction_participant: (_tx, candidate) => {
        if (candidate.receipt_sequences.length === 0) return;
        expect(core.runtime_turn('core')?.token.turn_number).toBe(1);
        if (first) {
          first = false;
          rejected.resolve();
          throw new Error('synthetic save failure');
        }
      },
    },
  });
  core = f.core;
  await f.start();
  const sent = f.send();
  await rejected.promise;
  expect(core.runtime_turn('core')).toBeNull();
  expect(f.store.read_events_after('core', 0)).toEqual([]);
  const duplicate = f.send();
  expect(await sent).toBeInstanceOf(PromptDeliveryStarted);
  expect(await duplicate).toBeInstanceOf(PromptDeliveryStarted);
  const child = await f.child;
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(1);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual(['prompt']);
  await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Inventory #33: a failed live-tail subscriber must not turn a committed receipt into a retry.
test('publication failure after receipt commit does not append or deliver twice', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const secondary = new Error('Ticket write failed');
  const f = setup({
    core: {
      publish_event: () => {
        throw new Error('subscriber failed');
      },
      on_held_model_change: () => {
        throw secondary;
      },
    },
  });
  await f.start();
  f.store.create_conversation(
    new ResolvedConversationStart({
      ...f.store.read_conversation('core')!.resolved_start(),
      conversation_id: 'disabled-control',
      model: 'disabled',
    }),
  );
  f.database.write((sql) =>
    sql.run('INSERT INTO backend_model_enablement VALUES(?,?,0)', ['codex', 'disabled']),
  );
  expect(
    await f.core.send('disabled-control', text_message_content('refuse disabled'), {
      sender_label: 'Owner',
      sender_message_id: 'disabled-model',
    }),
  ).toEqual(
    new PromptDeliveryRefused({
      refusal_reason: PromptDeliveryRefusalReason.model_resolution_failed,
    }),
  );
  expect(f.children).toHaveLength(0);
  expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
  expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
  expect(
    await f.core.send('core', text_message_content('change'), {
      sender_label: 'Owner',
      sender_message_id: 'held-change',
      model_change: 'next',
    }),
  ).toBeInstanceOf(PromptDeliveryQueued);
  const child = await f.child;
  await child.finish();
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'model_changed',
    'prompt',
  ]);
  expect(
    await f.core.send('core', text_message_content('change'), {
      sender_label: 'Owner',
      sender_message_id: 'held-change',
      model_change: 'next',
    }),
  ).toBeInstanceOf(PromptDeliveryStarted);
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(2);
  expect(await f.core.is_running('core')).toBe(true);
  expect(f.core.conversations.get('core')!.reserved_turn).toBeNull();
  expect(f.core.conversations.get('core')!.sender_messages_being_delivered.size).toBe(0);
  expect(console.error).toHaveBeenCalledWith(
    'conversation core could not drain held prompts',
    secondary,
  );
  await child.emit(
    new ToolCallStarted({ tool_call_id: 'after-error', title: 'After error', tool_kind: 'read' }),
  );
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.store.read_events_after('core', 4).map((row) => row.kind)).toEqual([
    'tool_call_started',
  ]);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Inventory #7: an old turn's terminal event must not end its successor.
test('a turn ends once and its late terminal event does not end the next turn', async () => {
  const f = setup();
  await f.start();
  await f.send();
  const child = await f.child;
  const old = f.core.runtime_turn('core')!.token;
  await child.finish();
  await child.flush_events();
  await f.core.wait_until_quiescent();
  await f.send('two', 'second');
  await child.emit(new TurnEnded({ ending: 'completed' }), old);
  await child.emit(
    new AgentMessageCompleted({ content: text_message_content('not a notebook message') }),
    old,
  );
  await child.emit(
    new ToolCallStarted({ tool_call_id: 'stale', title: 'Stale', tool_kind: 'read' }),
    old,
  );
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.core.runtime_turn('core')?.token.turn_number).toBe(2);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'prompt',
  ]);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Inventory #16: the queue entry disappears before its receipt, but sender identity must remain.
test('dequeued sender identity waits for the receipt instead of crossing the wire again', async () => {
  const f = setup();
  await f.start();
  await f.send();
  expect(await f.send('held', 'held-sender')).toBeInstanceOf(PromptDeliveryQueued);
  expect(await f.core.sender_message_ids_awaiting_outcome('core')).toEqual(
    new Set(['held-sender']),
  );
  const child = await f.child;
  await child.configure('write_prompt', { barrier: 'queued-receipt' });
  await child.finish();
  await child.wait_for_barrier('queued-receipt');
  expect(await f.core.held_prompts('core')).toEqual([]);
  expect(await f.core.sender_message_ids_awaiting_outcome('core')).toEqual(
    new Set(['held-sender']),
  );
  const duplicate = f.send('held', 'held-sender');
  await child.release('queued-receipt');
  expect(await duplicate).toBeInstanceOf(PromptDeliveryStarted);
  await f.core.wait_until_quiescent();
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(2);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'prompt',
  ]);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Earlier receipt tests do not prove that only the exact live sink releases capacity,
// or that two queued automatic messages share one permit when the core dequeues them.
test('automatic capacity waits for exact agent activity and queued messages share a permit', async () => {
  let now = 0;
  const f = setup({ core: { monotonic_now: () => now } });
  try {
    await f.start();
    const offer = async (id: string) =>
      f.core.start_admission.try_start(await f.core.automatic_start_request(id, 'core'));
    expect(await offer('first')).toBe(true);
    expect(await f.send('first', 'first')).toBeInstanceOf(PromptDeliveryStarted);
    const child = await f.child;
    const state = f.core.conversations.get('core')!;
    expect(f.core.start_admission.snapshots()).toHaveLength(1);
    await child.emit(new AgentMessageDelta({ text_delta: 'stale' }), {
      conversation_id: 'core',
      turn_number: 99,
    });
    await child.flush_events();
    await f.core.wait_until_quiescent();
    expect(f.core.start_admission.snapshots()).toHaveLength(1);
    const sink = state.child_event_sink;
    state.child_event_sink = null;
    await child.emit(new AgentMessageDelta({ text_delta: 'old sink' }));
    await child.flush_events();
    expect(f.core.start_admission.snapshots()).toHaveLength(1);
    state.child_event_sink = sink;
    await child.emit(new AgentMessageDelta({ text_delta: 'accepted' }));
    await child.flush_events();
    expect(f.core.start_admission.snapshots()).toEqual([]);
    now = 1;
    expect(await offer('second')).toBe(true);
    expect(await f.send('second', 'second')).toBeInstanceOf(PromptDeliveryQueued);
    expect(await offer('third')).toBe(true);
    expect(await f.send('third', 'third')).toBeInstanceOf(PromptDeliveryQueued);
    expect(f.core.start_admission.snapshots().map((entry) => entry.state)).toEqual([
      'queued',
      'queued',
    ]);
    await child.finish();
    await f.core.wait_until_quiescent();
    const admitted = f.core.start_admission.snapshots();
    expect(admitted).toHaveLength(2);
    expect(new Set(admitted.map((entry) => entry.permit_identity)).size).toBe(1);
    expect(admitted.map((entry) => entry.turn?.turn_number)).toEqual([2, 2]);
    const calls = (await child.inspect()).calls.filter((call) => call.command === 'write_prompt');
    expect(calls).toHaveLength(2);
    expect(calls[1].arguments.sender_message_count).toBe(2);
    await child.emit(new AgentMessageDelta({ text_delta: 'combined accepted' }));
    await child.flush_events();
    expect(f.core.start_admission.snapshots()).toEqual([]);
    expect(f.database.read((sql) => sql.all('SELECT * FROM automatic_start_bindings'))).toEqual([]);
    // A model rebind replaces the child without surrendering the new turn's permit.
    now = 2;
    expect(await offer('rebind')).toBe(true);
    expect(await f.send('rebind', 'rebind')).toBeInstanceOf(PromptDeliveryQueued);
    await child.configure('write_prompt', {
      error: { name: 'NeedsRebind', message: 'fresh configuration required' },
    });
    await child.finish();
    await f.core.wait_until_quiescent();
    expect(f.children).toHaveLength(2);
    expect(f.core.start_admission.snapshots()).toMatchObject([
      { request: { identity: 'rebind' }, state: 'active', turn: { turn_number: 3 } },
    ]);
    await f.children[1].emit(new AgentMessageDelta({ text_delta: 'replacement accepted' }));
    await f.children[1].flush_events();
    expect(f.core.start_admission.snapshots()).toEqual([]);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});

// A pre-wire save departure must release the turn reservation and its unattempted binding.
// Existing accepted-save retries start after native delivery and cannot prove this boundary.
test('a failed admission save releases preparation before any provider starts', async () => {
  let now = 0;
  const f = setup({ core: { monotonic_now: () => now } });
  try {
    await f.start();
    expect(
      f.core.start_admission.try_start(await f.core.automatic_start_request('sender', 'core')),
    ).toBe(true);
    const failure = new DOMException('admission save observer left', 'AbortError');
    vi.spyOn(f.core.start_admission, 'persist_bindings').mockImplementationOnce(() => {
      throw failure;
    });
    await expect(f.send()).rejects.toBe(failure);
    const state = f.core.conversations.get('core')!;
    expect(state.reserved_turn).toBeNull();
    expect(state.phase).toBe('idle');
    expect(f.children).toEqual([]);
    expect(f.core.start_admission.snapshots()).toEqual([]);
    expect(f.database.read((sql) => sql.all('SELECT * FROM automatic_start_bindings'))).toEqual([]);
    now = 1;
    expect(
      f.core.start_admission.try_start(await f.core.automatic_start_request('sender', 'core')),
    ).toBe(true);
    expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
    expect(f.children).toHaveLength(1);
    const child = await f.child;
    for (const method of ['mark_held_sender_messages_leaving_queue', 'reserve_turn'] as const) {
      const identity = `queued-${method}`;
      f.core.start_admission.try_start(await f.core.automatic_start_request(identity, 'core'));
      expect(await f.send('queued after preparation failure', identity)).toBeInstanceOf(
        PromptDeliveryQueued,
      );
      const injected = vi.spyOn(f.store, method).mockImplementationOnce(() => {
        throw failure;
      });
      // Allow this dequeue grant before the injected failure, rather than fail
      // the later explicit retry after pacing prevented the first preparation.
      now += 1;
      await child.finish();
      await f.core.wait_until_quiescent();
      expect(injected).toHaveBeenCalledOnce();
      expect(state.phase).toBe('idle');
      expect(state.reserved_turn).toBeNull();
      expect(state.sender_messages_being_delivered.size).toBe(0);
      expect((await f.core.held_prompts('core')).map((held) => held.sender_message_id)).toEqual([
        identity,
      ]);
      expect(f.core.start_admission.snapshots()).toMatchObject([
        { request: { identity }, permit_identity: null, turn: null },
      ]);
      now += 1;
      await f.core.drain_held_prompts(state);
      expect(await f.core.held_prompts('core')).toEqual([]);
      expect(state.phase).toBe('running');
    }
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});

// A too-old CLI is refused before the launch record, so no custody needs proving afterwards.
test('the CLI floor check refuses a turn before child custody is written', async () => {
  const custody = () =>
    f.database.read((sql) => sql.all('SELECT state FROM conversation_child_custody'));
  const observed: unknown[] = [];
  const start = vi.fn();
  const factory: BackendChildFactory = () =>
    ({
      stop_outside_lock: false,
      async confirm_cli_compatible() {
        observed.push(custody());
        throw new BackendSpawnFailed('Update Codex to at least 0.148.0.');
      },
      start,
    }) as unknown as BackendChild;
  const f = setup({
    core: { backend_child_factories: { codex: factory, claude: factory, hermes: factory } },
  });
  try {
    await f.start();
    const before = custody();
    expect(before).not.toContainEqual({ state: 'launch_pending' });
    expect(await f.send()).toEqual(
      new PromptDeliveryRefused({
        refusal_reason: PromptDeliveryRefusalReason.backend_did_not_start,
      }),
    );
    expect(observed).toEqual([before]);
    expect(custody()).toEqual(before);
    expect(start).not.toHaveBeenCalled();
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
