/** The shared ACP child against a scripted ACP peer: each flavor hook and each base rule at its point. */
import { afterEach, expect, test, vi } from 'vitest';
import { RequestError } from '@agentclientprotocol/sdk/unstable';
import type { AnyMessage } from '@agentclientprotocol/sdk/unstable';
import { parse_backend_instance_id } from '../core/identity.ts';
import { ChildProcesses, OneShotChildren } from '../../lifecycle/owned_processes.ts';
import {
  BackendSpawnFailed,
  NeedsRebind,
  PromptWriteFailed,
  PromptWriteRefused,
  ToolCallFinished,
  TurnEnded,
} from '../../conversation/backends/contracts.ts';
import type { BackendEventValue } from '../../conversation/backends/contracts.ts';
import {
  ConversationAccess,
  PromptDeliveryMode,
  ResolvedConversationStart,
} from '../../conversation/contracts.ts';
import { text_message_content } from '../../conversation/message_content.ts';
import type { ConversationMessageFiles } from '../../conversation/message_files.ts';
import { uncontained_worker_supervision } from '../testing/uncontained_supervision.ts';
import { AcpBackendChild } from './child.ts';
import { AcpClient, AcpLauncher } from './client.ts';
import { OPEN_TOOL_CALL_SILENCE_LIMITS } from './configuration.ts';
import type { AcpFlavor } from './flavor.ts';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
type Answer = { result: unknown } | { error: { code: number; message: string } } | null;
const SESSION = 'scripted-session';
const MODEL_OPTION = (current: string, values: readonly string[]) => ({
  id: 'model',
  name: 'Model',
  category: 'model',
  type: 'select',
  currentValue: current,
  options: values.map((value) => ({ value, name: value })),
});
const EFFORT_OPTION = (current: string, values: readonly string[]) => ({
  id: 'thought_level',
  name: 'Thinking Level',
  category: 'thought_level',
  type: 'select',
  currentValue: current,
  options: values.map((value) => ({ value, name: value })),
});
const NEW_SESSION = {
  sessionId: SESSION,
  configOptions: [MODEL_OPTION('family', ['family'])],
};
/**
 * A scripted ACP peer over in-memory streams. `answer` replies to each request; null holds the
 * reply until the test sends it with `reply`. `hold_prompt_write` keeps each prompt's write in
 * the stream until it settles.
 */
