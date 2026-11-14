/** Native Claude facts enter the unchanged backend event seam here. */
import { stringify_python_json } from '../../../core/http/numeric_json.ts';
import { python_truth } from '../../../core/python_truth.ts';
export { python_truth } from '../../../core/python_truth.ts';
import { ComposerCatalogEntry, ComposerCatalogEntryKind } from '../../contracts.ts';
import { PlanEntry, PlanEntryStatus, ToolCallStatus } from '../../events.ts';
import { text_message_content } from '../../message_content.ts';
import * as B from '../contracts.ts';
import type { ClaudeSdkMessage } from './query_contracts.ts';
import type { TurnInFlight } from './turn.ts';
import { display_text } from './failure_summary.ts';

export function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function required(fields: Record<string, unknown>, key: string, kind: string): unknown {
  if (!Object.hasOwn(fields, key))
    throw new Error(`Missing required field in ${kind} message: '${key}'`);
  return fields[key];
}
/** Mirror only the required native fields that Python's SDK parser consumes. */
export function validate_message(message: ClaudeSdkMessage): void {
  const kind = message.type;
  if (kind === 'assistant' || kind === 'user') {
    const raw = required(message, 'message', kind);
    const body = record(raw);
    if (body === null) throw new TypeError(`${kind} message must contain an object`);
    const content = required(body, 'content', kind);
    if (kind === 'assistant' && !Array.isArray(content))
      throw new Error('Assistant content must be an array');
    if (Array.isArray(content)) {
      for (const value of content) {
        const block = record(value);
        if (!block) throw new Error('Content block must be an object');
        const type = required(block, 'type', kind);
        let keys: string[] = [];
        if (type === 'text') keys = ['text'];
        else if (type === 'thinking' && kind === 'assistant') keys = ['thinking', 'signature'];
        else if (type === 'tool_use' || (type === 'server_tool_use' && kind === 'assistant'))
          keys = ['id', 'name', 'input'];
        else if (type === 'tool_result') keys = ['tool_use_id'];
        else if (type === 'advisor_tool_result' && kind === 'assistant')
          keys = ['tool_use_id', 'content'];
        for (const key of keys) required(block, key, kind);
      }
    }
    if (kind === 'assistant') required(body, 'model', kind);
  } else if (kind === 'result') {
    for (const key of [
      'subtype',
      'duration_ms',
      'duration_api_ms',
      'is_error',
      'num_turns',
      'session_id',
    ])
      required(message, key, kind);
    if (python_truth(message.deferred_tool_use)) {
      const deferred = record(message.deferred_tool_use);
      if (!deferred) throw new TypeError('Deferred tool use must be an object');
      for (const key of ['id', 'name', 'input']) required(deferred, key, kind);
    }
  } else if (kind === 'stream_event') {
    for (const key of ['uuid', 'session_id', 'event']) required(message, key, kind);
  } else if (kind === 'system') {
    const subtype = required(message, 'subtype', kind);
    let keys: string[] = [];
    if (subtype === 'task_started') keys = ['task_id', 'description', 'uuid', 'session_id'];
    else if (subtype === 'task_progress')
      keys = ['task_id', 'description', 'usage', 'uuid', 'session_id'];
    else if (subtype === 'task_notification')
      keys = ['task_id', 'status', 'output_file', 'summary', 'uuid', 'session_id'];
    for (const key of keys) required(message, key, kind);
  } else if (kind === 'rate_limit_event') {
    const raw = required(message, 'rate_limit_info', kind);
    const info = record(raw);
    if (!info) throw new TypeError(`${kind} message must contain an object`);
    required(info, 'status', kind);
    for (const key of ['uuid', 'session_id']) required(message, key, kind);
  } else if (kind === 'conversation_reset') {
    for (const key of ['new_conversation_id', 'uuid', 'session_id']) required(message, key, kind);
  }
}
function joined_text(values: readonly unknown[]): string {
  for (const [index, value] of values.entries()) {
    if (typeof value !== 'string') throw new TypeError(`Text item ${index} must be a string`);
  }
  return values.join('');
}
export function canonical_json(value: unknown): string | null {
  if (!python_truth(value)) return null;
  try {
    return stringify_python_json(value, { sort_keys: true, ensure_ascii: false });
  } catch {
    return display_text(value);
  }
}
export function composer_catalog(
  handshake: Record<string, unknown> | null,
): readonly ComposerCatalogEntry[] | null {
  if (!handshake || !Array.isArray(handshake.commands)) return null;
  const commands: ComposerCatalogEntry[] = [];
  for (const value of handshake.commands) {
    const entry = record(value);
    if (!entry || typeof entry.name !== 'string' || !entry.name) continue;
    commands.push(
      new ComposerCatalogEntry({
        kind: ComposerCatalogEntryKind.command,
        display_text: `/${entry.name}`,
        insertion_text: `/${entry.name} `,
        description: typeof entry.description === 'string' ? entry.description : '',
        argument_hint:
          typeof entry.argumentHint === 'string' && entry.argumentHint ? entry.argumentHint : null,
      }),
    );
  }
  return commands;
}
export function durable_session_id(message: ClaudeSdkMessage): string | null {
  if (message.type === 'system') {
    if (['hook_started', 'hook_progress', 'hook_response'].includes(String(message.subtype)))
      return null;
  } else if (!['assistant', 'result', 'stream_event', 'rate_limit_event'].includes(message.type))
    return null;
  return typeof message.session_id === 'string' ? message.session_id : null;
}
function plan_entries(block: Record<string, unknown>): readonly PlanEntry[] | null {
  const input = record(block.input);
  if (block.name !== 'TodoWrite' || !input || !Array.isArray(input.todos) || !input.todos.length)
    return null;
  const entries: PlanEntry[] = [];
  for (const value of input.todos) {
    const todo = record(value);
    if (
      !todo ||
      typeof todo.content !== 'string' ||
      !todo.content ||
      typeof todo.status !== 'string' ||
      !Object.values(PlanEntryStatus).includes(todo.status as PlanEntryStatus)
    )
      return null;
    entries.push(new PlanEntry({ text: todo.content, status: todo.status as PlanEntryStatus }));
  }
  return entries;
}
function tool_result_detail(content: unknown): string | null {
  if (content == null) return null;
  if (typeof content === 'string') return content || null;
  if (!Array.isArray(content)) return null;
  const texts = content
    .map(record)
    .map((item) => item?.text)
    .filter((text): text is string => typeof text === 'string' && Boolean(text));
  return texts.join('\n') || null;
}
function blocks(message: ClaudeSdkMessage): Record<string, unknown>[] {
  const content = record(message.message)?.content;
  const kinds =
    message.type === 'assistant'
      ? ['text', 'thinking', 'tool_use', 'tool_result', 'server_tool_use', 'advisor_tool_result']
      : ['text', 'tool_use', 'tool_result'];
  return Array.isArray(content)
    ? content
        .map(record)
        .filter(
          (block): block is Record<string, unknown> =>
            block !== null && kinds.includes(block.type as string),
        )
    : [];
}
export class ClaudeEvents {
  private readonly sink: B.BackendEventSink;
  private live_background_tasks = new Map<string, string>();
  private readonly background_endings = new Map<string, string>();
  constructor(sink: B.BackendEventSink) {
    this.sink = sink;
  }
  forget_background_tasks(): void {
    this.live_background_tasks.clear();
    this.background_endings.clear();
  }
  async take_background(message: ClaudeSdkMessage): Promise<boolean> {
    if (message.type !== 'system') return false;
    if (message.subtype === 'task_notification' || message.subtype === 'task_updated') {
      const status =
        message.subtype === 'task_updated' ? record(message.patch)?.status : message.status;
      if (
        typeof status === 'string' &&
        ['completed', 'failed', 'stopped', 'killed'].includes(status) &&
        typeof message.task_id === 'string' &&
        this.live_background_tasks.has(message.task_id)
      ) {
        this.background_endings.set(message.task_id, status);
      }
      return true;
    }
    if (message.subtype === 'task_started') return true;
    if (message.subtype !== 'background_tasks_changed') return false;
    const live = new Map<string, string>();
    const ambient = new Set<string>();
    for (const value of Array.isArray(message.tasks) ? message.tasks : []) {
      const task = record(value);
      if (!task || typeof task.task_id !== 'string' || typeof task.description !== 'string')
        continue;
      if (task.ambient === true) ambient.add(task.task_id);
      else live.set(task.task_id, task.description);
    }
    const previous = this.live_background_tasks;
    this.live_background_tasks = live;
    for (const task_id of previous.keys()) {
      if (live.has(task_id)) continue;
      const task_status =
        this.background_endings.get(task_id) ?? (ambient.has(task_id) ? 'ambient' : 'unreported');
      this.background_endings.delete(task_id);
      await this.sink.report(null, new B.BackgroundTaskEnded({ task_id, task_status }));
    }
    for (const [task_id, task_description] of live) {
      if (!previous.has(task_id))
        await this.sink.report(null, new B.BackgroundTaskStarted({ task_id, task_description }));
    }
    return true;
  }
  async stream_event(turn: TurnInFlight, message: ClaudeSdkMessage): Promise<void> {
    const event = record(message.event);
    if (event?.type !== 'content_block_delta') return;
    const delta = record(event.delta);
    if (!delta) return;
    if (delta.type === 'thinking_delta') {
      await this.sink.report(turn.token, new B.ModelThinkingHappened());
      return;
    }
    if (delta.type !== 'text_delta' || typeof delta.text !== 'string' || !delta.text) return;
    if (message.parent_tool_use_id != null) {
      await this.sink.report(
        turn.token,
        new B.ToolCallProgress({
          tool_call_id: message.parent_tool_use_id as string,
          detail: delta.text,
        }),
      );
      return;
    }
    await this.sink.report(turn.token, new B.AgentMessageDelta({ text_delta: delta.text }));
  }
  async flush_replies(turn: TurnInFlight): Promise<void> {
    const replies = turn.command_replies;
    turn.command_replies = [];
    for (const reply_text of replies)
      await this.sink.report(turn.token, new B.CommandReplyReported({ reply_text }));
  }
  private async complete_message(turn: TurnInFlight, said: string[]): Promise<void> {
    if (!said.length) return;
    const text = joined_text(said);
    said.length = 0;
    await this.sink.report(
      turn.token,
      new B.AgentMessageCompleted({ content: text_message_content(text) }),
    );
  }
  async assistant_message(
    turn: TurnInFlight,
    message: ClaudeSdkMessage,
    after_turn = false,
  ): Promise<void> {
    if (message.parent_tool_use_id != null) return;
    const content = blocks(message);
    if (after_turn) {
      const said: string[] = [];
      for (const block of content) {
        if (block.type === 'text') said.push(block.text as string);
        else if (block.type !== 'thinking') return;
      }
      await this.complete_message(turn, said);
      return;
    }
    if (record(message.message)?.model === '<synthetic>') {
      if (turn.automatic_compaction) return;
      const text = joined_text(
        content.filter((block) => block.type === 'text').map((block) => block.text),
      );
      if (text) turn.command_replies.push(text);
      return;
    }
    await this.flush_replies(turn);
    const said: string[] = [];
    for (const block of content) {
      if (block.type === 'text') said.push(block.text as string);
      else if (block.type === 'tool_use') {
        await this.complete_message(turn, said);
        if (block.name === 'AskUserQuestion') {
          turn.user_input_tool_use_ids.add(block.id as string);
          continue;
        }
        const plan = plan_entries(block);
        if (plan !== null) await this.sink.report(turn.token, new B.PlanUpdated({ entries: plan }));
        await this.sink.report(
          turn.token,
          new B.ToolCallStarted({
            tool_call_id: block.id as string,
            title: block.name as string,
            tool_kind: block.name as string,
            detail: canonical_json(block.input),
          }),
        );
      } else if (block.type === 'thinking')
        await this.sink.report(turn.token, new B.ModelThinkingHappened());
    }
    await this.complete_message(turn, said);
  }
  async user_message(turn: TurnInFlight, message: ClaudeSdkMessage): Promise<void> {
    if (message.parent_tool_use_id != null) return;
    for (const block of blocks(message)) {
      if (
        block.type !== 'tool_result' ||
        turn.user_input_tool_use_ids.has(block.tool_use_id as string)
      )
        continue;
      await this.sink.report(
        turn.token,
        new B.ToolCallFinished({
          tool_call_id: block.tool_use_id as string,
          tool_call_status: block.is_error ? ToolCallStatus.failed : ToolCallStatus.completed,
          detail: tool_result_detail(block.content),
        }),
      );
    }
  }
  async usage(turn: TurnInFlight, message: ClaudeSdkMessage): Promise<void> {
    const usage = record(message.usage) ?? {};
    const count = (value: unknown): number | null =>
      typeof value === 'boolean'
        ? Number(value)
        : typeof value === 'number' && Number.isInteger(value)
          ? value
          : null;
    await this.sink.report(
      turn.token,
      new B.TokenUsageReported({
        input_tokens: count(usage.input_tokens),
        output_tokens: count(usage.output_tokens),
        cached_input_tokens: count(usage.cache_read_input_tokens),
        cost_usd: (message.total_cost_usd ?? null) as number | null,
      }),
    );
  }
}
