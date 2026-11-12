/** The real SDK owns the protocol. Public streams preserve Python's consumed facts. */
import { spawn } from 'node:child_process';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { Writable } from 'node:stream';
import { StringDecoder } from 'node:string_decoder';
import { CLAUDE_AGENT_SDK_VERSION, load_claude_sdk } from '../claude_sdk.ts';
import type {
  Query,
  SDKUserMessage,
  SDKMessage,
  SpawnedProcess,
  SpawnOptions,
} from '@anthropic-ai/claude-agent-sdk';
import { PromptWriteFailed } from '../contracts.ts';
import { ensure_cli_compatible } from '../cli_compatibility.ts';
import { WorkerUnit } from '../../worker_unit/index.ts';
import { with_tool_shell_kill_priority } from '../../tool_shell_kill_priority.ts';
import {
  CLAUDE_CANCEL_QUEUED_TIMEOUT_SECONDS,
  CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS,
  CLAUDE_STEER_ADMISSION_TIMEOUT_SECONDS,
} from './configuration.ts';
import type { ClaudeSdkClient, ClaudeSdkOptions, ClaudeSdkMessage } from './query_contracts.ts';
import { ClaudeReceipts } from './receipts.ts';
import {
  ObservedStdout,
  ObservedStderr,
  Pending,
  bounded,
  cancellation_uuid_set,
  object,
} from './wire.ts';

export type {
  ClaudeSdkClient,
  ClaudeSdkClientFactory,
  ClaudeSdkOptions,
  ClaudeSdkMessage,
} from './query_contracts.ts';

class InputQueue implements AsyncIterable<SDKUserMessage> {
  private readonly values: SDKUserMessage[] = [];
  private waiting: Pending<IteratorResult<SDKUserMessage>> | null = null;
  private ended = false;
  push(value: SDKUserMessage): void {
    if (this.ended) throw new Error('Cannot write to closed transport');
    if (this.waiting !== null) {
      const waiting = this.waiting;
      this.waiting = null;
      waiting.resolve({ value, done: false });
    } else this.values.push(value);
  }
  end(): void {
    this.ended = true;
    this.values.length = 0;
    this.waiting?.resolve({ value: undefined, done: true });
    this.waiting = null;
  }
  [Symbol.asyncIterator](): AsyncIterator<SDKUserMessage> {
    return {
      next: async () => {
        const value = this.values.shift();
        if (value !== undefined) return { value, done: false };
        if (this.ended) return { value: undefined, done: true };
        this.waiting = new Pending();
        return this.waiting.promise;
      },
      return: async () => {
        this.end();
        return { value: undefined, done: true };
      },
    };
  }
}
interface PendingWrite {
  uuid: string | undefined;
  completed: Pending<void>;
}
interface CancelAttempt {
  request_id: string | null;
  response: Pending<Record<string, unknown> | null>;
}