function scripted(
  flavor_hooks: Partial<AcpFlavor>,
  answer: (method: string, params: unknown) => Answer,
  options: { hold_prompt_write?: Promise<void> } = {},
) {
  let input!: ReadableStreamDefaultController<AnyMessage>;
  const writes: AnyMessage[] = [];
  const events: BackendEventValue[] = [];
  const ended = Promise.withResolvers<TurnEnded>();
  const flavor: AcpFlavor = {
    label: 'Scripted',
    launch: () => ({ argv: ['scripted'], environment_overrides: [] }),
    ...flavor_hooks,
  };
  vi.spyOn(AcpLauncher.prototype, 'start').mockImplementation(async (_spawn, _start, handlers) => {
    return new AcpClient(
      {
        readable: new ReadableStream<AnyMessage>({
          start(controller) {
            input = controller;
          },
        }),
        writable: new WritableStream<AnyMessage>({
          async write(message) {
            if ('method' in message && message.method === 'session/prompt')
              await options.hold_prompt_write;
            writes.push(message);
            if (!('method' in message) || !('id' in message)) return;
            const reply = answer(message.method, 'params' in message ? message.params : undefined);
            if (reply !== null)
              queueMicrotask(() => input.enqueue({ jsonrpc: '2.0', id: message.id, ...reply }));
          },
        }),
      },
      handlers,
    );
  });
  vi.spyOn(AcpLauncher.prototype, 'stop').mockResolvedValue();
  vi.spyOn(AcpLauncher.prototype, 'standard_error_tail').mockReturnValue(null);
  const resolved = new ResolvedConversationStart({
    conversation_id: 'scripted',
    backend_key: parse_backend_instance_id('scripted'),
    model: 'family',
    reasoning_effort: null,
    role_materials: null,
    workspace_folder: '/scripted',
    access: ConversationAccess.full,
  });
  const child_processes = new ChildProcesses();
  const child = new AcpBackendChild(
    flavor,
    {
      child_processes,
      worker_supervision: uncontained_worker_supervision(new OneShotChildren(child_processes)),
    },
    {
      resolved_start: resolved,
      event_sink: {
        async report(_turn, event) {
          events.push(event);
          if (event instanceof TurnEnded) ended.resolve(event);
        },
      },
      message_files: {} as ConversationMessageFiles,
    },
  );
  const requests = (method: string) =>
    writes.filter((message) => 'method' in message && message.method === method);
  return {
    child,
    resolved,
    writes,
    events,
    ended: ended.promise,
    requests,
    /** Answer the newest request of that method. */
    reply(method: string, outcome: NonNullable<Answer>) {
      const request = requests(method).at(-1);
      if (!request || !('id' in request)) throw new Error(`no ${method} request`);
      input.enqueue({ jsonrpc: '2.0', id: request.id, ...outcome });
    },
    update(update: Record<string, unknown>) {
      input.enqueue({
        jsonrpc: '2.0',
        method: 'session/update',
        params: { sessionId: SESSION, update },
      });
    },
    /** The agent asks permission to run the tool call `tool_call_id`. Its ask id is `<id>:<n>`. */
    ask(tool_call_id: string) {
      input.enqueue({
        jsonrpc: '2.0',
        id: `ask-${tool_call_id}`,
        method: 'session/request_permission',
        params: {
          sessionId: SESSION,
          toolCall: { toolCallId: tool_call_id, title: `Edit ${tool_call_id}` },
          options: [
            { optionId: 'allow', name: 'Allow', kind: 'allow_once' },
            { optionId: 'deny', name: 'Deny', kind: 'reject_once' },
          ],
        },
      });
    },
    /** The agent's process ends: its output closes. */
    end() {
      input.close();
    },
    /** Reading the agent's output fails, while the agent itself may still run. */
    break_output() {
      input.error(new Error('the output read failed'));
    },
    prompt(
      text = 'hello',
      change: { model_change: string | null; reasoning_effort_change: string | null } = {
        model_change: null,
        reasoning_effort_change: null,
      },
    ) {
      const content = text_message_content(text);
      return child.write_prompt({ conversation_id: 'scripted', turn_number: 1 }, content, {
        sender_label: 'Owner',
        sender_content: content,
        mode: PromptDeliveryMode.queue,
        ...change,
      });
    },
  };
}
/** The replies an ordinary session gives. The prompt reply waits for the test. */
const ordinary = (method: string): Answer =>
  method === 'session/prompt'
    ? null
    : {
        result:
          method === 'initialize'
            ? { protocolVersion: 1 }
            : method === 'session/new'
              ? NEW_SESSION
              : { configOptions: NEW_SESSION.configOptions },
      };
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));
const turn_ended = (peer: ReturnType<typeof scripted>) =>
  peer.events.some((event) => event instanceof TurnEnded);

// Risk: an agent that ended while idle fails the next prompt instead of being replaced. The core
// rebinds only on NeedsRebind with failed_child_recovery (as Codex and Claude raise it).
test('a child whose agent ended while idle asks the core for a fresh child', async () => {
  const peer = scripted({}, ordinary);
  await peer.child.start(peer.resolved, { vendor_session_cursor: null });
  peer.end();
  await tick();
  const refused = await peer.prompt().catch((error: unknown) => error);
  expect(refused).toBeInstanceOf(NeedsRebind);
  expect((refused as NeedsRebind).failed_child_recovery).toBe(true);
  expect(peer.requests('session/prompt')).toHaveLength(0);
  await peer.child.stop();
});

// Risk: the wire fails for a reason other than the agent dying while the prompt's write is in the
// stream. The agent may already have the prompt, so a rebind would send it twice.
test('a prompt whose write began when the wire failed is not handed to a fresh child', async () => {
  const held = Promise.withResolvers<void>();
  const peer = scripted({}, ordinary, { hold_prompt_write: held.promise });
  await peer.child.start(peer.resolved, { vendor_session_cursor: null });
  const prompt = peer.prompt().catch((error: unknown) => error);
  await tick();
  peer.break_output();
  const failure = await prompt;
  expect(failure).toBeInstanceOf(PromptWriteFailed);
  expect(failure).not.toBeInstanceOf(NeedsRebind);
  held.resolve();
  await peer.child.stop();
});

