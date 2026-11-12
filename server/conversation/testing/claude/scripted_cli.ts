#!/usr/bin/env node
/** A local protocol peer. It never executes tools or contacts a provider. */
import { appendFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
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
const launchMetadata = process.env.UNIT18_WRAPPED_FILE;
if (!launchMetadata) throw new Error('The scripted CLI requires captured launch argv');
log({
  kind: 'launch',
  pid: process.pid,
  node_executable: process.execPath,
  environment: process.env,
  args: arguments_,
  ...JSON.parse(readFileSync(launchMetadata, 'utf8')),
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
  const raw = JSON.stringify(message);
  log({ kind: 'wire-out', message, raw_json: raw });
  process.stdout.write(`${raw}\n`);
}
function lifecycle(uuid: string, state: string) {
  log({ kind: 'lifecycle', uuid, state });
  emit({
    type: 'command_lifecycle',
    command_uuid: uuid,
    state,
    session_id: session,
    uuid: `script-lifecycle-${uuid}-${state}`,
  });
}
let resultNumber = state.resultNumber;
let assistantNumber = 0;
let active: string[] = [];
let malformedCancel = false;
let plainResult = false;
let initDelayed = process.env.UNIT18_DELAY_INIT === '1';
let initPublished = false;
let observedPattern: 'plain' | 'resume' | null = null;
function publishInit() {
  initPublished = true;
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
    terminal_slash_commands: [],
    apiKeySource: 'synthetic-fixture',
    claude_code_version: '2.1.316',
    output_style: 'default',
    agents: [],
    skills: [],
    plugins: [],
    analytics_disabled: true,
    product_feedback_disabled: true,
    memory_paths: {},
    original_cwd: process.cwd(),
    additional_directories: [],
    messaging_socket_path: 'synthetic-socket',
    fast_mode_state: 'off',
    fast_mode_disabled_reason: 'synthetic-disabled',
    per_turn_effort_active: false,
    ultrathink_available: false,
    ultrathink_typed_raises: false,
    view_mode: 'default',
    user_message_uuids: [...active],
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
    timestamp: '2000-01-01T00:00:00.000Z',
    request_id: `synthetic-request-${resultNumber}`,
    user_message_uuid: active[0],
    user_message_uuids: [...active],
    message: {
      id: `script-message-${assistantNumber}`,
      role: 'assistant',
      model: 'haiku',
      content,
      type: 'message',
      container: null,
      stop_reason: null,
      stop_sequence: null,
      stop_details: null,
      usage: {},
      input_transformations: [],
      diagnostics: observedPattern === 'resume' ? {} : null,
      context_management: null,
    },
  });
}
function text(value: string) {
  assistant([{ type: 'text', text: value }]);
}
function status() {
  emit({
    type: 'system',
    subtype: 'status',
    status: 'synthetic-ready',
    session_id: session,
    uuid: `synthetic-status-${resultNumber}`,
    user_message_uuids: [...active],
  });
}
function rateLimit() {
  // Only the outer object shape was recorded in the real turn. This is synthetic.
  emit({
    type: 'rate_limit_event',
    rate_limit_info: { status: 'allowed' },
    session_id: session,
    uuid: `synthetic-rate-${resultNumber}`,
  });
}
function streamedAnswer(value: string, pieces: string[]) {
  let index = 0;
  const stream = (event: Record<string, unknown>) => {
    emit({
      type: 'stream_event',
      event,
      session_id: session,
      parent_tool_use_id: null,
      uuid: `synthetic-stream-${resultNumber}-${index}`,
      api_message_id: `synthetic-api-${resultNumber}`,
      ...(index++ === 0
        ? {
            ttft_ms: 1,
            thinking_display: 'synthetic-none',
            user_message_uuid: active[0],
            user_message_uuids: [...active],
          }
        : {}),
    });
  };
  // Inner stream discriminators were not retained in the real evidence. These
  // representative SDK frames prove the observed outer order and text delivery.
  status();
  if (observedPattern === 'plain') rateLimit();
  stream({
    type: 'message_start',
    message: {
      id: `synthetic-api-${resultNumber}`,
      type: 'message',
      role: 'assistant',
      model: 'haiku',
      content: [],
      usage: {},
    },
  });
  stream({ type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } });
  for (const piece of pieces) {
    stream({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: piece } });
  }
  text(value);
  stream({ type: 'content_block_stop', index: 0 });
  stream({
    type: 'message_delta',
    delta: { stop_reason: 'end_turn' },
    usage: { output_tokens: 5 },
  });
  stream({ type: 'message_stop' });
  if (observedPattern === 'resume') rateLimit();
}
function finish(interrupted = false) {
  const completed = active;
  active = [];
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
    user_message_uuid: completed[0],
    terminal_reason: interrupted ? 'aborted_streaming' : 'completed',
    total_cost_usd: resultNumber / 1000,
    usage: {
      input_tokens: plainResult ? 'unit18-float-input' : resultNumber * 10,
      output_tokens: plainResult ? 5 : resultNumber * 2,
      cache_read_input_tokens: 1,
    },
    result: interrupted ? '' : 'script completed',
    api_error_status: null,
    credential_expires_at: '2000-01-01T00:00:00.000Z',
    fast_mode_disabled_reason: 'synthetic-disabled',
    fast_mode_state: 'off',
    first_content_frame_ms: 1,
    first_request_cache_read_input_tokens: 0,
    first_request_input_tokens: 5,
    modelUsage: {},
    model_usage_since_last_result: {},
    permission_denials: [],
    queued_turn_count: 0,
    request_sent_wall_ms: 100000,
    result_index: resultNumber,
    safety_stops: 0,
    stop_reason: 'end_turn',
    subagent_stats: {},
    time_to_request_ms: 1,
    ttft_ms: 1,
    ttft_stream_ms: 1,
  };
  const raw = JSON.stringify(result).replace('"unit18-float-input"', '5.0');
  log({ kind: 'wire-out', message: JSON.parse(raw), raw_json: raw });
  process.stdout.write(`${raw}\n`);
  for (const uuid of completed) lifecycle(uuid, interrupted ? 'cancelled' : 'completed');
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
  if (!initPublished && !initDelayed) publishInit();
  observedPattern =
    scenario === 'plain' ? 'plain' : scenario === 'resume-success' ? 'resume' : null;
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
    streamedAnswer('plain live text', ['plain live ', 'text']);
    finish();
    return;
  }
  if (scenario === 'resume-success') {
    log({ kind: 'resume-memory', memory: state.memory });
    streamedAnswer(state.memory, ['unit18-', 'memory', '-', 'to', 'ken']);
    finish();
    return;
  }
  text(scenario === 'steer' ? 'steer answer' : 'plain answer');
  finish();
});
input.once('close', () => process.exit(0));
