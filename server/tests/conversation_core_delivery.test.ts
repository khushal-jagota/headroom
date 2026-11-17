import {
  for_every_built_in_backend,
  registry_of_factories,
  scripted_instance_controls,
} from '../drivers/testing/registry.ts';
import type { SelectionTransitionPlan } from '../drivers/core/selection.ts';
import { AUTOMATIC_COMPACTION_AFTER_SECONDS } from '../conversation/runtime/configuration.ts';
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
  TokenUsageReported,
  TurnEnded,
  ToolCallStarted,
} from '../conversation/backends/contracts.ts';
import type { BackendChild, BackendChildFactory } from '../conversation/backends/contracts.ts';
import { message_content_text, text_message_content } from '../conversation/message_content.ts';
import { AgentMessageEventPayload } from '../conversation/events.ts';
import { conversation_attention } from '../notifications/attention.ts';
import { Deferred } from '../core/deferred.ts';
import { ScriptedChild } from '../conversation/testing/scripted_child.ts';
import type { ScriptedBehavior } from '../conversation/testing/scripted_protocol.ts';
import type { ConversationStoreOptions } from '../conversation/record/contracts.ts';
import type { ConversationCoreOptions } from '../conversation/runtime/contracts.ts';
import { ConversationRecord } from '../conversation/record/contracts.ts';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../lifecycle/one_shot_children.ts';
import type { WorkerSupervisionRuntime } from '../worker_supervision/runtime.ts';
import { create_claude_backend_child_factory } from '../drivers/claude/runtime/adapter.ts';
import type { ClaudeSdkClient } from '../drivers/claude/runtime/query_contracts.ts';
import { create_codex_backend_child_factory } from '../drivers/codex/runtime/adapter.ts';
import type { CodexClient } from '../drivers/codex/runtime/adapter.ts';
import { CodexRequestRejected } from '../drivers/codex/runtime/client.ts';
import { RememberedCodexLoginRejection } from '../drivers/codex/runtime/login.ts';
import { create_hermes_backend_child_factory } from '../drivers/hermes/runtime/adapter.ts';
import { HermesAcpLauncher } from '../drivers/hermes/runtime/client.ts';
import type { HermesAcpClient } from '../drivers/hermes/runtime/client.ts';

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