// SDK 0.3.316 readMessages consumes control/keep-alive/mirror/host-only frames.
// Count only the native message families that the Python adapter can consume.
// The version assertion in query.test.ts forces review when the SDK pin changes.
const ORDERED_SYSTEM_SUBTYPES = {
  api_retry: true,
  background_tasks_changed: true,
  commands_changed: true,
  compact_boundary: true,
  control_request_progress: true,
  elicitation_complete: true,
  files_persisted: true,
  hook_progress: true,
  hook_response: true,
  hook_started: true,
  informational: true,
  local_command_output: true,
  memory_recall: true,
  mirror_error: true,
  model_refusal_fallback: true,
  model_refusal_no_fallback: true,
  notification: true,
  permission_denied: true,
  plugin_install: true,
  session_state_changed: true,
  status: true,
  init: true,
  task_notification: true,
  task_progress: true,
  task_started: true,
  task_updated: true,
  thinking_tokens: true,
  worker_shutting_down: true,
} satisfies Record<Extract<SDKMessage, { type: 'system' }>['subtype'], true>;
export const CLAUDE_PERMISSION_ORDER = {
  sdk_version: CLAUDE_AGENT_SDK_VERSION,
  message_types: [
    'assistant',
    'user',
    'result',
    'stream_event',
    'system',
    'rate_limit_event',
    'conversation_reset',
  ],
  system_subtypes: Object.keys(ORDERED_SYSTEM_SUBTYPES),
  sdk_skipped_types: [
    'control_request',
    'control_response',
    'control_cancel_request',
    'keep_alive',
    'transcript_mirror',
  ],
} as const;
const ORDERED_MESSAGE_TYPES = new Set<string>(CLAUDE_PERMISSION_ORDER.message_types);
function ordered_message(message: Record<string, unknown>): boolean {
  return (
    typeof message.type === 'string' &&
    ORDERED_MESSAGE_TYPES.has(message.type) &&
    !(
      message.type === 'system' &&
      message.subtype === 'session_state_changed' &&
      message.sdk_host_only === true
    ) &&
    (message.type !== 'system' ||
      (typeof message.subtype === 'string' &&
        Object.hasOwn(ORDERED_SYSTEM_SUBTYPES, message.subtype)))
  );
}
function native_exit_failure(cause: unknown): unknown {
  if (!(cause instanceof Error)) return cause;
  const native = cause as Error & { errorClass?: string; exitCode?: number };
  const match = /^Claude Code process exited with code (-?\d+)(?:\. stderr: [\s\S]*)?$/.exec(
    cause.message,
  );
  if (
    native.errorClass !== 'process_exited_nonzero' ||
    !match ||
    !Number.isInteger(native.exitCode) ||
    Number(match[1]) !== native.exitCode
  )
    return cause;
  return new Error(
    `Command failed with exit code ${native.exitCode} (exit code: ${native.exitCode})\nError output: Check stderr output for details`,
  );
}

class ObservedClaudeSdkClient implements ClaudeSdkClient {
  private readonly options: ClaudeSdkOptions;
  private readonly receipts = new ClaudeReceipts();
  private readonly input = new InputQueue();
  private readonly pending_writes: PendingWrite[] = [];
  private readonly active_writes = new Set<Pending<void>>();
  private cli: Query | null = null;
  private info: Record<string, unknown> | null = null;
  private child: ChildProcessWithoutNullStreams | null = null;
  private child_exit: Pending<void> | null = null;
  private worker_unit: WorkerUnit | null = null;
  private worker_unit_stop: Promise<void> | null = null;
  private stderr: ObservedStderr | null = null;
  private cleanup_started = false;
  private stream_failure: Error | null = null;
  private cancel_attempt: CancelAttempt | null = null;
  private wire_message_count = 0;
  private delivered_message_count = 0;
  private processed_message_count = 0;
  private permission_fallback_floor = 0;
  private reader_started = false;
  private readonly permission_targets = new Map<string, number>();
  private readonly permission_waiters = new Set<{ target: number; ready: Pending<boolean> }>();
  private readonly raw_result_usage = new Map<string | null, Record<string, unknown>[]>();