// Risk: a model's effort is set against the previous model's options, or before the model.
test('start sets the model, then the effort the new model offers', async () => {
  const peer = scripted({}, (method) => {
    if (method === 'session/new')
      return {
        result: { sessionId: SESSION, configOptions: [MODEL_OPTION('plain', ['plain', 'deep'])] },
      };
    if (method === 'session/set_config_option')
      return {
        result: {
          configOptions: [
            MODEL_OPTION('deep', ['plain', 'deep']),
            EFFORT_OPTION('medium', ['medium', 'xhigh']),
          ],
        },
      };
    return ordinary(method);
  });
  const start = new ResolvedConversationStart({
    ...peer.resolved,
    model: 'deep',
    reasoning_effort: 'xhigh',
  });
  await peer.child.start(start, { vendor_session_cursor: null });
  expect(
    peer
      .requests('session/set_config_option')
      .map((message) => 'params' in message && message.params),
  ).toEqual([
    { configId: 'model', sessionId: SESSION, value: 'deep' },
    { configId: 'thought_level', sessionId: SESSION, value: 'xhigh' },
  ]);
  await peer.child.stop();
});

// Risk: the agent takes the new model and refuses its effort. The prompt is refused while the
// child stays on the new model and the record still shows the old one.
test('a model taken before its effort was refused is put back before the prompt is refused', async () => {
  const options = (model: string) => [
    MODEL_OPTION(model, ['plain', 'deep']),
    EFFORT_OPTION('medium', ['medium']),
  ];
  const peer = scripted({}, (method, params) => {
    if (method === 'session/new')
      return { result: { sessionId: SESSION, configOptions: options('plain') } };
    if (method !== 'session/set_config_option') return ordinary(method);
    const { configId, value } = params as { configId: string; value: string };
    return configId === 'thought_level'
      ? { error: { code: -32602, message: `${value} is not offered` } }
      : { result: { configOptions: options(value) } };
  });
  // The session already reports `plain`, so the start sets nothing.
  await peer.child.start(new ResolvedConversationStart({ ...peer.resolved, model: 'plain' }), {
    vendor_session_cursor: null,
  });
  const refused = await peer
    .prompt('hello', { model_change: 'deep', reasoning_effort_change: 'xhigh' })
    .catch((error: unknown) => error);
  expect(refused).toBeInstanceOf(PromptWriteRefused);
  expect(
    peer.requests('session/set_config_option').map((message) => {
      const { configId, value } = (message as { params: { configId: string; value: string } })
        .params;
      return [configId, value];
    }),
  ).toEqual([
    ['model', 'deep'],
    ['thought_level', 'xhigh'],
    ['model', 'plain'],
  ]);
  expect(peer.requests('session/prompt')).toHaveLength(0);
  await peer.child.stop();
});

// Risk: the model cannot be put back either. A rebind would start a fresh agent on the refused
// selection, whose start then fails as a lost session and hides the agent's refusal.
test('a refused effort whose model cannot be put back refuses with the agent’s text and retires the child', async () => {
  const options = (model: string) => [
    MODEL_OPTION(model, ['plain', 'deep']),
    EFFORT_OPTION('medium', ['medium']),
  ];
  const peer = scripted({}, (method, params) => {
    if (method === 'session/new')
      return { result: { sessionId: SESSION, configOptions: options('plain') } };
    if (method !== 'session/set_config_option') return ordinary(method);
    const { configId, value } = params as { configId: string; value: string };
    if (configId === 'thought_level')
      return { error: { code: -32602, message: `${value} is not offered` } };
    return value === 'deep'
      ? { result: { configOptions: options(value) } }
      : { error: { code: -32602, message: 'the model cannot change now' } };
  });
  await peer.child.start(new ResolvedConversationStart({ ...peer.resolved, model: 'plain' }), {
    vendor_session_cursor: null,
  });
  const refused = await peer
    .prompt('hello', { model_change: 'deep', reasoning_effort_change: 'xhigh' })
    .catch((error: unknown) => error);
  expect(refused).toBeInstanceOf(PromptWriteRefused);
  expect((refused as Error).message).toBe('xhigh is not offered');
  // The child no longer matches its record, so the next prompt goes to a fresh child.
  const next = await peer.prompt().catch((error: unknown) => error);
  expect(next).toBeInstanceOf(NeedsRebind);
  expect((next as NeedsRebind).failed_child_recovery).toBe(true);
  expect(peer.requests('session/prompt')).toHaveLength(0);
  await peer.child.stop();
});