test('backend news waits for the accepted prompt receipt and each finished reply is recorded once', async () => {
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
      // Claude can finish a text block, call a tool, and finish another block in one turn.
      events: [
        { event: { kind: 'AgentMessageDelta', values: { text_delta: 'before' } } },
        { event: { kind: 'AgentMessageCompleted', values: { text: 'before the tool' } } },
        {
          event: {
            kind: 'ToolCallStarted',
            values: { tool_call_id: 'tool', title: 'Tool', tool_kind: 'read' },
          },
        },
        { event: { kind: 'AgentMessageCompleted', values: { text: 'after the tool' } } },
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
  const recorded = f.store.read_events_after('core', 0);
  expect(recorded.map((row) => row.kind)).toEqual([
    'prompt',
    'agent_message',
    'tool_call_started',
    'agent_message',
    'turn_ended',
  ]);
  expect(
    recorded.flatMap((row) =>
      row.payload instanceof AgentMessageEventPayload
        ? [message_content_text(row.payload.content)]
        : [],
    ),
  ).toEqual(['before the tool', 'after the tool']);
  // Full reads these rows on reload. Focus leaves the replies out.
  expect(
    f.database.read((sql) =>
      sql.all(
        "SELECT row_kind,focus_visible FROM conversation_view_rows WHERE conversation_id='core' AND row_kind='agent_message' ORDER BY sequence",
      ),
    ),
  ).toEqual([
    { row_kind: 'agent_message', focus_visible: 0 },
    { row_kind: 'agent_message', focus_visible: 0 },
  ]);
  // A recorded reply is not addressed to the owner: no unread mark, so no owner notification.
  expect(
    f.database.read((sql) => conversation_attention(sql, 'core').get('core')?.unread_message),
  ).toBe(false);
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
    core: {
      backend_registry: registry_of_factories(for_every_built_in_backend(() => factory)),
    },
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

// A live Manager's resume failed and nobody could see why. Each real adapter, with only its
// native client faked, must hand the backend's own words to the record and to one log line.
async function refused_resume(factory: BackendChildFactory, sends = 1) {
  const f = setup({
    core: {
      backend_registry: registry_of_factories(for_every_built_in_backend(() => factory)),
    },
  });
  const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    await f.start();
    const state = f.core.conversations.get('core')!;
    state.record = new ConversationRecord({
      ...state.record,
      vendor_session_cursor: 'lost-session',
    });
    const fates = [];
    for (let index = 0; index < sends; index += 1)
      fates.push(await f.send('one', `sender-${index}`));
    return {
      fates,
      refusals: f.store
        .read_events_after('core', 0)
        .filter((row) => row.kind === 'prompt_delivery_refused')
        .map((row) => row.payload as { refusal_reason: string; refusal_detail: string }),
      logged: logged.mock.calls
        .map(([line]) => String(line))
        .filter((line) => line.startsWith('conversation backend failed')),
    };
  } finally {
    logged.mockRestore();
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
}
const SESSION_DID_NOT_LOAD = new PromptDeliveryRefused({
  refusal_reason: PromptDeliveryRefusalReason.session_did_not_load,
});
function expect_session_load_refusal(
  observed: Awaited<ReturnType<typeof refused_resume>>,
  ...texts: string[]
): void {
  expect(observed.fates).toEqual([SESSION_DID_NOT_LOAD]);
  expect(observed.refusals).toHaveLength(1);
  expect(observed.refusals[0].refusal_reason).toBe('session_did_not_load');
  expect(observed.logged).toHaveLength(1);
  expect(observed.logged[0]).toContain('conversation_id=core');
  expect(observed.logged[0]).toContain('error_class=SessionLoadFailed');
  for (const text of texts) {
    expect(observed.refusals[0].refusal_detail).toContain(text);
    expect(observed.logged[0]).toContain(text);
  }
}
const unreachable = async (): Promise<never> => {
  throw new Error('nothing runs after a failed resume');
};
function failing_claude(): BackendChildFactory {
  const child_processes = new ChildProcesses();
  const claude = create_claude_backend_child_factory(
    { claude_executable: '/synthetic/claude' },
    {
      child_processes,
      one_shot_children: new OneShotChildren(child_processes),
      worker_supervision: {} as WorkerSupervisionRuntime,
      client_factory: (options): ClaudeSdkClient => ({
        async connect() {
          options.stderr?.('session file is missing\n');
          throw new Error('No conversation found with session ID: lost-session');
        },
        get_server_info: unreachable,
        query: unreachable,
        receive_messages: () => {
          throw new Error('no stream after a failed resume');
        },
        watch_user_message() {},
        forget_user_message() {},
        wait_for_user_message_admission: unreachable,
        user_message_uuids_for_result: () => new Set(),
        user_message_is_settled: () => true,
        wait_for_user_message_settlement: unreachable,
        interrupt_and_cancel_queued: unreachable,
        apply_reasoning_effort: unreachable,
        async disconnect() {},
      }),
    },
  );
  // The CLI floor check would run the synthetic executable.
  return (values) => Object.assign(claude(values), { confirm_cli_compatible: undefined });
}
function failing_codex(standard_error: string): BackendChildFactory {
  const client: CodexClient = {
    is_alive: true,
    end_reason: null,
    async start() {},
    async stop() {},
    async request(method) {
      if (method === 'thread/resume')
        throw new CodexRequestRejected(method, -32600, 'no rollout found for thread lost-session');
      return {};
    },
    begin_request: unreachable,
    finish_request: unreachable,
    async notify() {},
    async respond() {},
    async respond_with_error() {},
    standard_error_tail: () => standard_error,
    standard_error_bytes_so_far: () => 0,
    standard_error_since: () => null,
    async settle_standard_error() {},
  };
  return create_codex_backend_child_factory(
    { argv: ['/synthetic/codex', 'app-server'] },
    {
      child_processes: new ChildProcesses(),
      remembered_login_rejection: new RememberedCodexLoginRejection(),
      worker_supervision: {} as WorkerSupervisionRuntime,
      client_factory: () => client,
    },
  );
}

test("a failed Claude resume records Claude's own error text", async () => {
  expect_session_load_refusal(
    await refused_resume(failing_claude()),
    'No conversation found with session ID: lost-session',
    'session file is missing',
  );
});

test("a failed Codex resume records Codex's own error text", async () => {
  expect_session_load_refusal(
    await refused_resume(failing_codex('ERROR codex_core: rollout file is missing\n')),
    'no rollout found for thread lost-session',
    'rollout file is missing',
  );
});

test("a failed Hermes resume records Hermes's own error text", async () => {
  const client = {
    signal: new AbortController().signal,
    async request(method: string) {
      if (method === 'session/load') throw new Error('Session lost-session not found');
      return {};
    },
    close() {},
  };
  try {
    vi.spyOn(HermesAcpLauncher.prototype, 'start').mockResolvedValue(
      client as unknown as HermesAcpClient,
    );
    vi.spyOn(HermesAcpLauncher.prototype, 'stop').mockResolvedValue();
    vi.spyOn(HermesAcpLauncher.prototype, 'standard_error_tail').mockReturnValue(
      'KeyError: lost-session\n',
    );
    const factory = create_hermes_backend_child_factory(
      { argv: ['/synthetic/hermes'], environment_overrides: [] },
      { child_processes: new ChildProcesses(), worker_supervision: {} as WorkerSupervisionRuntime },
    );
    expect_session_load_refusal(
      await refused_resume(factory),
      'Session lost-session not found',
      'KeyError: lost-session',
    );
  } finally {
    vi.restoreAllMocks();
  }
});

// A refusal detail is shown in place of the friendly sentence, so it must hold no secret.
test('a backend failure text is redacted in the record and the log', async () => {
  const observed = await refused_resume(
    failing_codex(
      'GET https://example.test/v1/models?key=AIzaQUERYSECRET failed\n' +
        'token rejected: Bearer BEARERSECRET\n' +
        'config api_key=sk-ASSIGNEDSECRET\n',
    ),
  );
  expect_session_load_refusal(observed, 'no rollout found for thread lost-session', '[redacted]');
  const kept = JSON.stringify(observed.refusals) + observed.logged.join('\n');
  for (const secret of ['QUERYSECRET', 'BEARERSECRET', 'ASSIGNEDSECRET'])
    expect(kept).not.toContain(secret);
});

// A worker step retries a lost session. Each retry must not add another row and log line.
test('a repeated identical session-load failure is recorded and logged once', async () => {
  const observed = await refused_resume(failing_claude(), 2);
  expect(observed.fates).toEqual([SESSION_DID_NOT_LOAD, SESSION_DID_NOT_LOAD]);
  expect(observed.refusals).toHaveLength(1);
  expect(observed.logged).toHaveLength(1);
});

// The selection plan replaced the adapters' own restart throws, and `reject` has no producer
// yet, so each plan outcome is driven here through a stub on the scripted driver instance.
function stub_selection_plan(f: ReturnType<typeof setup>, plan: SelectionTransitionPlan) {
  const controls = scripted_instance_controls(
    f.core.options.backend_registry,
    f.core.conversations.get('core')!.record.backend_key,
  );
  const stub = vi.fn((_input: Parameters<typeof controls.plan_selection_transition>[0]) => plan);
  controls.plan_selection_transition = stub;
  return stub;
}
function written_prompts(child: ScriptedChild) {
  return child
    .inspect()
    .then((snapshot) => snapshot.calls.filter((call) => call.command === 'write_prompt'));
}

test('a restart plan retires the child and writes once on a fresh one', async () => {
  const f = setup();
  try {
    await f.start();
    const first = stub_selection_plan(f, { type: 'apply_on_next_turn' });
    expect(await f.send('one', 'one')).toBeInstanceOf(PromptDeliveryStarted);
    // A fresh child is started on its target, so no plan is asked for.
    expect(first).not.toHaveBeenCalled();
    const child = await f.child;
    await child.finish();
    await f.core.wait_until_quiescent();
    // The HTTP route names an effort without `reasoning_effort_is_set`.
    const send = (text: string, reasoning_effort_change: string) =>
      f.core.send('core', text_message_content(text), {
        sender_label: 'Owner',
        sender_message_id: text,
        reasoning_effort_change,
      });
    expect(await send('two', 'high')).toBeInstanceOf(PromptDeliveryStarted);
    expect(first.mock.calls.map(([input]) => [input.current, input.target])).toEqual([
      [
        { model: 'model', reasoning_effort: null },
        { model: 'model', reasoning_effort: 'high' },
      ],
    ]);
    await child.finish();
    await f.core.wait_until_quiescent();
    const restart = stub_selection_plan(f, { type: 'restart_session' });
    expect(await send('three', 'low')).toBeInstanceOf(PromptDeliveryStarted);
    // The applied change is what the child now runs with.
    expect(restart.mock.calls.map(([input]) => [input.current, input.target])).toEqual([
      [
        { model: 'model', reasoning_effort: 'high' },
        { model: 'model', reasoning_effort: 'low' },
      ],
    ]);
    expect(f.children).toHaveLength(2);
    expect(f.core.conversations.get('core')!.child).toBe(f.children[1]);
    // The retired child kept only its own two prompts.
    const retired = child.last_observation!.calls.map((call) => call.command);
    expect(retired.filter((command) => command === 'write_prompt')).toHaveLength(2);
    expect(retired.at(-1)).toBe('stop');
    expect(await written_prompts(f.children[1])).toHaveLength(1);
    expect((await f.children[1].inspect()).delivered_text).toEqual(['three']);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});

test('a rejected selection refuses the prompt, records why, and logs nothing', async () => {
  const f = setup();
  const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    await f.start();
    await f.send('one', 'one');
    const child = await f.child;
    await child.finish();
    await f.core.wait_until_quiescent();
    stub_selection_plan(f, { type: 'reject', reason: 'This session cannot switch models.' });
    expect(await f.send('two', 'two')).toEqual(
      new PromptDeliveryRefused({
        refusal_reason: PromptDeliveryRefusalReason.backend_rejected_prompt,
      }),
    );
    expect(
      f.store
        .read_events_after('core', 0)
        .filter((row) => row.kind === 'prompt_delivery_refused')
        .map((row) => row.payload),
    ).toMatchObject([
      {
        sender_message_id: 'two',
        refusal_reason: 'backend_rejected_prompt',
        refusal_detail: 'This session cannot switch models.',
      },
    ]);
    expect(f.children).toHaveLength(1);
    expect(await written_prompts(child)).toHaveLength(1);
    expect(logged).not.toHaveBeenCalled();
  } finally {
    logged.mockRestore();
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});

test('an automatic compaction that needs a fresh child is suppressed', async () => {
  let now = 100;
  const f = setup({
    record: { integer_now: () => now },
    core: {
      unix_time_now: () => now,
      monotonic_now: () => now,
      idle_child_stop_after_seconds: 10000,
    },
  });
  try {
    await f.start();
    await f.send();
    const child = await f.child;
    await child.emit(new TokenUsageReported({ context_input_tokens: 30000 }));
    await child.finish();
    await f.core.wait_until_quiescent();
    now += AUTOMATIC_COMPACTION_AFTER_SECONDS;
    f.store.conversations_due_for_automatic_compaction = () => ['core'];
    const restart = stub_selection_plan(f, { type: 'restart_session' });
    await f.core.sweep_idle_children();
    await f.core.wait_until_quiescent();
    expect(restart).toHaveBeenCalledOnce();
    expect(f.children).toHaveLength(1);
    expect(f.core.conversations.get('core')!.child).toBe(child);
    expect((await child.inspect()).delivered_text).toEqual(['Role\n\none']);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