  constructor(options: ClaudeSdkOptions) {
    this.options = options;
  }
  async connect(): Promise<void> {
    if (this.cleanup_started) throw new Error('a Claude transport cannot reconnect after cleanup');
    if (this.cli !== null) throw new Error('Claude transport is already connected');
    const overrides: Record<string, string> = {};
    for (const [key, value] of Object.entries(this.options.env ?? {})) {
      if (value !== undefined) overrides[key] = value;
    }
    await ensure_cli_compatible('claude', this.options.pathToClaudeCodeExecutable, {
      environment: overrides,
      working_directory: this.options.cwd,
    });
    with_tool_shell_kill_priority(overrides);
    const environment: Record<string, string> = {};
    for (const [key, value] of Object.entries(process.env)) {
      if (key !== 'CLAUDECODE' && value !== undefined) environment[key] = value;
    }
    environment.CLAUDE_CODE_ENTRYPOINT = 'sdk-ts';
    Object.assign(environment, overrides);
    // These are the two explicitly accepted migration differences from Python.
    environment.CLAUDE_CODE_ENTRYPOINT = 'sdk-ts';
    environment.CLAUDE_AGENT_SDK_VERSION = CLAUDE_AGENT_SDK_VERSION;
    if (this.options.cwd) environment.PWD = this.options.cwd;
    if (this.options.enableFileCheckpointing)
      environment.CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING = 'true';
    if (overrides.PLAN_TICKET_ID) {
      this.worker_unit = await WorkerUnit.create(environment, this.options.cwd ?? process.cwd());
      if (this.worker_unit !== null) Object.assign(environment, this.worker_unit.environment);
    }
    try {
      const { query: sdk_query } = await load_claude_sdk();
      this.cli = sdk_query({
        prompt: this.input,
        options: {
          ...this.options,
          env: environment,
          canUseTool:
            this.options.canUseTool === undefined
              ? undefined
              : async (tool, input, permission) => {
                  const target = this.permission_targets.get(permission.requestId) ?? 0;
                  this.permission_targets.delete(permission.requestId);
                  if (
                    !(await this.wait_for_processing(target, permission.signal)) ||
                    this.cleanup_started ||
                    permission.signal.aborted
                  ) {
                    return {
                      behavior: 'deny',
                      message: 'The turn ended before this was answered.',
                    };
                  }
                  return this.options.canUseTool!(tool, input, permission);
                },
          spawnClaudeCodeProcess: (options) => this.start_process(options, environment),
        },
      });
      const info: unknown = await this.cli.initializationResult();
      this.info = object(info) ? info : null;
    } catch (cause) {
      await this.stop_worker_unit();
      throw native_exit_failure(cause);
    }
  }