// Hook requires_sign_in. Risk: a signed-out agent reads as a lost session (retried forever) or
// as an unexplained failure.
test('a signed-out agent fails its start and its prompt with the flavor sign-in text', async () => {
  const requires_sign_in = (error: unknown) =>
    error instanceof RequestError && error.code === -32000 ? 'Scripted is not signed in.' : null;
  const signed_out = { error: { code: -32000, message: 'Authentication required' } };
  const at_start = scripted({ requires_sign_in }, (method) =>
    method === 'session/new' ? signed_out : ordinary(method),
  );
  const failure = await at_start.child
    .start(at_start.resolved, { vendor_session_cursor: null })
    .catch((error: unknown) => error);
  expect(failure).toBeInstanceOf(BackendSpawnFailed);
  expect((failure as Error).message).toBe('Scripted is not signed in.');
  vi.restoreAllMocks();
  const mid_session = scripted({ requires_sign_in }, ordinary);
  await mid_session.child.start(mid_session.resolved, { vendor_session_cursor: null });
  await mid_session.prompt();
  mid_session.reply('session/prompt', signed_out);
  const ended = await mid_session.ended;
  expect([ended.ending, ended.error_summary]).toEqual(['failed', 'Scripted is not signed in.']);
  await mid_session.child.stop();
});

// Hook watch_prompt_outcome. Risk: an agent that reports its API error as text plus end_turn
// shows the turn as completed. The watch must be taken before the prompt reaches the agent, or a
// fast failure is already in its baseline.
test('an end_turn the outcome watch reads as failed ends the turn failed and reusable', async () => {
  const order: string[] = [];
  let peer!: ReturnType<typeof scripted>;
  peer = scripted(
    {
      watch_prompt_outcome(session_id) {
        order.push(`watch ${session_id} after ${peer.requests('session/prompt').length} prompts`);
        return { failure: async () => 'quota reached' };
      },
    },
    ordinary,
  );
  await peer.child.start(peer.resolved, { vendor_session_cursor: null });
  await peer.prompt();
  peer.reply('session/prompt', { result: { stopReason: 'end_turn' } });
  const ended = await peer.ended;
  expect([ended.ending, ended.error_summary, ended.child_disposition]).toEqual([
    'failed',
    'quota reached',
    'reusable',
  ]);
  expect(order).toEqual([`watch ${SESSION} after 0 prompts`]);
  await peer.child.stop();
});

// Hook prompt_inactivity_timeout_ms. Risks: a hung agent holds the conversation forever; a person
// answering slowly, or the tool they just allowed, is killed for silence; a tool call the agent
// never closes turns the limit off; the hung child takes the next prompt.
test('a silent agent fails its turn and its child; an allowed tool call holds the clock for a bounded time', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const peer = scripted({ prompt_inactivity_timeout_ms: 1000 }, ordinary);
  await peer.child.start(peer.resolved, { vendor_session_cursor: null });
  await peer.prompt();
  peer.ask('notes');
  await vi.advanceTimersByTimeAsync(5000);
  expect(turn_ended(peer)).toBe(false);
  expect(await peer.child.answer_permission_ask('notes:1', 'allow')).toBe('applied');
  // The agent now runs the allowed tool and never reports it finished.
  await vi.advanceTimersByTimeAsync(OPEN_TOOL_CALL_SILENCE_LIMITS * 1000);
  expect(turn_ended(peer)).toBe(false);
  await vi.advanceTimersByTimeAsync(1000);
  expect(turn_ended(peer)).toBe(true);
  const ended = await peer.ended;
  expect([ended.ending, ended.child_disposition]).toEqual(['failed', 'broken']);
  expect(ended.error_summary).toContain(
    `sent nothing for ${OPEN_TOOL_CALL_SILENCE_LIMITS + 1} seconds`,
  );
  expect(peer.requests('$/cancel_request')).toHaveLength(1);
  await expect(peer.prompt()).rejects.toBeInstanceOf(NeedsRebind);
  await peer.child.stop();
});

