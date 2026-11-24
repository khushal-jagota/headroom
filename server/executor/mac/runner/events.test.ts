/** Gap: the live bypass dropped deltas during an outage, outside the durable event sequence.
 * Exercise the real spool and existing publication door without a backend or network host.
 */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import {
  AgentMessageCompleted,
  AgentMessageDelta,
  ModelThinkingHappened,
  ToolCallProgress,
  TurnEnded,
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
