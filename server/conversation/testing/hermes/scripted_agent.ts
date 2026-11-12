/** One local ACP peer. Only fixture state and trace files are read or written. */
import { appendFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { Readable, Writable } from 'node:stream';
import { ndJsonStream, RequestError } from '@agentclientprotocol/sdk/unstable';
import type { AnyMessage } from '@agentclientprotocol/sdk/unstable';
import type * as Rpc from '../../../node_modules/@agentclientprotocol/sdk/dist/core/jsonrpc.js';

const root = import.meta.resolve('@agentclientprotocol/sdk/unstable');
const rpc = (await import(new URL('../../core/jsonrpc.js', root).href)) as typeof Rpc;
const trace = process.env.UNIT16_TRACE!;
const statePath = process.env.UNIT16_STATE!;
const controlPath = process.env.UNIT16_CONTROL!;
const scenario = process.env.UNIT16_CASE!;
if (!trace || !statePath || !controlPath || !scenario)
  throw new Error('Synthetic fixture configuration required');
const session = `scripted-${scenario}`;
const observedShape = scenario === 'plain' || scenario === 'resume';
type State = { model: string; load: boolean; prompts: number };
const all: Record<string, State> = existsSync(statePath)
  ? JSON.parse(readFileSync(statePath, 'utf8'))
  : {};
let state = all[session] ?? { model: 'synthetic-model', load: true, prompts: 0 };
const save = () => {
  all[session] = state;
  writeFileSync(statePath, JSON.stringify(all));
};
const log = (value: unknown) => appendFileSync(trace, JSON.stringify(value) + '\n');
log({
  kind: 'launch',
  scenario,
  session,
  argv: process.argv,
  cwd: process.cwd(),
  environment: { ...process.env },
});
process.once('exit', () => log({ kind: 'exit', scenario }));
process.stdin.once('end', () => process.exit(0));
let controlSerial = existsSync(controlPath)
  ? JSON.parse(readFileSync(controlPath, 'utf8')).serial
  : 0;
const output = new WritableStream<Uint8Array>({
  async write(bytes) {
    // Split every encoded frame inside its JSON token, not just between lines.
    const cut = Math.min(13, bytes.length);
    for (const piece of [bytes.slice(0, cut), bytes.slice(cut)]) {
      await new Promise<void>((resolve, reject) =>
        process.stdout.write(piece, (error) => (error ? reject(error) : resolve())),
      );
    }
  },
});
const framed = ndJsonStream(output, Readable.toWeb(process.stdin) as ReadableStream<Uint8Array>);
const input = framed.readable.getReader();
const stream = {
  writable: framed.writable,
  readable: new ReadableStream<AnyMessage>({
    async pull(controller) {
      const item = await input.read();
      if (item.done) {
        controller.close();
        return;
      }
      log({ kind: 'in', scenario, message: item.value });
      controller.enqueue(item.value);
    },
    cancel: (reason) => input.cancel(reason),
  }),
};
const option = () => [
  {
    type: 'select',
    id: 'effort',
    name: 'Thought level',
    category: 'thought_level',
    currentValue: 'low',
    options: [
      { value: 'low', name: 'Low' },
      { value: 'high', name: 'High' },
    ],
  },
];
const metadata = () => ({ panels: { currentModelId: state.model } });
let cx: Rpc.ConnectionContext;
let active: { responder: Rpc.RequestResponder; params: Record<string, any> } | null = null;
const notify = (update: Record<string, unknown>) =>
  cx.sendNotification('session/update', { sessionId: session, update });
const chunk = (text: string, id = 'message-1') =>
  notify({ sessionUpdate: 'agent_message_chunk', messageId: id, content: { type: 'text', text } });
async function finish(text = 'finished', stopReason = 'end_turn') {
  if (!active) throw new Error('No synthetic active prompt');
  const pending = active;
  active = null;
  if (text) await chunk(text);
  // Cost-free occupancy is context state, never this turn's expenditure.
  if (observedShape)
    await notify({
      sessionUpdate: 'usage_update',
      used: scenario === 'plain' ? 1571 : state.prompts === 1 ? 1578 : 1715,
      size: 272000,
    });
  const [inputTokens, outputTokens, cachedReadTokens]: [number, number, number] =
    scenario === 'plain'
      ? [1232, 10, 0]
      : scenario === 'resume'
        ? state.prompts === 1
          ? [1243, 7, 0]
          : [1276, 7, 1024]
        : [12, 7, 3];
  await pending.responder.respond({
    stopReason,
    usage: {
      inputTokens,
      outputTokens,
      totalTokens: inputTokens + outputTokens,
      cachedReadTokens,
      // Native thought counts were zero. This proves shape acceptance only.
      ...(observedShape ? { thoughtTokens: 0 } : {}),
    },
  });
}
async function commands() {
  await notify({
    sessionUpdate: 'available_commands_update',
    availableCommands: [
      { name: 'compress', description: 'Compress context' },
      { name: 'native', description: 'Native command', input: { hint: 'argument' } },
    ],
  });
  if (observedShape)
    await notify({
      sessionUpdate: 'usage_update',
      used: state.prompts === 0 ? 0 : 139,
      size: 272000,
    });
}
async function prompt(responder: Rpc.RequestResponder, params: Record<string, any>) {
  if (active) throw new Error('Prompt overlapped an active turn');
  active = { responder, params };
  state.prompts++;
  save();
  const text = params.prompt
    .filter((part: any) => part.type === 'text')
    .map((part: any) => part.text)
    .join('\n');
  const name =
    /\[CASE:([^\]]+)\]/.exec(text)?.[1] ??
    (text === '/compress' ? 'compact' : text.startsWith('/native') ? 'native' : 'plain');
  log({ kind: 'prompt', scenario, name, text });
  // Representative ordinary metadata: native evidence captured its type only.
  if (observedShape && state.prompts === 1)
    await notify({ sessionUpdate: 'session_info_update', title: 'Synthetic ordinary session' });
  if (name === 'hold' || name === 'steer-base') return;
  if (name === 'compact') {
    await notify({
      sessionUpdate: 'session_info_update',
      _meta: { hermes: { sessionProvenance: { reason: 'compression' } } },
    });
    return;
  }
  if (name === 'plain') {
    await notify({
      sessionUpdate: 'agent_thought_chunk',
      content: { type: 'text', text: 'PRIVATE_THOUGHT_MUST_NEVER_ESCAPE' },
    });
    for (const part of ['one ', 'two ', 'three']) await chunk(part);
    await chunk('next message', 'message-2');
    await finish('');
  } else if (name === 'tools') {
    await notify({
      sessionUpdate: 'tool_call',
      toolCallId: 'tool-1',
      title: 'Synthetic read',
      kind: 'read',
      status: 'in_progress',
      content: [],
    });
    await notify({
      sessionUpdate: 'tool_call_update',
      toolCallId: 'tool-1',
      status: 'in_progress',
      content: [{ type: 'content', content: { type: 'text', text: 'synthetic progress' } }],
    });
    await notify({
      sessionUpdate: 'tool_call_update',
      toolCallId: 'tool-1',
      status: 'completed',
      content: [{ type: 'content', content: { type: 'text', text: 'synthetic result' } }],
    });
    await notify({
      sessionUpdate: 'plan',
      entries: [{ content: 'Synthetic plan', priority: 'high', status: 'completed' }],
    });
    await notify({
      sessionUpdate: 'usage_update',
      used: 100,
      size: 1000,
      cost: { amount: 0.25, currency: 'USD' },
    });
    await notify({
      sessionUpdate: 'session_info_update',
      _meta: { hermes: { sessionProvenance: { reason: 'compression' } } },
    });
    await finish();
  } else if (name === 'permission') {
    const answer = await cx.sendRequest('session/request_permission', {
      sessionId: session,
      toolCall: { toolCallId: 'permission-tool', title: 'Synthetic permission' },
      options: [
        { optionId: 'allow', name: 'Allow once', kind: 'allow_once' },
        { optionId: 'deny', name: 'Deny once', kind: 'reject_once' },
      ],
    });
    log({ kind: 'permission-answer', scenario, answer });
    if (active) await finish('permission answered');
  } else if (name === 'media') {
    await chunk('before image');
    await notify({
      sessionUpdate: 'agent_message_chunk',
      messageId: 'message-1',
      content: { type: 'image', mimeType: 'image/png', data: 'c3ludGhldGljLWltYWdl' },
    });
    await chunk('after image');
    await finish('');
  } else if (name === 'malformed') {
    await notify({ sessionUpdate: 'agent_message_chunk' });
    await notify({ sessionUpdate: 'available_commands_update', availableCommands: null });
    await notify({
      sessionUpdate: 'available_commands_update',
      availableCommands: [
        { name: 'partial-replacement', description: 'must not replace catalog' },
        { name: 7, description: 'invalid name' },
      ],
    });
    await notify({
      sessionUpdate: 'plan',
      entries: [
        { content: 'partial plan must not escape', priority: 'high', status: 'pending' },
        { content: 'invalid plan', priority: 'high', status: 'unknown' },
      ],
    });
    await notify({
      sessionUpdate: 'tool_call_update',
      toolCallId: 'unknown-status-tool',
      status: 'unknown-status',
      content: [
        { type: 'content', content: { type: 'text', text: 'unknown status must not escape' } },
      ],
    });
    await finish('valid after malformed');
  } else if (name === 'provider-error') await finish('Provider error: synthetic model unavailable');
  else if (name === 'rpc-error') {
    active = null;
    await responder.respondWithError(new RequestError(-32000, 'synthetic provider RPC failure'));
  } else if (name === 'crash') {
    await new Promise<void>((resolve, reject) =>
      process.stderr.write('X'.repeat(9000) + '\nsynthetic crash\n', (error) =>
        error ? reject(error) : resolve(),
      ),
    );
    // Let both adapters consume the complete older and final lines before exit.
    await new Promise((resolve) => setTimeout(resolve, 20));
    process.exit(7);
  } else if (name === 'refuse-load') {
    state.load = false;
    save();
    await finish('next load refused');
  } else if (name === 'resume') await finish('new response after load');
  else await finish(name);
}
const connection = rpc.Connection.builder()
  .withHandler({
    async handleMessage(message, context) {
      cx = context;
      if (message.kind === 'notification') {
        if (message.method === 'session/cancel') {
          log({ kind: 'cancel', scenario });
          if (active) {
            await new Promise((resolve) => setTimeout(resolve, 25));
            await finish('', 'cancelled');
          }
        }
        return rpc.Handled.yes();
      }
      const params = message.params as Record<string, any>;
      if (message.method === 'initialize')
        await message.responder.respond({
          protocolVersion: 1,
          agentCapabilities: {
            loadSession: true,
            promptCapabilities: { image: true, audio: true, embeddedContext: true },
          },
          authMethods: [],
        });
      else if (message.method === 'session/new') {
        save();
        await commands();
        await message.responder.respond({
          sessionId: session,
          ...(observedShape ? {} : { configOptions: option() }),
          _meta: metadata(),
        });
      } else if (message.method === 'session/load') {
        if (!all[params.sessionId] || !state.load)
          await message.responder.respondWithError(
            new RequestError(-32000, 'synthetic session load refused'),
          );
        else {
          if (observedShape)
            await notify({
              sessionUpdate: 'user_message_chunk',
              messageId: 'replay-user',
              content: { type: 'text', text: 'REPLAY_MUST_NOT_APPEAR user' },
            });
          await chunk('REPLAY_MUST_NOT_APPEAR');
          await commands();
          await message.responder.respond({
            ...(observedShape ? {} : { configOptions: option() }),
            _meta: metadata(),
          });
        }
      } else if (message.method === 'session/set_mode') await message.responder.respond({});
      else if (message.method === 'session/set_model') {
        if (params.modelId === 'synthetic-refused') {
          await message.responder.respondWithError(
            new RequestError(-32000, 'synthetic model change refused'),
          );
        } else {
          state.model = params.modelId;
          save();
          await message.responder.respond({});
        }
      } else if (message.method === 'session/set_config_option')
        await message.responder.respond({ configOptions: option() });
      else if (message.method === 'session/prompt') {
        void prompt(message.responder, params).catch((error) => {
          log({ kind: 'fixture-error', scenario, error: String(error) });
          process.exit(9);
        });
      } else if (message.method === '_panels/steer') {
        if (params.text.includes('steer-lost-reply')) {
          log({ kind: 'lost-steer-reply', scenario });
          process.exit(0);
        }
        const accepted = !params.text.includes('steer-refused');
        const uncertain = params.text.includes('steer-uncertain');
        await message.responder.respond({ accepted, turnToken: uncertain ? {} : params.turnToken });
        if (accepted && !uncertain && active) await finish('steered response');
      } else await message.responder.respondWithError(RequestError.methodNotFound(message.method));
      return rpc.Handled.yes();
    },
  })
  .connect(stream);
const controls = setInterval(() => {
  if (!existsSync(controlPath)) return;
  const control = JSON.parse(readFileSync(controlPath, 'utf8'));
  if (control.serial <= controlSerial || control.scenario !== scenario) return;
  if (!active) return;
  controlSerial = control.serial;
  if (control.command === 'finish')
    void finish(control.text).catch((error) => {
      log({ kind: 'fixture-error', scenario, error: String(error) });
      process.exit(9);
    });
}, 5);
void connection.closed.then(() => {
  clearInterval(controls);
  process.exit(0);
});
