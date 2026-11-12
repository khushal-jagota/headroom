#!/usr/bin/env node
/** A local protocol peer. It never executes tools or contacts a provider. */
import { appendFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

if (process.argv.includes('--version') || process.argv.includes('-v')) {
  process.stdout.write('2.1.316 (Claude Code)\n');
  process.exit(0);
}
const arguments_ = process.argv.slice(2);
const part3 = process.env.UNIT18_PART3_SCRIPTED === '1';
if (part3 && Object.keys(process.env).some((name) => name.startsWith('PLAN_'))) {
  throw new Error('The manual provider rehearsal must have no Panels identity variables');
}
const option = (name: string) => {
  const inline = arguments_.find((argument) => argument.startsWith(`${name}=`));
  if (inline !== undefined) return inline.slice(name.length + 1);
  const position = arguments_.indexOf(name);
  return position < 0 ? undefined : arguments_[position + 1];
};
const session = option('--session-id') ?? option('--resume') ?? 'unit18-script-session';
const trace = process.env.UNIT18_TRACE;
if (!trace) throw new Error('The scripted CLI requires its own trace path');
function log(value: unknown) {
  appendFileSync(trace!, `${JSON.stringify(value)}\n`);
}
const launchMetadata = process.env.UNIT18_WRAPPED_FILE;
if (!launchMetadata && !part3) throw new Error('The scripted CLI requires captured launch argv');
log({
  kind: 'launch',
  pid: process.pid,
  node_executable: process.execPath,
  environment: process.env,
  args: arguments_,
  ...(launchMetadata ? JSON.parse(readFileSync(launchMetadata, 'utf8')) : {}),
});
if (process.env.PLAN_TICKET_ID) {
  const path = process.env.UNIT18_WRAPPED_FILE;
  if (!path) throw new Error('Worker fixture requires wrapped argv evidence');
  log({ kind: 'contained-launch', ...JSON.parse(readFileSync(path, 'utf8')) });
}
process.once('exit', () => log({ kind: 'exit', pid: process.pid }));
process.once('SIGTERM', () => process.exit(0));
const statePath = `${trace}.state.json`;
let state = { session, resultNumber: 0, resumeAllowed: true, memory: '' };
if (option('--resume') && existsSync(statePath))
  state = JSON.parse(readFileSync(statePath, 'utf8'));
if (
  option('--resume') &&
  (!existsSync(statePath) || !state.resumeAllowed || state.session !== session)
) {
  log({ kind: 'resume-refused', session });
  process.stderr.write(`No conversation found with session ID: ${session}\n`);
  process.exit(1);
}
if (option('--resume')) {
  state.resumeAllowed = false;
  writeFileSync(statePath, JSON.stringify(state));
  log({ kind: 'resume-loaded', session, memory: state.memory });
}
function emit(message: Record<string, unknown>) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}
function lifecycle(uuid: string, state: string) {
  log({ kind: 'lifecycle', uuid, state });
  emit({ type: 'command_lifecycle', command_uuid: uuid, state, session_id: session });
}
let resultNumber = state.resultNumber;
let assistantNumber = 0;
let active: string[] = [];
let malformedCancel = false;
let plainResult = false;
let initDelayed = process.env.UNIT18_DELAY_INIT === '1';
function publishInit() {
  emit({
    type: 'system',
    subtype: 'init',
    session_id: session,
    uuid: 'script-init',
    cwd: process.cwd(),
    model: 'haiku',
    tools: part3 ? [] : ['Write', 'AskUserQuestion'],
    mcp_servers: [],
    slash_commands: [],
    permissionMode: 'default',
    capabilities: ['interrupt_cancel_queued_v1', 'interrupt_receipt_v1'],
  });
}
const queued = new Set<string>();
const pending = new Map<string, { uuid: string; tool: string; input: unknown }>();
function assistant(content: unknown[]) {
  assistantNumber += 1;
  emit({
    type: 'assistant',
    session_id: session,
    parent_tool_use_id: null,
    uuid: `script-assistant-${assistantNumber}`,
    message: {
      id: `script-message-${assistantNumber}`,
      role: 'assistant',
      model: 'haiku',
      content,
    },
  });
}
function text(value: string) {
  assistant([{ type: 'text', text: value }]);
}
function finish(interrupted = false) {
  const completed = active;
  active = [];
  for (const uuid of completed) lifecycle(uuid, interrupted ? 'cancelled' : 'completed');
  resultNumber += 1;
  state.resultNumber = resultNumber;
  writeFileSync(statePath, JSON.stringify(state));
  const result = {
    type: 'result',
    subtype: 'success',
    duration_ms: 1,
    duration_api_ms: 1,
    is_error: false,
    num_turns: 1,
    session_id: session,
    uuid: `script-result-${resultNumber}`,
    user_message_uuids: completed,
    terminal_reason: interrupted ? 'aborted_streaming' : 'completed',
    total_cost_usd: resultNumber / 1000,
    usage: {
      input_tokens: plainResult ? 'unit18-float-input' : resultNumber * 10,
      output_tokens: plainResult ? 5 : resultNumber * 2,
      cache_read_input_tokens: 1,
    },
    result: interrupted ? '' : 'script completed',
  };
  process.stdout.write(`${JSON.stringify(result).replace('"unit18-float-input"', '5.0')}\n`);
}
function answer(request: Record<string, unknown>, payload: unknown) {
  emit({
    type: 'control_response',
    response: { subtype: 'success', request_id: request.request_id, response: payload },
  });
}
const input = createInterface({ input: process.stdin });
input.on('line', (line) => {
  const message = JSON.parse(line) as Record<string, any>;
  log({ kind: 'wire-in', message });
  if (message.type === 'control_request') {
    if (message.request.subtype === 'initialize') {
      answer(message, {
        commands: [],
        models: [
          {
            value: 'haiku',
            resolvedModel: 'haiku',
            displayName: 'Haiku',
            supportedEffortLevels: ['low'],
          },
        ],
      });
      if (initDelayed) log({ kind: 'system-init-withheld' });
      else publishInit();
    } else if (message.request.subtype === 'interrupt') {
      const cancelled = malformedCancel ? [null] : message.request.cancel_queued ? [...queued] : [];
      const still_queued = message.request.cancel_queued ? [] : [...queued];
      log({
        kind: 'interrupt',
        cancel_queued: message.request.cancel_queued === true,
        cancelled,
        still_queued,
      });
      answer(message, { cancelled, still_queued });
      for (const uuid of cancelled) {
        if (uuid !== null) {
          queued.delete(uuid);
          lifecycle(uuid, 'cancelled');
        }
      }
      pending.clear();
      setImmediate(() => {
        finish(true);
        if (initDelayed) {
          initDelayed = false;
          log({ kind: 'system-init-released' });
          publishInit();
        }
      });
    } else {
      answer(message, {});
    }
    return;
  }
  if (message.type === 'control_response') {
    const response = message.response;
    const ask = pending.get(response.request_id);
    if (!ask) return;
    pending.delete(response.request_id);
    log({ kind: 'tool-answer', tool: ask.tool, response: response.response });
    emit({
      type: 'user',
      session_id: session,
      parent_tool_use_id: null,
      message: {
        role: 'user',
        content: [
          {
            type: 'tool_result',
            tool_use_id: ask.tool,
            content: 'script tool result',
            is_error: false,
          },
        ],
      },
    });
    text(ask.tool === 'question-tool' ? 'question answered' : 'permission answered');
    finish();
    return;
  }
  if (message.type !== 'user') return;
  const content = message.message.content;
  const value =
    typeof content === 'string'
      ? content
      : content
          .filter((piece: any) => piece.type === 'text')
          .map((piece: any) => piece.text)
          .join('');
  const scenario = /\[CASE:([^\]]+)\]/.exec(value)?.[1];
  const uuid = message.uuid;
  if (typeof uuid !== 'string') throw new Error('The adapter must stamp ordinary input UUIDs');
  log({ kind: 'prompt', uuid, scenario, text: value });
  lifecycle(uuid, 'queued');
  if (scenario === 'queued') {
    queued.add(uuid);
    return;
  }
  lifecycle(uuid, 'started');
  active.push(uuid);
  if (part3) {
    let reply: string;
    if (value.includes('Reply exactly UNIT18_FRESH_OK')) {
      reply = 'UNIT18_FRESH_OK';
    } else {
      const token = /UNIT18_MEMORY_[0-9a-f]+/.exec(value)?.[0];
      if (token && value.includes('Reply exactly STORED')) {
        state.memory = token;
        reply = 'STORED';
      } else if (value.includes('Recall the exact memory token from the previous turn')) {
        if (!option('--resume') || !state.memory) throw new Error('No scripted seed memory exists');
        reply = state.memory;
      } else {
        throw new Error('Unexpected prompt in the manual provider rehearsal');
      }
    }
    emit({
      type: 'stream_event',
      session_id: session,
      uuid: 'part3-stream',
      parent_tool_use_id: null,
      event: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: reply } },
    });
    text(reply);
    finish();
    return;
  }
  if (scenario === 'crash7') {
    process.stderr.write('synthetic process crash\n');
    process.exit(7);
  }
  plainResult = scenario === 'plain';
  if (plainResult) state.memory = 'unit18-memory-token';
  if (scenario === 'malformed-assistant') {
    emit({
      type: 'assistant',
      session_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
      message: { model: 'haiku', content: [{ type: 'text' }] },
    });
    return;
  }
  malformedCancel = scenario === 'malformed-cancel-base';
  if (
    scenario === 'steer-base' ||
    scenario === 'queue-base' ||
    scenario === 'interrupt-base' ||
    scenario === 'malformed-cancel-base' ||
    scenario === 'delayed-init-base'
  )
    return;
  if (scenario === 'permission' || scenario === 'question') {
    const tool = scenario === 'question' ? 'question-tool' : 'permission-tool';
    const toolName = scenario === 'question' ? 'AskUserQuestion' : 'Write';
    const toolInput =
      scenario === 'question'
        ? {
            questions: [
              {
                question: 'Choose a color?',
                header: 'Color',
                options: [
                  { label: 'Blue', description: 'Use blue.' },
                  { label: 'Red', description: 'Use red.' },
                ],
                multiSelect: false,
              },
            ],
          }
        : { file_path: 'synthetic.txt', content: 'No actual tool executes.' };
    const requestId = `script-${tool}`;
    pending.set(requestId, { uuid, tool, input: toolInput });
    assistant([{ type: 'tool_use', id: tool, name: toolName, input: toolInput }]);
    emit({
      type: 'control_request',
      request_id: requestId,
      request: {
        subtype: 'can_use_tool',
        tool_name: toolName,
        input: toolInput,
        tool_use_id: tool,
        permission_suggestions: [],
      },
    });
    return;
  }
  if (scenario === 'plain') {
    emit({
      type: 'stream_event',
      session_id: session,
      uuid: 'script-stream-plain',
      parent_tool_use_id: null,
      event: {
        type: 'content_block_delta',
        index: 0,
        delta: { type: 'text_delta', text: 'plain live text' },
      },
    });
  }
  if (scenario === 'resume-success') log({ kind: 'resume-memory', memory: state.memory });
  text(
    scenario === 'steer'
      ? 'steer answer'
      : scenario === 'resume-success'
        ? state.memory
        : 'plain answer',
  );
  finish();
});
input.once('close', () => process.exit(0));
