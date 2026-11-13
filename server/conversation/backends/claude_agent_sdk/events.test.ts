/** Mapper risks are below the SDK/core connection already covered by the direct driver. */
import { expect, it } from 'vitest';
import * as B from '../contracts.ts';
import { ClaudeEvents, validate_message } from './events.ts';
import { TurnInFlight } from './turn.ts';
import { normalized_failure_summary } from './failure_summary.ts';

function fixture() {
  const events: { turn: B.TurnToken | null; event: B.BackendEventValue }[] = [];
  const mapper = new ClaudeEvents({
    report: async (turn, event) => {
      events.push({ turn, event });
    },
  });
  const turn = new TurnInFlight({ conversation_id: 'synthetic', turn_number: 1 });
  return { mapper, turn, events };
}
it('keeps parent text order around tools while dropping thought text and nested speech', async () => {
  const { mapper, turn, events } = fixture();
  await mapper.assistant_message(turn, {
    type: 'assistant',
    parent_tool_use_id: null,
    message: {
      model: 'model',
      content: [
        { type: 'text', text: 'before' },
        {
          type: 'tool_use',
          id: 'plan',
          name: 'TodoWrite',
          input: { todos: [{ content: 'Step', status: 'pending' }] },
        },
        { type: 'thinking', thinking: 'never store this', signature: 'sig' },
        { type: 'text', text: 'after' },
      ],
    },
  });
  await mapper.assistant_message(turn, {
    type: 'assistant',
    parent_tool_use_id: 'plan',
    message: { model: 'model', content: [{ type: 'text', text: 'nested' }] },
  });
  expect(events.map(({ event }) => event.constructor.name)).toEqual([
    'AgentMessageCompleted',
    'PlanUpdated',
    'ToolCallStarted',
    'ModelThinkingHappened',
    'AgentMessageCompleted',
  ]);
  expect(JSON.stringify(events)).not.toContain('never store this');
  expect(JSON.stringify(events)).not.toContain('nested');
  expect(events[0].event).toMatchObject({ content: [{ text: 'before' }] });
  expect(events[2].event).toMatchObject({
    detail: '{"todos": [{"content": "Step", "status": "pending"}]}',
  });
  expect(events[4].event).toMatchObject({ content: [{ text: 'after' }] });
});
it('attributes streamed tool text and preserves a late parent message through unknown block additions', async () => {
  const { mapper, turn, events } = fixture();
  await mapper.stream_event(turn, {
    type: 'stream_event',
    parent_tool_use_id: 'owned-tool',
    event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'progress' } },
  });
  await mapper.assistant_message(
    turn,
    {
      type: 'assistant',
      message: {
        model: 'model',
        content: [
          { type: 'text', text: 'late' },
          { type: 'future-addition', metadata: {} },
        ],
      },
    },
    true,
  );
  expect(events[0].event).toEqual(
    new B.ToolCallProgress({ tool_call_id: 'owned-tool', detail: 'progress' }),
  );
  expect(events[1].event).toMatchObject({ content: [{ text: 'late' }] });
});
it('uses the authoritative background set and terminal patch instead of task notifications alone', async () => {
  const { mapper, events } = fixture();
  await mapper.take_background({ type: 'system', subtype: 'task_started', task_id: 'not-live' });
  expect(events).toEqual([]);
  await mapper.take_background({
    type: 'system',
    subtype: 'background_tasks_changed',
    tasks: [
      { task_id: 'one', description: 'Work' },
      { task_id: 'two', description: 'Other' },
    ],
  });
  await mapper.take_background({
    type: 'system',
    subtype: 'task_updated',
    task_id: 'one',
    patch: { status: 'killed' },
  });
  await mapper.take_background({
    type: 'system',
    subtype: 'background_tasks_changed',
    tasks: [{ task_id: 'two', description: 'Other', ambient: true }],
  });
  expect(events.map(({ event }) => event)).toEqual([
    new B.BackgroundTaskStarted({ task_id: 'one', task_description: 'Work' }),
    new B.BackgroundTaskStarted({ task_id: 'two', task_description: 'Other' }),
    new B.BackgroundTaskEnded({ task_id: 'one', task_status: 'killed' }),
    new B.BackgroundTaskEnded({ task_id: 'two', task_status: 'ambient' }),
  ]);
  expect(events.every(({ turn }) => turn === null)).toBe(true);
});
it('rejects missing consumed fields before native facts can reach the sink', () => {
  expect(() =>
    validate_message({
      type: 'assistant',
      session_id: 'substitute',
      message: { model: 'model', content: [{ type: 'text' }] },
    }),
  ).toThrow("Missing required field in assistant message: 'text'");
  expect(() => validate_message({ type: 'assistant', message: { content: [] } })).toThrow(
    "Missing required field in assistant message: 'model'",
  );
  expect(() =>
    validate_message({
      type: 'assistant',
      message: { model: 'model', content: [{ type: 'tool_use', name: 'Read', input: {} }] },
    }),
  ).toThrow("Missing required field in assistant message: 'id'");
});
it('redacts native failure secrets and preserves the authentication policy', () => {
  expect(normalized_failure_summary({ api_key: 'private', Authorization: 'Bearer secret' })).toBe(
    '{"api_key":[redacted],"Authorization":[redacted]}',
  );
  expect(normalized_failure_summary('untrusted provider text', 401)).toBe(
    'authentication was rejected (HTTP 401)',
  );
  expect(Array.from(normalized_failure_summary('😀'.repeat(241))).length).toBe(240);
});
