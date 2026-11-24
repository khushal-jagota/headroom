/** Gap: the live bypass dropped deltas during an outage, outside the durable event sequence.
 * Exercise the real spool and existing publication door without a backend or network host.
 */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import {
  BackgroundTaskStarted,
  AgentMessageCompleted,
  AgentMessageDelta,
  ModelThinkingHappened,
  ToolCallProgress,
  TurnEnded,
  BackendPromptAccepted,
  BackendSteerRefused,
} from '../../../conversation/backends/contracts.ts';
import { text_message_content } from '../../../conversation/message_content.ts';
import { OutboxStore } from '../storage/outbox.ts';
import { row_envelope } from '../storage/envelopes.ts';
import type { QueuedEnvelope } from '../../wire.generated.ts';
import type { MacNativeChildrenContract, MacRunningChild } from './contracts.ts';
import { MacRunnerEvents } from './events.ts';
import { create_mac_output_publication } from './publication.ts';
import { MacSourceWork } from './source_work.ts';
import { MacChildState } from './state.ts';
import { MacPresence } from '../transport/presence.ts';
import { dispatch_command } from './commands.ts';
import type { MacCommandDependencies, MacCommandWork } from './command_contracts.ts';
import { FifoLock } from '../../../conversation/runtime/state.ts';
import { PROTOCOL_VERSION } from '../../../execution_configuration.ts';

test('offline event publication preserves every delta and its order with completed output', async () => {
  const root = await mkdtemp(join(tmpdir(), 'machine-events-'));
  const stop_options = () => ({ deadline: performance.now() / 1000 + 10 });
  const clock = {
    monotonic: () => performance.now() / 1000,
    now_ns: () => BigInt(Date.now()) * 1_000_000n,
  };
  const failures: unknown[] = [];
  let store = await OutboxStore.open(root, clock, stop_options(), (error) => failures.push(error));
  const reference = { executor_incarnation: 'inc', child_id: 'child', conversation_id: 'one' };
  const turn = { conversation_id: 'one', turn_number: 1, generation: 'turn' };
  const child = {
    reference,
    active_turn: turn,
    open_tool_calls: new Set(),
  } as unknown as MacRunningChild;
  const state = new MacChildState();
  const sources = new MacSourceWork(state, () => false);
  try {
    const events = new MacRunnerEvents({
      state,
      sources,
      native_children: { get: () => child } as unknown as MacNativeChildrenContract,
      publication: create_mac_output_publication(store),
      live: {
        send: () => {
          throw new Error('Events must not use lossy delivery');
        },
      },
    });
    const sink = events.event_sink(reference);
    // More than the previous live queue retained, with no HTTP sender present.
    for (let index = 0; index < 70; index++)
      await sink.report(turn, new AgentMessageDelta({ text_delta: String(index) }));
    await sink.report(turn, new ModelThinkingHappened());
    await sink.report(turn, new ToolCallProgress({ tool_call_id: 'tool', detail: 'offline' }));
    await sink.report(
      turn,
      new AgentMessageCompleted({ content: text_message_content('complete') }),
    );
    await sink.report(turn, new TurnEnded({ ending: 'completed' }));
    await sources.finish();
    await store.stop(stop_options());
    await store.settled();
    store = await OutboxStore.open(root, clock, stop_options(), (error) => failures.push(error));
    const envelopes: QueuedEnvelope[] = [];
    while (store.pending_count()) {
      for (const row of store.pending()) {
        envelopes.push(row_envelope(store.sender_id, row));
        await store.acknowledge(row.submission_id, store.sender_id, Buffer.from('{}'), () => {});
      }
    }
    expect(envelopes.map((envelope) => envelope.sequence)).toEqual(
      Array.from({ length: 74 }, (_, index) => index + 1),
    );
    expect(envelopes.map((envelope) => envelope.payload.method)).toEqual([
      ...Array.from({ length: 70 }, () => 'agent_message_delta'),
      'model_thinking_happened',
      'tool_call_progress',
      'agent_message_completed',
      'turn_ended',
    ]);
    expect(
      envelopes
        .slice(0, 70)
        .map((envelope) => (envelope.payload.arguments as { text_delta: string }).text_delta),
    ).toEqual(Array.from({ length: 70 }, (_, index) => String(index)));
    expect(failures).toEqual([]);
  } finally {
    await store.stop(stop_options());
    await store.settled();
    await rm(root, { recursive: true, force: true });
  }
});

