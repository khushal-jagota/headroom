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
