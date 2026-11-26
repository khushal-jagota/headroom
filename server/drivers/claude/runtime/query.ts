/** The real SDK owns the protocol and reads stdout. Panels drains its messages once. */
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { copy_environment } from '../../../core/environment.ts';
import { Writable } from 'node:stream';
import { StringDecoder } from 'node:string_decoder';
import { CLAUDE_AGENT_SDK_VERSION, load_claude_sdk, PANELS_CLAUDE_CODE_ENTRYPOINT } from './sdk.ts';
import type {
  EffortLevel,
  Query,
  SDKUserMessage,
  SpawnedProcess,
  SpawnOptions,
} from '@anthropic-ai/claude-agent-sdk';
import { PromptWriteFailed } from '../../../conversation/backends/contracts.ts';
import { error_text } from '../../../conversation/backends/failure_summary.ts';
import {
  confirm_reported_cli_version,
  ensure_cli_compatible,
} from '../../../conversation/backends/cli_compatibility.ts';
import { WorkerSupervisor } from '../../../worker_supervision/index.ts';
import { WorkerScratch } from '../../../conversation/worker_scratch.ts';
import type { LocalChildIdentity } from '../../../conversation/local_child_contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import {
  CLAUDE_APPLY_REASONING_EFFORT_TIMEOUT_SECONDS,
  CLAUDE_CANCEL_QUEUED_TIMEOUT_SECONDS,
  CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS,
  CLAUDE_PROCESSED_TOOL_USE_ID_LIMIT,
  CLAUDE_STEER_ADMISSION_TIMEOUT_SECONDS,
} from './configuration.ts';
import type { ClaudeSdkClient, ClaudeSdkOptions, ClaudeSdkMessage } from './query_contracts.ts';
import { ClaudeReceipts } from './receipts.ts';
import { Deferred } from '../../../core/deferred.ts';
import { ObservedStderr, object, raw_uuid_set } from './wire.ts';
import { STOP_SECONDS } from '../../../worker_supervision/config.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { observe_promise } from '../../../core/promise_observer.ts';
import { OwnedProcess } from '../../../lifecycle/owned_processes.ts';

export type {
  ClaudeSdkClient,
  ClaudeSdkClientFactory,
  ClaudeSdkOptions,
  ClaudeSdkMessage,
} from './query_contracts.ts';

class InputQueue implements AsyncIterable<SDKUserMessage> {
  private readonly values: SDKUserMessage[] = [];
  private waiting: Deferred<IteratorResult<SDKUserMessage>> | null = null;
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
        this.waiting = new Deferred();
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
  completed: Deferred<void>;
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
  private readonly active_writes = new Set<Deferred<void>>();
  private cli: Query | null = null;
  private info: Record<string, unknown> | null = null;
  private owned_process: OwnedProcess | null = null;
  private stream_closed: Deferred<void> | null = null;
  private supervisor: WorkerSupervisor | null = null;
  private scratch: WorkerScratch | null = null;
  private worker_stop: Promise<void> | null = null;
  private registration: Promise<void> = Promise.resolve();
  private readonly registered = new Deferred<void>();
  private preparation: Promise<void> = Promise.resolve();
  private identity: LocalChildIdentity | null = null;
  private supervision_settlement: Promise<void> = Promise.resolve();
  private disconnect_task: Promise<void> | null = null;
  private connecting: Promise<void> | null = null;
  private stop_options: StopOptions | undefined;
  private revocation: Promise<void> | null = null;
  private stderr: ObservedStderr | null = null;
  private cleanup_started = false;
  private readonly compatibility_controller = new AbortController();
  private stream_failure: Error | null = null;
  private reader_started = false;
  // SDK messages drained eagerly, waiting for the adapter to take them in.
  private readonly drained_messages: ClaudeSdkMessage[] = [];
  private drained_message_ready: Deferred<void> | null = null;
  private drain_ended = false;
  private drain_failure: { cause: unknown } | null = null;
  // Tool uses whose assistant message the adapter has taken in.
  private readonly processed_tool_use_ids = new Set<string>();
  private readonly permission_waiters = new Set<{
    tool_use_id: string;
    ready: Deferred<boolean>;
  }>();