// Gap: an autonomous turn must receive the core token before later native events,
// and native steer/cancel must see that same token. No network/backend is needed.
test('agent turn start waits for its durable receipt and installs the allocated token', async () => {
  const root = await mkdtemp(join(tmpdir(), 'agent-turn-receipt-'));
  const stop_options = () => ({ deadline: performance.now() / 1000 + 10 });
  const store = await OutboxStore.open(
    root,
    {
      monotonic: () => performance.now() / 1000,
      now_ns: () => BigInt(Date.now()) * 1_000_000n,
    },
    stop_options(),
    () => {},
  );
  const { GatewayOutbox } = await import('../ipc/outbox.ts');
  const gateway = new GatewayOutbox(store);
  const reference = { executor_incarnation: 'inc', child_id: 'child', conversation_id: 'one' };
  const token = { conversation_id: 'one', turn_number: 2 };
  const cancelled_tokens: unknown[] = [];
  const cancel = vi.fn(async () => {
    cancelled_tokens.push(child.active_turn);
  });
  const write_prompt = vi.fn(
    async () => new BackendPromptAccepted({ composed_content_delivered: true }),
  );
  const steer = vi.fn(
    async () => new BackendSteerRefused({ refusal_reason: 'no_running_turn_to_steer_into' }),
  );
  const child = {
    reference,
    active_turn: null,
    pending_agent_turn: null,
    lock: new FifoLock(),
    native: { cancel_running_turn: cancel, write_prompt, steer },
    background_task_ids: new Set(),
    open_tool_calls: new Set(),
  } as unknown as MacRunningChild;
  const state = new MacChildState();
  const sources = new MacSourceWork(state, () => false);
  const presence = new MacPresence({
    application_revision: 'candidate',
    executor_incarnation: 'inc',
    protocol_version: PROTOCOL_VERSION,
    spool_format_version: 5,
  });
  const confirm = vi.fn((id: string, signal: AbortSignal) =>
    gateway.wait_confirmed(id, 10, signal),
  );
  try {
    const sink = new MacRunnerEvents({
      state,
      sources,
      native_children: { get: () => child } as unknown as MacNativeChildrenContract,
      publication: create_mac_output_publication(store),
      live: null,
      supports_agent_turns: () => presence.agent_started_turns,
      confirm_agent_turn: confirm,
    }).event_sink(reference);
    // An old lease has no opt-in. Skip unsupported work before a durable item or waiter exists.
    expect(await sink.start_agent_turn!('Old home')).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
    expect(store.pending_count()).toBe(0);
    presence.accepted(true);
    let settled = false;
    const started = sink.start_agent_turn!('Sub-agent finished').then((value) => {
      settled = true;
      return value;
    });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(settled).toBe(false);
    expect(child.active_turn).toBeNull();
    const row = store.pending()[0]!;
    const envelope = row_envelope(store.sender_id, row);
    expect(envelope.payload).toMatchObject({
      turn: null,
      method: 'agent_turn_started',
      arguments: { reason: 'Sub-agent finished' },
    });
    // An actual cancel followed by send-now replacement cannot overtake activation.
    const dependencies = {
      incarnation: 'inc',
      state,
      native_children: { get: () => child, is_retiring: () => false },
    } as unknown as MacCommandDependencies;
    const cancel_command = {
      command_id: 'cancel',
      child: reference,
      turn: token,
      operation: 'cancel_running_turn',
      arguments: { kind: 'cancel_running_turn' },
    } as const;
    // A steer for an ended prior turn must refuse without waiting for its replacement.
    const stale_steer = await dispatch_command(
      dependencies,
      {
        command_id: 'old-steer',
        child: reference,
        turn: { conversation_id: 'one', turn_number: 1 },
        operation: 'steer',
        arguments: {
          kind: 'steer',
          content: [{ text: 'Old turn' }],
          sender_content: [{ text: 'Old turn' }],
          sender_label: 'Owner',
        },
      },
      new Map(),
      {} as MacCommandWork,
      () => {},
    );
    expect(stale_steer.result).toMatchObject({
      kind: 'steer_refused',
      refusal_reason: 'no_running_turn_to_steer_into',
    });
    expect(settled).toBe(false);
    const cancellation = dispatch_command(
      dependencies,
      cancel_command,
      new Map(),
      {} as MacCommandWork,
      () => {},
    );
    const replacement = cancellation.then(() =>
      dispatch_command(
        dependencies,
        {
          command_id: 'replacement',
          child: reference,
          turn: { conversation_id: 'one', turn_number: 3 },
          operation: 'write_prompt',
          arguments: {
            kind: 'write_prompt',
            content: [{ text: 'Send now' }],
            sender_content: [{ text: 'Send now' }],
            sender_label: 'Owner',
            sender_message_count: 1,
            mode: 'queue',
            model_change: null,
            reasoning_effort_change: null,
          },
        },
        new Map(),
        {} as MacCommandWork,
        () => {},
      ),
    );
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(cancel).not.toHaveBeenCalled();
    expect(write_prompt).not.toHaveBeenCalled();
    expect(child.lock.locked).toBe(false);
    const receipt = {
      sender_id: store.sender_id,
      submission_id: row.submission_id,
      disposition: 'accepted' as const,
      result: { applied: true, turn: token },
      accepted_at_vps_ms: Date.now(),
    };
    await store.acknowledge(
      row.submission_id,
      store.sender_id,
      Buffer.from(JSON.stringify(receipt)),
      () => gateway.notify_confirmed(receipt),
    );
    expect(await started).toEqual(token);
    await replacement;
    expect(cancelled_tokens).toEqual([token]);
    expect(child.active_turn).toEqual({ conversation_id: 'one', turn_number: 3 });
    await sink.report(child.active_turn!, new TurnEnded({ ending: 'completed' }));
    expect(child.active_turn).toBeNull();
    expect(store.pending().map((row) => row_envelope(store.sender_id, row).payload.method)).toEqual(
      ['turn_ended'],
    );
    presence.accepted();
    expect(await sink.start_agent_turn!('Server changed')).toBeNull();
    expect(confirm).toHaveBeenCalledTimes(1);
    presence.accepted(true);
    presence.failed('Connection lost');
    expect(await sink.start_agent_turn!('Offline home')).toBeNull();
    expect(confirm).toHaveBeenCalledTimes(1);
    // Existing traffic still crosses the same publication path after the skipped start.
    await sink.report(
      token,
      new AgentMessageCompleted({ content: text_message_content('ordinary turn') }),
    );
    await sink.report(
      null,
      new BackgroundTaskStarted({ task_id: 'legacy', task_description: 'Legacy task' }),
    );
    expect(store.pending().map((row) => row_envelope(store.sender_id, row).payload.method)).toEqual(
      ['turn_ended', 'agent_message_completed', 'background_task_started'],
    );
    // A pending network receipt releases source custody when native shutdown aborts it.
    presence.accepted(true);
    const shutdown = new AbortController();
    const offline = sink.start_agent_turn!('During shutdown', shutdown.signal);
    await new Promise<void>((resolve) => setImmediate(resolve));
    shutdown.abort();
    expect(await offline).toBeNull();
    expect(child.active_turn).toBeNull();
    await sources.finish();
  } finally {
    await store.stop(stop_options());
    await store.settled();
    await rm(root, { recursive: true, force: true });
  }
});
