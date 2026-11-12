#!/usr/bin/env node
/** A local protocol peer. It never executes tools or contacts a provider. */
import { appendFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

if (process.argv.includes('--version') || process.argv.includes('-v')) {
  process.stdout.write('2.1.316 (Claude Code)\n');
  process.exit(0);
}
const arguments_ = process.argv.slice(2);
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
log({ kind: 'launch', pid: process.pid, environment: process.env, args: arguments_ });
process.once('exit', () => log({ kind: 'exit', pid: process.pid }));
process.once('SIGTERM', () => process.exit(0));
if (option('--resume')) {
  log({ kind: 'resume-refused', session });
  process.stderr.write(`No conversation found with session ID: ${session}\n`);
  process.exit(1);
}
function emit(message: Record<string, unknown>) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}
function lifecycle(uuid: string, state: string) {
  log({ kind: 'lifecycle', uuid, state });
  emit({ type: 'command_lifecycle', command_uuid: uuid, state, session_id: session });
}
let resultNumber = 0;
let assistantNumber = 0;
let active: string[] = [];
let malformedCancel = false;
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
  emit({
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
      input_tokens: resultNumber * 10,
      output_tokens: resultNumber * 2,
      cache_read_input_tokens: 1,
    },
    result: interrupted ? '' : 'script completed',
  });
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
      emit({
        type: 'system',
        subtype: 'init',
        session_id: session,
        uuid: 'script-init',
        cwd: process.cwd(),
        model: 'haiku',
        tools: ['Write', 'AskUserQuestion'],
        mcp_servers: [],
        slash_commands: [],
        permissionMode: 'default',
        capabilities: ['interrupt_cancel_queued_v1', 'interrupt_receipt_v1'],
      });
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
      setImmediate(() => finish(true));
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
    scenario === 'malformed-cancel-base'
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
  text(scenario === 'steer' ? 'steer answer' : 'plain answer');
  finish();
});
input.once('close', () => process.exit(0));