  private start_process(
    options: SpawnOptions,
    environment: Record<string, string>,
  ): SpawnedProcess {
    if (this.cleanup_started || this.child !== null)
      throw new Error('a Claude transport cannot reconnect after cleanup');
    const original = [options.command, ...options.args];
    const command = this.worker_unit === null ? original : this.worker_unit.argv(original);
    const child = spawn(command[0]!, [...command.slice(1)], {
      cwd: options.cwd,
      env: environment,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.child = child;
    const exit = new Pending<void>();
    this.child_exit = exit;
    child.once('close', () => {
      exit.resolve();
      this.receipts.stream_ended();
      this.release_permission_waiters(true);
      this.fail_writes(new Error('Cannot write to closed transport'));
    });
    child.once('error', (cause) => {
      this.stream_failure ??= cause;
      this.fail_writes(cause);
    });
    const stdout = new ObservedStdout((message) => {
      this.receipts.observe(message);
      this.observe_permission_order(message);
      if (message.type === 'result' && object(message.usage)) {
        const key = typeof message.uuid === 'string' ? message.uuid : null;
        const queued = this.raw_result_usage.get(key) ?? [];
        queued.push({ ...message.usage });
        this.raw_result_usage.set(key, queued);
      }
    });
    stdout.on('error', (cause) => {
      this.stream_failure ??= cause;
      this.receipts.stream_ended();
      this.release_permission_waiters(true);
    });
    child.stdout.pipe(stdout);
    const stderr = new ObservedStderr((line) => this.options.stderr?.(line));
    this.stderr = stderr;
    child.stderr.on('data', (chunk: Buffer) => stderr.write(chunk));
    child.stderr.on('end', () => stderr.end());
    child.stderr.on('error', () => stderr.end());
    child.stderr.on('close', () => stderr.end());
    child.stdout.on('error', (cause) => stdout.destroy(cause));
    const decoder = new StringDecoder('utf8');
    let outgoing = '';
    const stdin = new Writable({
      write: (chunk: Buffer, encoding, callback) => {
        let completed: Pending<void> | null = null;
        try {
          outgoing += decoder.write(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, encoding));
          const lines = outgoing.split('\n');
          outgoing = lines.pop() ?? '';
          for (const line of lines) {
            let frame: unknown;
            try {
              frame = JSON.parse(line);
            } catch {
              continue;
            }
            if (!object(frame)) continue;
            if (
              frame.type === 'control_response' &&
              object(frame.response) &&
              typeof frame.response.request_id === 'string'
            ) {
              this.permission_targets.delete(frame.response.request_id);
            }
            if (frame.type === 'user') {
              const uuid = typeof frame.uuid === 'string' ? frame.uuid : undefined;
              const index = this.pending_writes.findIndex((pending) => pending.uuid === uuid);
              if (index >= 0) completed = this.pending_writes.splice(index, 1)[0]!.completed;
            }
            if (
              frame.type === 'control_request' &&
              object(frame.request) &&
              frame.request.subtype === 'interrupt' &&
              frame.request.cancel_queued === true &&
              typeof frame.request_id === 'string' &&
              this.cancel_attempt !== null
            ) {
              const attempt = this.cancel_attempt;
              attempt.request_id = frame.request_id;
              void this.receipts
                .watch_control(frame.request_id)
                .then((response) => attempt.response.resolve(response));
            }
          }
          if (completed !== null) this.active_writes.add(completed);
          child.stdin.write(chunk, encoding, (cause) => {
            if (completed !== null) this.active_writes.delete(completed);
            if (cause) {
              completed?.reject(cause);
              this.break_writes(cause);
            } else completed?.resolve();
            callback(cause);
          });
        } catch (cause) {
          const error = cause instanceof Error ? cause : new Error(String(cause));
          if (completed !== null) this.active_writes.delete(completed);
          completed?.reject(error);
          this.break_writes(error);
          callback(error);
        }
      },
      final: (callback) => child.stdin.end(callback),
    });
    stdin.on('error', (cause) => this.break_writes(cause));
    child.stdin.on('error', (cause) => {
      this.break_writes(cause);
      stdin.destroy(cause);
    });
    // Custom SDK spawns must consume stderr themselves. Exit follows stream closure.
    return {
      stdin,
      stdout,
      get killed() {
        return child.killed;
      },
      get exitCode() {
        return child.exitCode;
      },
      get signalCode() {
        return child.signalCode;
      },
      kill: (signal) => child.kill(signal),
      on: (event, listener) => {
        child.on(event === 'exit' ? 'close' : event, listener as (...args: unknown[]) => void);
      },
      once: (event, listener) => {
        child.once(event === 'exit' ? 'close' : event, listener as (...args: unknown[]) => void);
      },
      off: (event, listener) => {
        child.off(event === 'exit' ? 'close' : event, listener as (...args: unknown[]) => void);
      },
    } as SpawnedProcess;
  }

  private observe_permission_order(message: Record<string, unknown>): void {
    if (message.type === 'control_request') {
      if (
        this.options.canUseTool !== undefined &&
        typeof message.request_id === 'string' &&
        object(message.request) &&
        message.request.subtype === 'can_use_tool' &&
        !this.permission_targets.has(message.request_id)
      ) {
        this.permission_targets.set(
          message.request_id,
          this.wire_message_count <= this.permission_fallback_floor ? 0 : this.wire_message_count,
        );
      }
      return;
    }
    if (message.type === 'control_cancel_request') {
      if (typeof message.request_id === 'string')
        this.permission_targets.delete(message.request_id);
      return;
    }
    if (ordered_message(message)) this.wire_message_count += 1;
    else if (
      !(
        typeof message.type === 'string' &&
        (CLAUDE_PERMISSION_ORDER.sdk_skipped_types as readonly string[]).includes(message.type)
      ) &&
      !(
        message.type === 'system' &&
        message.subtype === 'session_state_changed' &&
        message.sdk_host_only === true
      )
    ) {
      // An extension may not reach the iterator. Do not hold asks behind it.
      this.permission_fallback_floor = this.wire_message_count;
      this.release_permission_waiters(false, true);
    }
  }
  private release_permission_waiters(closed = false, fallback = false): void {
    for (const waiter of this.permission_waiters) {
      if (closed || fallback || this.processed_message_count >= waiter.target)
        waiter.ready.resolve(!closed);
    }
    if (closed || fallback) this.permission_targets.clear();
  }
  private async wait_for_processing(target: number, signal: AbortSignal): Promise<boolean> {
    if (this.cleanup_started || signal.aborted) return false;
    if (!this.reader_started || this.processed_message_count >= target) return true;
    const waiter = { target, ready: new Pending<boolean>() };
    const cancelled = () => waiter.ready.resolve(false);
    this.permission_waiters.add(waiter);
    signal.addEventListener('abort', cancelled, { once: true });
    try {
      const ready = await bounded(waiter.ready.promise, CLAUDE_STEER_ADMISSION_TIMEOUT_SECONDS);
      return ready.timed_out ? !this.cleanup_started && !signal.aborted : ready.value;
    } finally {
      signal.removeEventListener('abort', cancelled);
      this.permission_waiters.delete(waiter);
    }
  }
  private async stop_worker_unit(): Promise<void> {
    if (this.worker_unit_stop !== null) return this.worker_unit_stop;
    if (this.worker_unit === null) return;
    const attempt = this.worker_unit.stop().catch((cause) => {
      if (this.worker_unit_stop === attempt) this.worker_unit_stop = null;
      throw cause;
    });
    this.worker_unit_stop = attempt;
    return attempt;
  }
  private fail_writes(cause: unknown): void {
    for (const pending of this.pending_writes.splice(0)) pending.completed.reject(cause);
    for (const pending of this.active_writes) pending.reject(cause);
    this.active_writes.clear();
  }
  private break_writes(cause: unknown): void {
    this.stream_failure ??= cause instanceof Error ? cause : new Error(String(cause));
    this.fail_writes(this.stream_failure);
    this.release_permission_waiters(true);
  }
  async get_server_info(): Promise<Record<string, unknown> | null> {
    return this.info;
  }
  async query(prompt: string | AsyncIterable<SDKUserMessage>): Promise<void> {
    if (this.cli === null || this.cleanup_started)
      throw new Error('Not connected. Call connect() first.');
    if (this.stream_failure !== null) throw this.stream_failure;
    const send = async (message: SDKUserMessage) => {
      // The prompt iterator can suspend across a broken stdin or disconnect.
      if (this.cli === null || this.cleanup_started)
        throw new Error('Not connected. Call connect() first.');
      if (this.stream_failure !== null) throw this.stream_failure;
      const pending: PendingWrite = { uuid: message.uuid, completed: new Pending<void>() };
      this.pending_writes.push(pending);
      try {
        this.input.push({ ...message, session_id: message.session_id ?? 'default' });
      } catch (cause) {
        this.pending_writes.splice(this.pending_writes.indexOf(pending), 1);
        throw cause;
      }
      await pending.completed.promise;
    };
    if (typeof prompt === 'string') {
      await send({
        type: 'user',
        message: { role: 'user', content: prompt },
        parent_tool_use_id: null,
        session_id: 'default',
      });
    } else {
      for await (const message of prompt) await send(message);
    }
  }
  async *receive_messages(): AsyncIterable<ClaudeSdkMessage> {
    if (this.cli === null) throw new Error('Not connected. Call connect() first.');
    this.reader_started = true;
    try {
      for await (const message of this.cli) {
        const sequence =
          object(message) && ordered_message(message) ? ++this.delivered_message_count : null;
        try {
          if (object(message) && typeof message.type === 'string') {
            if (message.type === 'result') {
              const key = typeof message.uuid === 'string' ? message.uuid : null;
              const queued = this.raw_result_usage.get(key);
              const usage = queued?.shift();
              if (queued?.length === 0) this.raw_result_usage.delete(key);
              if (usage !== undefined) {
                const counts = Object.fromEntries(
                  ['input_tokens', 'output_tokens', 'cache_read_input_tokens']
                    .filter((field) => Object.hasOwn(usage, field))
                    .map((field) => [field, usage[field]]),
                );
                yield {
                  ...message,
                  usage: { ...(object(message.usage) ? message.usage : {}), ...counts },
                } as ClaudeSdkMessage;
                continue;
              }
            }
            yield message as ClaudeSdkMessage;
          }
        } finally {
          // Resumption means the adapter finished take_in for the previous yield.
          if (sequence !== null) this.processed_message_count = sequence;
          this.release_permission_waiters(false, object(message) && message.type === 'result');
        }
      }
      if (this.stream_failure !== null) throw this.stream_failure;
    } catch (cause) {
      throw this.stream_failure ?? native_exit_failure(cause);
    } finally {
      this.reader_started = false;
      this.release_permission_waiters(true);
      this.receipts.stream_ended();
      this.raw_result_usage.clear();
      await this.stop_worker_unit();
    }
  }
  watch_user_message(uuid: string): void {
    this.receipts.watch_user_message(uuid);
  }
  forget_user_message(uuid: string): void {
    this.receipts.forget_user_message(uuid);
  }
  wait_for_user_message_admission(uuid: string): Promise<boolean | null> {
    return this.receipts.wait_for_user_message_admission(uuid);
  }
  user_message_uuids_for_result(uuid: string | null): ReadonlySet<string> {
    return this.receipts.user_message_uuids_for_result(uuid);
  }
  user_message_is_settled(uuid: string): boolean {
    return this.receipts.user_message_is_settled(uuid);
  }
  wait_for_user_message_settlement(uuid: string): Promise<void> {
    return this.receipts.wait_for_user_message_settlement(uuid);
  }

  async interrupt_and_cancel_queued(): Promise<
    readonly [ReadonlySet<string>, ReadonlySet<string>]
  > {
    if (this.cli === null || this.cancel_attempt !== null)
      throw new PromptWriteFailed('claude did not confirm queued command cancellation');
    const attempt: CancelAttempt = { request_id: null, response: new Pending() };
    this.cancel_attempt = attempt;
    try {
      const native = this.cli as unknown as {
        interrupt(options: { cancelQueued: boolean }): Promise<unknown>;
      };
      const result = native.interrupt({ cancelQueued: true });
      // The SDK filters malformed UUIDs. The raw correlated payload is the proof.
      const failed = result.then(() => new Promise<never>(() => {}));
      const response = await bounded(
        Promise.race([attempt.response.promise, failed]),
        CLAUDE_CANCEL_QUEUED_TIMEOUT_SECONDS,
      );
      if (response.timed_out || response.value === null)
        throw new PromptWriteFailed('claude did not confirm queued command cancellation');
      return [
        cancellation_uuid_set(response.value.cancelled),
        cancellation_uuid_set(response.value.still_queued),
      ];
    } finally {
      if (attempt.request_id !== null) this.receipts.forget_control(attempt.request_id);
      if (this.cancel_attempt === attempt) this.cancel_attempt = null;
    }
  }
  async disconnect(): Promise<void> {
    this.cleanup_started = true;
    this.release_permission_waiters(true);
    this.stderr?.end();
    await this.stop_worker_unit();
    let close_failure: unknown;
    try {
      this.cli?.close();
    } catch (cause) {
      close_failure = cause;
    }
    this.input.end();
    this.fail_writes(new Error('Cannot write to closed transport'));
    // Startup can own a process before SDK query construction returns a handle.
    if (this.child !== null && !this.child.stdin.writableEnded) this.child.stdin.end();
    if (this.child_exit !== null) {
      let exited = await bounded(this.child_exit.promise, CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS);
      if (exited.timed_out && this.child !== null) {
        this.child.kill('SIGTERM');
        exited = await bounded(this.child_exit.promise, CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS);
      }
      if (exited.timed_out && this.child !== null) {
        this.child.kill('SIGKILL');
        exited = await bounded(this.child_exit.promise, CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS);
      }
      if (exited.timed_out) throw new Error('Claude process termination remains unconfirmed');
    }
    if (close_failure !== undefined) throw close_failure;
    if (this.cli !== null) await this.cli.return(undefined);
    this.receipts.clear();
    this.raw_result_usage.clear();
    this.cli = null;
    this.child = null;
    this.child_exit = null;
    this.worker_unit = null;
    this.worker_unit_stop = null;
    this.stderr = null;
  }
}

export function claude_sdk_client(options: ClaudeSdkOptions): ClaudeSdkClient {
  return new ObservedClaudeSdkClient(options);
}