// Risk: a turn the core ended without its reply (a cancel the agent never answered) keeps its
// silence clock, which then fails the next turn on the same child and breaks its wire.
test('a turn ended without its reply cannot fail the next turn on the same child', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const peer = scripted({ prompt_inactivity_timeout_ms: 1000 }, ordinary);
  await peer.child.start(peer.resolved, { vendor_session_cursor: null });
  await peer.prompt('hangs');
  peer.update({ sessionUpdate: 'tool_call', toolCallId: 'hung', title: 'Run', status: 'pending' });
  await vi.advanceTimersByTimeAsync(500);
  await peer.prompt('next');
  // The abandoned turn's tool call will never be finished by the agent.
  expect(peer.events.filter((event) => event instanceof ToolCallFinished)).toMatchObject([
    { tool_call_id: 'hung', tool_call_status: 'failed' },
  ]);
  // The first turn's limit has passed, the second's has not.
  await vi.advanceTimersByTimeAsync(700);
  expect(turn_ended(peer)).toBe(false);
  peer.reply('session/prompt', { result: { stopReason: 'end_turn' } });
  const ended = await peer.ended;
  expect([ended.ending, ended.child_disposition]).toEqual(['completed', 'reusable']);
  expect(peer.requests('$/cancel_request')).toHaveLength(0);
  await peer.child.stop();
});

// Risk: a tool call the agent leaves open at end_turn (agy 1.5.10's ask_question stays pending)
// shows as running forever, because Panels never ends a tool call by itself.
test('a tool call still open when its turn ends is finished before the turn ends', async () => {
  const peer = scripted({}, ordinary);
  await peer.child.start(peer.resolved, { vendor_session_cursor: null });
  await peer.prompt();
  peer.update({
    sessionUpdate: 'tool_call',
    toolCallId: 'ask',
    title: 'Run ask_question',
    kind: 'other',
    status: 'in_progress',
  });
  peer.update({ sessionUpdate: 'tool_call_update', toolCallId: 'ask', status: 'pending' });
  peer.reply('session/prompt', { result: { stopReason: 'end_turn' } });
  await peer.ended;
  const finished = peer.events.findIndex((event) => event instanceof ToolCallFinished);
  expect(peer.events[finished]).toMatchObject({
    tool_call_id: 'ask',
    tool_call_status: 'completed',
  });
  expect(finished).toBe(peer.events.findIndex((event) => event instanceof TurnEnded) - 1);
  await peer.child.stop();
});

// Risks: a tool call that arrives already finished after its ask was allowed stays open, holding
// the silence clock and finishing a second time at turn end; a refused ask holds the clock for a
// tool that never runs and finishes a tool call that was never shown.
test('a tool call is open only while the agent runs it', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const peer = scripted({ prompt_inactivity_timeout_ms: 1000 }, ordinary);
  await peer.child.start(peer.resolved, { vendor_session_cursor: null });
  await peer.prompt();
  peer.ask('allowed');
  await tick();
  expect(await peer.child.answer_permission_ask('allowed:1', 'allow')).toBe('applied');
  peer.update({
    sessionUpdate: 'tool_call',
    toolCallId: 'allowed',
    title: 'Edit allowed',
    status: 'completed',
  });
  peer.ask('denied');
  await tick();
  expect(await peer.child.answer_permission_ask('denied:2', 'deny')).toBe('applied');
  // Nothing is open, so one limit of silence ends the turn.
  await vi.advanceTimersByTimeAsync(1000);
  const ended = await peer.ended;
  expect(ended.ending).toBe('failed');
  expect(peer.events.filter((event) => event instanceof ToolCallFinished)).toMatchObject([
    { tool_call_id: 'allowed', tool_call_status: 'completed' },
  ]);
  await peer.child.stop();
});