  constructor(options: ClaudeSdkOptions) {
    this.options = options;
  }
  connect(): Promise<void> {
    if (this.connecting) return this.connecting;
    const task = this.connect_owned();
    this.connecting = task;
    void task
      .finally(() => {
        if (this.connecting === task) this.connecting = null;
      })
      .catch(() => {});
    return task;
  }
  private async connect_owned(): Promise<void> {
    if (this.cleanup_started) throw new Error('a Claude transport cannot reconnect after cleanup');
    if (this.cli !== null) throw new Error('Claude transport is already connected');
    const overrides = copy_environment(this.options.env ?? {});
    await ensure_cli_compatible('claude', this.options.pathToClaudeCodeExecutable, {
      environment: overrides,
      working_directory: this.options.cwd,
      signal: this.compatibility_controller.signal,
      children: this.options.one_shot_children,
    });
    const environment = copy_environment(process.env);
    delete environment.CLAUDECODE;
    Object.assign(environment, overrides);
    // Report the Panels identity after explicit environment overrides.
    environment.CLAUDE_CODE_ENTRYPOINT = PANELS_CLAUDE_CODE_ENTRYPOINT;
    environment.CLAUDE_AGENT_SDK_VERSION = CLAUDE_AGENT_SDK_VERSION;
    if (this.options.cwd) environment.PWD = this.options.cwd;
    if (this.options.enableFileCheckpointing)
      environment.CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING = 'true';
    try {
      this.preparation = this.prepare_supervision(environment);
      await this.preparation;
      const { query: sdk_query } = await load_claude_sdk();
      if (this.cleanup_started) throw new Error('Claude startup stopped before SDK construction');
      // Ownership is internal composition data, never an unknown SDK option.
      const {
        child_processes: _native,
        one_shot_children: _probes,
        worker_supervision: _supervision,
        conversation_id: _conversation,
        worker_ticket_id: _ticket,
        custody_sink: _custody,
        ...sdk_options
      } = this.options;
      this.cli = sdk_query({
        prompt: this.input,
        options: {
          ...sdk_options,
          env: environment,
          canUseTool:
            this.options.canUseTool === undefined
              ? undefined
              : async (tool, input, permission) => {
                  if (
                    !(await this.wait_for_tool_use(permission.toolUseID, permission.signal)) ||
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
      this.drain_messages(this.cli);
      // The SDK can invoke its synchronous spawn callback after query() returns.
      const [info] = await Promise.all([this.cli.initializationResult(), this.registered.promise]);
      this.info = object(info) ? info : null;
      // The running process states its own version, which a wrapper or update can change.
      await confirm_reported_cli_version(
        'claude',
        this.options.pathToClaudeCodeExecutable,
        this.info?.claude_code_version,
      );
    } catch (cause) {
      await this.revoke_unspawned();
      await this.disconnect();
      throw native_exit_failure(cause);
    }
  }

  private revoke_unspawned(): Promise<void> {
    if (this.owned_process !== null || this.supervisor === null) return Promise.resolve();
    if (this.revocation === null) {
      const pending = this.supervisor.cancel_unspawned();
      this.revocation = pending;
      void pending.catch(() => {
        if (this.revocation === pending) this.revocation = null;
      });
    }
    return this.revocation;
  }
  /** Every launch writes its supervision record before spawn. Only a Worker gets scratch. */
  private async prepare_supervision(environment: Record<string, string>): Promise<void> {
    const supervisor = await WorkerSupervisor.create(
      environment,
      this.options.cwd ?? process.cwd(),
      {
        runtime: this.options.worker_supervision,
        conversation_id: this.options.conversation_id,
        worker_ticket_id: this.options.worker_ticket_id,
      },
    );
    this.supervisor = supervisor;
    Object.assign(environment, supervisor.environment);
    if (this.options.worker_ticket_id !== null) {
      this.scratch = await WorkerScratch.create(environment, {
        processes: this.options.child_processes,
      });
      Object.assign(environment, this.scratch.environment);
    }
    Object.assign(supervisor.environment, environment);
    await this.options.custody_sink?.record_local_child_supervision?.(supervisor.path);
  }
  private start_process(
    options: SpawnOptions,
    environment: Record<string, string>,
  ): SpawnedProcess {
    if (this.cleanup_started || this.owned_process !== null)
      throw new Error('a Claude transport cannot reconnect after cleanup');
    const supervisor = this.supervisor;
    if (supervisor === null) throw new Error('a Claude child cannot start without supervision');
    const command = supervisor.argv([options.command, ...options.args]);
    const child: ChildProcessWithoutNullStreams = this.options.child_processes.spawn(
      command[0]!,
      [...command.slice(1)],
      {
        cwd: options.cwd,
        env: environment,
        stdio: ['pipe', 'pipe', 'pipe'],
        detached: process.platform !== 'win32',
      },
    );
    const owned = new OwnedProcess(child);
    this.owned_process = owned;
    this.registration = this.register_process(owned, supervisor);
    void this.registration.then(
      () => this.registered.resolve(),
      (error: unknown) => this.registered.reject(error),
    );
    void owned.exited
      .then(async () => {
        await this.registration.catch(() => {});
        await this.stop_worker();
      })
      .catch((error: unknown) => {
        this.stream_failure ??= error instanceof Error ? error : new Error(String(error));
        this.fail_writes(this.stream_failure);
      });
    const stream_closed = new Deferred<void>();
    this.stream_closed = stream_closed;
    child.once('close', () => {
      stream_closed.resolve();
      // Receipts end with the drain. The SDK may still hold the final frames here.
      this.release_permission_waiters(true);
      this.fail_writes(new Error('Cannot write to closed transport'));
    });
    child.once('error', (cause) => {
      this.stream_failure ??= cause;
      this.fail_writes(cause);
    });
    child.stdout.on('error', (cause) => {
      this.stream_failure ??= cause;
    });
    const stderr = new ObservedStderr((line) => this.options.stderr?.(line));
    this.stderr = stderr;
    child.stderr.on('data', (chunk: Buffer) => stderr.write(chunk));
    child.stderr.on('end', () => stderr.end());
    child.stderr.on('error', () => stderr.end());
    child.stderr.on('close', () => stderr.end());
    const decoder = new StringDecoder('utf8');
    let outgoing = '';
    const stdin = new Writable({
      write: (chunk: Buffer, encoding, callback) => {
        let completed: Deferred<void> | null = null;
        try {
          outgoing += decoder.write(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, encoding));
          const lines = outgoing.split('\n');
          outgoing = lines.pop() ?? '';
          // Only Panels' own user frame completes its write.
          for (const line of this.pending_writes.length ? lines : []) {
            let frame: unknown;
            try {
              frame = JSON.parse(line);
            } catch {
              continue;
            }
            if (!object(frame) || frame.type !== 'user') continue;
            const uuid = typeof frame.uuid === 'string' ? frame.uuid : undefined;
            const index = this.pending_writes.findIndex((pending) => pending.uuid === uuid);
            if (index >= 0) completed = this.pending_writes.splice(index, 1)[0]!.completed;
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
      stdout: child.stdout,
      get killed() {
        return owned.killed;
      },
      get exitCode() {
        return child.exitCode;
      },
      get signalCode() {
        return child.signalCode;
      },
      kill: (signal) => owned.signal(signal),
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

  /** Read the SDK eagerly so receipts never wait for the adapter's take_in. */
  private drain_messages(cli: Query): void {
    void (async () => {
      try {
        for await (const message of cli) {
          if (this.drain_ended) return;
          if (!object(message) || typeof message.type !== 'string') continue;
          this.receipts.observe(message);
          this.drained_messages.push(message as ClaudeSdkMessage);
          this.wake_reader();
        }
      } catch (cause) {
        this.drain_failure = { cause };
      } finally {
        this.end_drain();
      }
    })();
  }
  private end_drain(): void {
    if (this.drain_ended) return;
    this.drain_ended = true;
    this.receipts.stream_ended();
    this.release_permission_waiters(true);
    this.wake_reader();
  }
  private wake_reader(): void {
    const ready = this.drained_message_ready;
    this.drained_message_ready = null;
    ready?.resolve();
  }
  /** Called when the adapter resumes the reader, so it has taken the message in. */
  private note_processed(message: ClaudeSdkMessage): void {
    if (message.type === 'result') {
      this.release_permission_waiters(false);
      return;
    }
    const content = object(message.message) ? message.message.content : undefined;
    if (message.type !== 'assistant' || !Array.isArray(content)) return;
    for (const block of content)
      if (object(block) && block.type === 'tool_use' && typeof block.id === 'string') {
        this.processed_tool_use_ids.add(block.id);
        // Most tool uses never ask. Forget the oldest beyond the bound.
        if (this.processed_tool_use_ids.size > CLAUDE_PROCESSED_TOOL_USE_ID_LIMIT)
          this.processed_tool_use_ids.delete(this.processed_tool_use_ids.values().next().value!);
      }
    for (const waiter of this.permission_waiters)
      if (this.processed_tool_use_ids.delete(waiter.tool_use_id)) waiter.ready.resolve(true);
  }
  private release_permission_waiters(closed: boolean): void {
    for (const waiter of this.permission_waiters) waiter.ready.resolve(!closed);
    // A result releases current waiters but keeps ids for asks that arrive after it.
    if (closed) this.processed_tool_use_ids.clear();
  }
  /** An ask waits until the adapter has taken in the assistant message holding its tool use. */
  private async wait_for_tool_use(tool_use_id: string, signal: AbortSignal): Promise<boolean> {
    if (this.cleanup_started || signal.aborted) return false;
    if (!this.reader_started || this.processed_tool_use_ids.delete(tool_use_id)) return true;
    const waiter = { tool_use_id, ready: new Deferred<boolean>() };
    const cancelled = () => waiter.ready.resolve(false);
    this.permission_waiters.add(waiter);
    signal.addEventListener('abort', cancelled, { once: true });
    try {
      const ready = await observe_promise(
        waiter.ready.promise,
        CLAUDE_STEER_ADMISSION_TIMEOUT_SECONDS * 1000,
      );
      return ready.completed ? ready.value : !this.cleanup_started && !signal.aborted;
    } finally {
      signal.removeEventListener('abort', cancelled);
      this.permission_waiters.delete(waiter);
    }
  }
  private async register_process(owned: OwnedProcess, supervisor: WorkerSupervisor): Promise<void> {
    const pid = owned.child.pid;
    if (pid === undefined) throw new Error('Claude process PID is unavailable');
    await supervisor.spawned(pid);
    await supervisor.started(pid);
    this.identity = await supervisor.identity();
    if (this.options.custody_sink?.record_local_child_custody) {
      if (this.identity === null) throw new Error('Cannot establish the Claude process identity');
      await this.options.custody_sink.record_local_child_custody(this.identity);
    }
  }
  async local_child_identity(): Promise<LocalChildIdentity | null> {
    await this.registration;
    return this.identity;
  }
  private stop_worker(options?: StopOptions): Promise<void> {
    if (this.worker_stop !== null) return this.worker_stop;
    if (this.supervisor === null && this.scratch === null) return Promise.resolve();
    const budget = options ?? this.stop_options ?? { deadline: monotonic_seconds() + STOP_SECONDS };
    const attempt = (async () => {
      const supervisor = this.supervisor;
      if (supervisor !== null) {
        const result = await supervisor.stop(() => this.stop_native(budget), budget);
        this.supervision_settlement = supervisor.shutdown_settled;
        if (!result.complete) throw new Error(result.reason);
        this.supervisor = null;
      }
      await this.scratch?.close();
      this.scratch = null;
    })();
    this.worker_stop = attempt;
    void attempt.catch(() => {
      if (this.worker_stop === attempt) this.worker_stop = null;
    });
    return attempt;
  }
  async settled(): Promise<void> {
    await this.connecting?.catch(() => {});
    await this.preparation.catch(() => {});
    await this.registration.catch(() => {});
    await this.disconnect_task?.catch(() => {});
    await this.worker_stop?.catch(() => {});
    await this.supervisor?.shutdown_settled;
    await this.supervision_settlement;
    await this.owned_process?.exited;
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
      const pending: PendingWrite = { uuid: message.uuid, completed: new Deferred<void>() };
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
      for (;;) {
        const message = this.drained_messages.shift();
        if (message === undefined) {
          if (this.drain_ended) break;
          this.drained_message_ready = new Deferred();
          await this.drained_message_ready.promise;
          continue;
        }
        try {
          yield message;
        } finally {
          // Resumption means the adapter finished take_in for this message.
          this.note_processed(message);
        }
      }
      if (this.drain_failure !== null)
        throw this.stream_failure ?? native_exit_failure(this.drain_failure.cause);
      if (this.stream_failure !== null) throw this.stream_failure;
    } finally {
      this.reader_started = false;
      this.release_permission_waiters(true);
      await this.stop_worker();
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
    const unconfirmed = () =>
      new PromptWriteFailed('claude did not confirm queued command cancellation');
    if (this.cli === null) throw unconfirmed();
    // Public types omit the option. The SDK sends cancel_queued and returns the receipt.
    const native = this.cli as unknown as {
      interrupt(options: { cancelQueued: true }): Promise<unknown>;
    };
    let receipt: { completed: true; value: unknown } | { completed: false };
    try {
      receipt = await observe_promise(
        native.interrupt({ cancelQueued: true }),
        CLAUDE_CANCEL_QUEUED_TIMEOUT_SECONDS * 1000,
      );
    } catch (error) {
      throw new PromptWriteFailed(
        `claude did not confirm queued command cancellation: ${error_text(error)}`,
        { cause: error },
      );
    }
    // Only a receipt naming both lists proves what left the queue.
    if (
      !receipt.completed ||
      !object(receipt.value) ||
      !Array.isArray(receipt.value.cancelled) ||
      !Array.isArray(receipt.value.still_queued)
    )
      throw unconfirmed();
    return [raw_uuid_set(receipt.value.cancelled), raw_uuid_set(receipt.value.still_queued)];
  }
  async apply_reasoning_effort(level: EffortLevel): Promise<void> {
    if (this.cli === null || this.cleanup_started)
      throw new Error('Not connected. Call connect() first.');
    const applied = await observe_promise(
      this.cli.applyFlagSettings({ effortLevel: level }),
      CLAUDE_APPLY_REASONING_EFFORT_TIMEOUT_SECONDS * 1000,
    );
    if (!applied.completed) throw new Error('claude did not confirm the reasoning effort change');
  }
  disconnect(options?: StopOptions): Promise<void> {
    if (this.disconnect_task === null) {
      this.stop_options = options ?? { deadline: monotonic_seconds() + STOP_SECONDS };
      const task = this.disconnect_owned(this.stop_options);
      this.disconnect_task = task;
      void task.catch(() => {
        if (this.disconnect_task === task) this.disconnect_task = null;
      });
    }
    return options === undefined
      ? this.disconnect_task
      : observe_shutdown(this.disconnect_task, options, 'Claude cleanup remains unconfirmed');
  }
  private async disconnect_owned(options?: StopOptions): Promise<void> {
    this.cleanup_started = true;
    this.compatibility_controller.abort();
    this.release_permission_waiters(true);
    this.stderr?.end();
    await this.preparation.catch(() => {});
    await this.registration.catch(() => {});
    await this.revoke_unspawned();
    await this.stop_worker(options);
    await this.stop_native(options);
    if (this.cli !== null) await this.cli.return(undefined);
    this.end_drain();
    this.receipts.clear();
    this.cli = null;
    this.owned_process = null;
    this.stream_closed = null;
    this.stderr = null;
  }
  private async stop_native(options?: StopOptions): Promise<void> {
    const deadline =
      options?.deadline ?? this.stop_options?.deadline ?? monotonic_seconds() + STOP_SECONDS;
    let close_failure: unknown;
    try {
      this.cli?.close();
    } catch (cause) {
      close_failure = cause;
    }
    this.input.end();
    this.fail_writes(new Error('Cannot write to closed transport'));
    // Startup can own a process before SDK query construction returns a handle.
    const owned = this.owned_process;
    if (owned !== null) {
      if (!owned.child.stdin.writableEnded) owned.child.stdin.end();
      const exited = await observe_promise(
        owned.exited,
        Math.max(
          0,
          Math.min(CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS, deadline - monotonic_seconds()),
        ) * 1000,
      );
      if (!exited.completed)
        await owned.stop({
          grace_seconds: CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS,
          kill_wait_seconds: CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS,
          preserve_output: true,
          deadline,
        });
      if (this.stream_closed !== null) {
        const drained = await observe_promise(
          this.stream_closed.promise,
          Math.max(
            0,
            Math.min(CLAUDE_PROCESS_CLOSE_TIMEOUT_SECONDS, deadline - monotonic_seconds()),
          ) * 1000,
        );
        if (!drained.completed) {
          owned.child.stdout.destroy();
          owned.child.stderr.destroy();
          throw new Error('Claude output drain remains unconfirmed');
        }
      }
    }
    if (close_failure !== undefined) throw close_failure;
  }
}

export function claude_sdk_client(options: ClaudeSdkOptions): ClaudeSdkClient {
  return new ObservedClaudeSdkClient(options);
}
