/** One Codex JSONL wire, correlated replies, and owned child lifetime. */
import { Buffer } from 'node:buffer';
import { copy_environment } from '../../../core/environment.ts';
import {
  is_json_record as object,
  parse_numeric_json,
  numeric_kind,
  numeric_value,
} from '../../../core/http/numeric_json.ts';
import { StringDecoder } from 'node:string_decoder';
import {
  BackendCliIncompatible,
  confirm_reported_cli_version,
  ensure_cli_compatible,
} from '../../../conversation/backends/cli_compatibility.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { WorkerSupervisor } from '../../../worker_supervision/service.ts';
import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import { local_process_identity } from '../../../worker_supervision/identity.ts';
import { STOP_SECONDS } from '../../../worker_supervision/config.ts';
import type { LocalChildIdentity } from '../../../conversation/local_child_contracts.ts';
import { WorkerScratch } from '../../../conversation/worker_scratch.ts';
import { CONSUMED_SERVER_NOTIFICATIONS, decode_server_request } from './protocol.ts';
import { OwnedProcess, spawn_owned_process } from '../../../lifecycle/owned_processes.ts';
import { observe_promise } from '../../../core/promise_observer.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions, OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import {
  CHILD_OUTPUT_LINE_LIMIT_BYTES,
  METHOD_NOT_FOUND_ERROR_CODE,
  PIPE_DRAIN_SECONDS,
  REQUEST_TIMEOUT_SECONDS,
  STANDARD_ERROR_RING_BUFFER_BYTES,
  STANDARD_ERROR_SETTLE_SECONDS,
} from './configuration.ts';
import {
  CodexAppServerError,
  CodexChildWouldNotStart,
  CodexRequestRejected,
  CodexWireFailed,
} from './errors.ts';
export * from './errors.ts';

type WireObject = Record<string, unknown>;
type Reader = { read(value: unknown): unknown };
export interface CodexServerMessageHandler {
  on_notification(method: string, notification: unknown): Promise<void>;
  on_server_request(method: string, request_id: unknown, params: unknown): Promise<void>;
  on_child_ended(): Promise<void>;
}
export interface PendingCodexRequest {
  readonly request_id: number;
  readonly promise: Promise<WireObject>;
}
interface Pending extends PendingCodexRequest {
  resolve(reply: WireObject): void;
  reject(error: unknown): void;
}
export interface CodexRequestOptions {
  poison_wire_on_timeout?: boolean;
  timeout_seconds?: number;
  signal?: AbortSignal;
}
export interface CodexClientStart {
  argv: readonly string[];
  environment: Readonly<Record<string, string>>;
  working_directory: string;
  worker_ticket_id?: string | null;
  supervision_reporter?: (path: string) => Promise<void>;
  custody_reporter?: (identity: LocalChildIdentity) => Promise<void>;
}

function error_text(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class CodexAppServerClient {
  private readonly child_processes: OwnedChildProcesses;
  private readonly handler: CodexServerMessageHandler;
  private readonly description: string;
  private readonly notifications: Readonly<Record<string, Reader>>;
  private readonly warning: (message: string) => void;
  private process: OwnedProcess | null = null;
  private supervisor: WorkerSupervisor | null = null;
  private scratch: WorkerScratch | null = null;
  private readonly worker_supervision: WorkerSupervisionRuntime | undefined;
  private request_id = 0;
  private start_task: Promise<void> | null = null;
  private cli_executable: string | null = null;
  private cli_check: AbortController | null = null;
  private readonly pending = new Map<number, Pending>();
  private wire_broken = false;
  private stopping = false;
  private incarnation = 0;
  private stop_task: Promise<void> | null = null;
  private stop_options: StopOptions | undefined;
  private readonly cleanup_settlements = new Set<Promise<void>>();
  private watcher: Promise<void> | null = null;
  private readonly watcher_settlements = new Set<Promise<boolean>>();
  private dispatch: Promise<void> = Promise.resolve();
  private writes: Promise<void> = Promise.resolve();
  private readonly active_writes = new Set<(error: unknown) => void>();
  private readonly stderr: Buffer[] = [];
  private stderr_bytes = 0;
  private stderr_ever = 0;
  private end_reason_value: string | null = null;
  constructor(options: {
    child_processes: OwnedChildProcesses;
    handler: CodexServerMessageHandler;
    description: string;
    consumed_notifications?: Readonly<Record<string, Reader>>;
    warning?: (message: string) => void;
    worker_supervision?: WorkerSupervisionRuntime;
  }) {
    this.child_processes = options.child_processes;
    this.worker_supervision = options.worker_supervision;
    this.handler = options.handler;
    this.description = options.description;
    this.notifications = options.consumed_notifications ?? CONSUMED_SERVER_NOTIFICATIONS;
    this.warning = options.warning ?? ((message) => console.warn(message));
  }
  get end_reason(): string | null {
    return this.end_reason_value;
  }
  get is_alive(): boolean {
    const child = this.process?.child;
    return (
      child !== undefined &&
      child.exitCode === null &&
      child.signalCode === null &&
      !this.wire_broken
    );
  }
  async start(options: CodexClientStart): Promise<void> {
    if (
      this.start_task !== null ||
      (this.process !== null &&
        this.process.child.exitCode === null &&
        this.process.child.signalCode === null) ||
      this.supervisor !== null ||
      this.scratch !== null
    )
      throw new CodexChildWouldNotStart('the previous codex child still owns cleanup handles');
    this.stopping = false;
    this.stop_task = null;
    this.stop_options = undefined;
    const incarnation = ++this.incarnation;
    const task = this.replace_dead_child(options, incarnation);
    this.start_task = task;
    try {
      await task;
    } finally {
      if (this.start_task === task) this.start_task = null;
    }
  }
  private async replace_dead_child(options: CodexClientStart, incarnation: number): Promise<void> {
    const previous = this.process;
    if (previous !== null || this.supervisor !== null || this.scratch !== null) {
      this.break_wire('the codex process ended');
      await this.cleanup(previous, this.supervisor, this.scratch);
      if (this.process === previous) this.process = null;
    }
    if (this.incarnation !== incarnation || this.stopping)
      throw new CodexChildWouldNotStart('the codex child stopped during startup');
    await this.start_owned(options, incarnation);
  }
  private async start_owned(options: CodexClientStart, incarnation: number): Promise<void> {
    const current = () => this.incarnation === incarnation && !this.stopping;
    const assert_current = () => {
      if (!current()) throw new CodexChildWouldNotStart('the codex child stopped during startup');
    };
    let environment = { ...options.environment };
    let owned: OwnedProcess | null = null;
    let supervisor: WorkerSupervisor | null = null;
    let scratch: WorkerScratch | null = null;
    try {
      if (options.worker_ticket_id && !this.worker_supervision)
        throw new CodexChildWouldNotStart('Worker supervision is unavailable');
      this.cli_executable = options.argv[0]!;
      await this.confirm_cli_compatible(options.argv[0]!, environment, options.working_directory);
      assert_current();
      if (options.worker_ticket_id) {
        environment = { ...environment, PLAN_TICKET_ID: options.worker_ticket_id };
        supervisor = await WorkerSupervisor.create(environment, options.working_directory, {
          runtime: this.worker_supervision!,
        });
        if (!supervisor)
          throw new CodexChildWouldNotStart('Worker supervision did not retain launch custody');
        this.supervisor = supervisor;
        scratch = await WorkerScratch.create(environment, { processes: this.child_processes });
        this.scratch = scratch;
        environment = scratch.environment;
        Object.assign(supervisor.environment, environment);
        assert_current();
        await options.supervision_reporter?.(supervisor.path);
      }
      assert_current();
      owned = await spawn_owned_process(supervisor?.argv(options.argv) ?? options.argv, {
        child_processes: this.child_processes,
        environment: supervisor?.environment ?? environment,
        working_directory: options.working_directory,
        on_created: (process) => {
          owned = process;
          this.process = process;
        },
      });
      this.process = owned;
      if (supervisor) {
        if (owned.child.pid === undefined)
          throw new CodexChildWouldNotStart('Worker process identity is unavailable');
        await supervisor.spawned(owned.child.pid);
        await supervisor.started(owned.child.pid);
      }
      if (options.custody_reporter) {
        const identity = await this.local_child_identity();
        if (identity === null)
          throw new CodexChildWouldNotStart('Cannot establish the Codex process identity');
        await options.custody_reporter(identity);
      }
      assert_current();
      this.dispatch = Promise.resolve();
      this.writes = Promise.resolve();
      this.wire_broken = false;
      this.stderr.length = 0;
      this.stderr_bytes = 0;
      this.stderr_ever = 0;
      this.end_reason_value = null;
      this.attach(owned, incarnation, supervisor, scratch);
    } catch (error) {
      if (supervisor && (owned === null || owned.child.pid === undefined))
        await supervisor.cancel_unspawned();
      await this.cleanup(owned, supervisor, scratch, this.stop_options);
      if (this.process === owned) this.process = null;
      throw new CodexChildWouldNotStart(error_text(error));
    }
  }
  async local_child_identity(): Promise<LocalChildIdentity | null> {
    if (!this.process?.child.pid) return null;
    if (this.supervisor) return this.supervisor.identity();
    return this.worker_supervision
      ? local_process_identity(this.process.child.pid, this.worker_supervision)
      : null;
  }
  private async cleanup(
    owned: OwnedProcess | null,
    supervisor: WorkerSupervisor | null,
    scratch: WorkerScratch | null,
    options?: StopOptions,
  ): Promise<void> {
    const deadline =
      options?.deadline ?? (this.worker_supervision?.clock() ?? monotonic_seconds()) + STOP_SECONDS;
    if (supervisor) {
      const result = await supervisor.stop(owned ? () => owned.stop({ deadline }) : undefined, {
        deadline,
      });
      const settled = supervisor.shutdown_settled;
      this.cleanup_settlements.add(settled);
      void settled.finally(() => this.cleanup_settlements.delete(settled)).catch(() => {});
      if (!result.complete)
        throw new CodexChildWouldNotStart(`Worker cleanup remains unproven: ${result.reason}`);
    } else if (owned) await owned.stop({ deadline });
    if (scratch) await scratch.close();
    if (this.supervisor === supervisor) this.supervisor = null;
    if (this.scratch === scratch) this.scratch = null;
  }
  /** Check the Codex feature floor. A kept verdict for the same file spawns nothing. */
  async confirm_cli_compatible(
    executable: string,
    environment: Readonly<Record<string, string>>,
    working_directory?: string,
  ): Promise<void> {
    const check = new AbortController();
    this.cli_check = check;
    const probes = new OneShotChildren(this.child_processes);
    let failure: { error: unknown } | null = null;
    try {
      await ensure_cli_compatible('codex', executable, {
        environment,
        working_directory,
        children: probes,
        signal: check.signal,
      });
    } catch (error) {
      // A stop kills the probe. Report the stop, not an unreadable version.
      failure = {
        error: check.signal.aborted
          ? new CodexChildWouldNotStart('the codex child stopped during startup')
          : error,
      };
    }
    if (this.cli_check === check) this.cli_check = null;
    probes.seal();
    try {
      await probes.settled();
    } catch (error) {
      failure ??= { error };
    }
    if (failure !== null) throw failure.error;
  }
  private attach(
    owned: OwnedProcess,
    incarnation: number,
    supervisor: WorkerSupervisor | null,
    scratch: WorkerScratch | null,
  ): void {
    const current = () =>
      this.process === owned && this.incarnation === incarnation && !this.stopping;
    const decoder = new StringDecoder('utf8');
    let fragments: string[] = [];
    let line_bytes = 0;
    const read = (text: string) => {
      if (!current()) return;
      let start = 0;
      while (start < text.length) {
        const newline = text.indexOf('\n', start);
        const fragment = text.slice(start, newline < 0 ? text.length : newline);
        line_bytes += Buffer.byteLength(fragment);
        if (line_bytes > CHILD_OUTPUT_LINE_LIMIT_BYTES) {
          this.warn('a line could not be read');
          fragments = [];
          line_bytes = 0;
          owned.child.stdout.destroy();
          return;
        }
        fragments.push(fragment);
        if (newline < 0) return;
        this.route_line(fragments.join(''), owned, incarnation);
        fragments = [];
        line_bytes = 0;
        start = newline + 1;
      }
    };
    owned.child.stdout.on('data', (chunk: Buffer) => read(decoder.write(chunk)));
    owned.child.stdout.once('end', () => {
      read(decoder.end());
      if (current() && fragments.length) this.route_line(fragments.join(''), owned, incarnation);
      fragments = [];
      line_bytes = 0;
    });
    let stderr_fragments: Buffer[] = [];
    let stderr_line_bytes = 0;
    owned.child.stderr.on('data', (chunk: Buffer) => {
      if (!current()) return;
      let start = 0;
      while (start < chunk.length) {
        const newline = chunk.indexOf(10, start);
        const fragment = chunk.subarray(start, newline < 0 ? chunk.length : newline + 1);
        stderr_fragments.push(fragment);
        stderr_line_bytes += fragment.length;
        if (stderr_line_bytes > CHILD_OUTPUT_LINE_LIMIT_BYTES) {
          stderr_fragments = [];
          stderr_line_bytes = 0;
          return;
        }
        if (newline < 0) return;
        this.keep_stderr(Buffer.concat(stderr_fragments, stderr_line_bytes));
        stderr_fragments = [];
        stderr_line_bytes = 0;
        start = newline + 1;
      }
    });
    owned.child.stderr.once('end', () => {
      if (current() && stderr_line_bytes)
        this.keep_stderr(Buffer.concat(stderr_fragments, stderr_line_bytes));
      stderr_fragments = [];
      stderr_line_bytes = 0;
    });
    void owned.output_failed.then((error) => {
      if (current()) this.warn(`a child output stream could not be read: ${error.message}`);
    });
    owned.child.stdin.on('error', (error) => {
      if (current()) this.break_wire(error_text(error));
    });
    const completion = this.watch(owned, incarnation, supervisor, scratch).catch((error) => {
      if (current()) {
        this.end_reason_value = error_text(error);
        this.break_wire(this.end_reason_value);
      }
      return false;
    });
    this.watcher_settlements.add(completion);
    void completion.finally(() => this.watcher_settlements.delete(completion)).catch(() => {});
    // Pipe and metadata work settles before the callback can reenter core cleanup.
    this.watcher = completion.then(async (notify) => {
      if (notify) await this.handler.on_child_ended();
    });
    void this.watcher.catch((error) => {
      if (!current()) return;
      this.end_reason_value = error_text(error);
      this.break_wire(this.end_reason_value);
    });
  }
  private async watch(
    owned: OwnedProcess,
    incarnation: number,
    supervisor: WorkerSupervisor | null,
    scratch: WorkerScratch | null,
  ): Promise<boolean> {
    const current = () =>
      this.process === owned && this.incarnation === incarnation && !this.stopping;
    await owned.exited;
    const stdout_done = owned.child.stdout.readableEnded
      ? Promise.resolve()
      : new Promise<void>((resolve) => owned.child.stdout.once('end', resolve));
    const stderr_done = owned.child.stderr.readableEnded
      ? Promise.resolve()
      : new Promise<void>((resolve) => owned.child.stderr.once('end', resolve));
    await observe_promise(Promise.all([stdout_done, stderr_done]), PIPE_DRAIN_SECONDS * 1000);
    let end_reason: string | null = null;
    try {
      await this.cleanup(owned, supervisor, scratch, this.stop_options);
    } catch (error) {
      end_reason = `Worker cleanup remains unproven: ${error_text(error)}`;
    }
    if (!current()) return false;
    await observe_promise(this.dispatch, PIPE_DRAIN_SECONDS * 1000);
    if (!current()) return false;
    this.end_reason_value = end_reason;
    this.break_wire(end_reason ?? 'the codex process ended');
    return true;
  }
  stop(options?: StopOptions): Promise<void> {
    if (this.stop_task === null) {
      this.stop_options = options ?? {
        deadline: (this.worker_supervision?.clock() ?? monotonic_seconds()) + STOP_SECONDS,
      };
      const task = this.stop_owned(this.stop_options);
      this.stop_task = task;
      void task.catch(() => {
        if (this.stop_task === task) this.stop_task = null;
      });
    }
    return options
      ? observe_shutdown(this.stop_task, options, 'Codex child stop exceeded its deadline')
      : this.stop_task;
  }
  async settled(): Promise<void> {
    await Promise.allSettled([
      ...(this.start_task ? [this.start_task] : []),
      ...(this.stop_task ? [this.stop_task] : []),
    ]);
    while (this.watcher_settlements.size || this.cleanup_settlements.size)
      await Promise.allSettled([...this.watcher_settlements, ...this.cleanup_settlements]);
  }
  private async stop_owned(options?: StopOptions): Promise<void> {
    this.stopping = true;
    this.incarnation += 1;
    this.break_wire('this child is being shut down');
    const startup = this.start_task;
    this.cli_check?.abort();
    if (startup !== null) {
      try {
        await startup;
      } catch {
        /* Startup owns and settles its cleanup. */
      }
    }
    const owned = this.process;
    await this.cleanup(owned, this.supervisor, this.scratch, options);
    // A core can call stop from its child-ended callback. Do not await that callback here.
    if (this.process === owned) this.process = null;
    this.watcher = null;
  }
  standard_error_tail(): string | null {
    return this.stderr.length ? Buffer.concat(this.stderr).toString('utf8') : null;
  }
  standard_error_bytes_so_far(): number {
    return this.stderr_ever;
  }
  standard_error_since(byte_count: number): string | null {
    const arrived = this.stderr_ever - byte_count;
    if (arrived <= 0 || !this.stderr.length) return null;
    return Buffer.concat(this.stderr).subarray(-arrived).toString('utf8') || null;
  }
  async settle_standard_error(): Promise<void> {
    if (this.process !== null && this.process.child.stderr.readable)
      await new Promise<void>((resolve) =>
        setTimeout(resolve, STANDARD_ERROR_SETTLE_SECONDS * 1000),
      );
  }
  private keep_stderr(chunk: Buffer): void {
    this.stderr_ever += chunk.length;
    this.stderr.push(chunk);
    this.stderr_bytes += chunk.length;
    while (this.stderr_bytes > STANDARD_ERROR_RING_BUFFER_BYTES && this.stderr.length > 1)
      this.stderr_bytes -= this.stderr.shift()!.length;
  }
  async request(
    method: string,
    params?: Readonly<WireObject>,
    options: CodexRequestOptions = {},
  ): Promise<unknown> {
    let attempted = false;
    return this.bounded_request(
      method,
      async (signal) => {
        const answer = await this.begin_request(method, params, {
          signal,
          on_write_attempt: () => {
            attempted = true;
          },
        });
        try {
          return this.unwrap_reply(method, await this.wait_answer(answer, signal));
        } finally {
          this.pending.delete(answer.request_id);
        }
      },
      options,
      () => attempted,
    ).then(async (result) => {
      if (method === 'initialize') await this.confirm_reported_version(result);
      return result;
    });
  }
  /** The `initialize` userAgent names the running version after its first slash. */
  private async confirm_reported_version(result: unknown): Promise<void> {
    if (this.cli_executable === null || !object(result) || typeof result.userAgent !== 'string')
      return;
    try {
      await confirm_reported_cli_version(
        'codex',
        this.cli_executable,
        /^[^/]*\/(\S+)/.exec(result.userAgent)?.[1],
      );
    } catch (error) {
      if (error instanceof BackendCliIncompatible) throw new CodexChildWouldNotStart(error.message);
      throw error;
    }
  }
  async begin_request(
    method: string,
    params?: Readonly<WireObject>,
    options: { signal?: AbortSignal; on_write_attempt?: () => void } = {},
  ): Promise<PendingCodexRequest> {
    options.signal?.throwIfAborted();
    const request_id = ++this.request_id;
    let resolve!: Pending['resolve'];
    let reject!: Pending['reject'];
    const promise = new Promise<WireObject>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    void promise.catch(() => {});
    const answer: Pending = { request_id, promise, resolve, reject };
    this.pending.set(request_id, answer);
    try {
      await this.write(
        { id: request_id, method, ...(params === undefined ? {} : { params }) },
        options.signal,
        options.on_write_attempt,
      );
    } catch (error) {
      this.pending.delete(request_id);
      reject(error);
      throw error;
    }
    return answer;
  }
  async finish_request(
    method: string,
    answer: PendingCodexRequest,
    options: CodexRequestOptions = {},
  ): Promise<unknown> {
    try {
      return await this.bounded_request(
        method,
        async (signal) => this.unwrap_reply(method, await this.wait_answer(answer, signal)),
        options,
        () => true,
      );
    } finally {
      this.pending.delete(answer.request_id);
    }
  }
  private async wait_answer(answer: PendingCodexRequest, signal: AbortSignal): Promise<WireObject> {
    signal.throwIfAborted();
    let abort: (() => void) | undefined;
    try {
      return await Promise.race([
        answer.promise,
        new Promise<never>((_, reject) => {
          abort = () => reject(signal.reason);
          signal.addEventListener('abort', abort, { once: true });
        }),
      ]);
    } finally {
      if (abort !== undefined) signal.removeEventListener('abort', abort);
      this.pending.delete(answer.request_id);
    }
  }
  private unwrap_reply(method: string, reply: WireObject): unknown {
    const error = reply.error;
    if (error !== null && error !== undefined) {
      throw new CodexRequestRejected(
        method,
        object(error) ? (numeric_value(error.code) ?? null) : null,
        object(error) ? String(error.message) : String(error),
        object(error) ? error.data : null,
        { request_id: String(numeric_value(reply.id) ?? reply.id) },
      );
    }
    return reply.result ?? null;
  }
  private async bounded_request<T>(
    method: string,
    work: (signal: AbortSignal) => Promise<T>,
    options: CodexRequestOptions,
    attempted: () => boolean,
  ): Promise<T> {
    const owned = this.process;
    const incarnation = this.incarnation;
    const current = () => this.process === owned && this.incarnation === incarnation;
    options.signal?.throwIfAborted();
    const cancel = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const external_abort = () => cancel.abort(options.signal!.reason);
    options.signal?.addEventListener('abort', external_abort, { once: true });
    const canceled = new Promise<never>((_, reject) => {
      cancel.signal.addEventListener('abort', () => reject(cancel.signal.reason), { once: true });
    });
    timer = setTimeout(
      () =>
        cancel.abort(
          new CodexWireFailed(`${method} was not answered`, {
            request_may_have_been_written: attempted(),
          }),
        ),
      (options.timeout_seconds ?? REQUEST_TIMEOUT_SECONDS) * 1000,
    );
    try {
      return await Promise.race([work(cancel.signal), canceled]);
    } catch (error) {
      if (
        cancel.signal.aborted &&
        !options.signal?.aborted &&
        current() &&
        (options.poison_wire_on_timeout ?? true)
      )
        this.wire_broken = true;
      throw error;
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', external_abort);
    }
  }
  async notify(method: string, params?: Readonly<WireObject>): Promise<void> {
    await this.write({ method, ...(params === undefined ? {} : { params }) });
  }
  async respond(request_id: unknown, result: Readonly<WireObject>): Promise<void> {
    await this.write({ id: request_id, result });
  }
  async respond_with_error(
    request_id: unknown,
    options: { code: number; message: string },
  ): Promise<void> {
    await this.write({ id: request_id, error: options });
  }
  private async write(
    message: WireObject,
    signal?: AbortSignal,
    on_write_attempt?: () => void,
  ): Promise<void> {
    const owned = this.process;
    const incarnation = this.incarnation;
    signal?.throwIfAborted();
    const line = JSON.stringify(message) + '\n';
    const task = this.writes.then(
      () =>
        new Promise<void>((resolve, reject) => {
          const child = owned?.child;
          if (signal?.aborted) {
            reject(signal.reason);
            return;
          }
          if (
            child === undefined ||
            this.process !== owned ||
            this.incarnation !== incarnation ||
            this.wire_broken ||
            child.stdin.destroyed
          ) {
            reject(new CodexWireFailed("this child's wire is not usable"));
            return;
          }
          const aborted = () => {
            this.active_writes.delete(failed);
            reject(signal!.reason);
          };
          const failed = (error: unknown) => {
            signal?.removeEventListener('abort', aborted);
            reject(error);
          };
          this.active_writes.add(failed);
          signal?.addEventListener('abort', aborted, { once: true });
          try {
            on_write_attempt?.();
            child.stdin.write(line, 'utf8', (error) => {
              this.active_writes.delete(failed);
              signal?.removeEventListener('abort', aborted);
              if (error !== null && error !== undefined) {
                if (this.process === owned && this.incarnation === incarnation)
                  this.break_wire(error.message);
                reject(new CodexWireFailed(error.message, { request_may_have_been_written: true }));
              } else resolve();
            });
          } catch (error) {
            this.active_writes.delete(failed);
            signal?.removeEventListener('abort', aborted);
            if (this.process === owned && this.incarnation === incarnation)
              this.break_wire(error_text(error));
            reject(new CodexWireFailed(error_text(error), { request_may_have_been_written: true }));
          }
        }),
    );
    this.writes = task.catch(() => {});
    return task;
  }
  private route_line(line: string, owned: OwnedProcess, incarnation: number): void {
    const current = () =>
      this.process === owned && this.incarnation === incarnation && !this.stopping;
    const task = this.dispatch.then(async () => {
      if (!current() || !line.trim()) return;
      let message: unknown;
      try {
        message = parse_numeric_json(line);
      } catch {
        this.warn('a line was not JSON');
        return;
      }
      if (!object(message)) {
        this.warn('a line was not a JSON object');
        return;
      }
      const method = message.method;
      if (typeof method === 'string') {
        await this.route_method(method, message);
        return;
      }
      const request_id = numeric_value(message.id);
      if (
        request_id !== undefined &&
        numeric_kind(message.id) === 'integer' &&
        Number.isSafeInteger(request_id) &&
        ('result' in message || 'error' in message)
      ) {
        const answer = this.pending.get(request_id);
        if (answer !== undefined) {
          this.pending.delete(request_id);
          answer.resolve(message);
        }
        return;
      }
      this.warn('a message was none of the three shapes');
    });
    this.dispatch = task.catch((error) => {
      if (current()) this.warn(error_text(error));
    });
  }
  private async route_method(method: string, message: WireObject): Promise<void> {
    if ('id' in message) {
      let decoded: unknown;
      try {
        decoded = decode_server_request(method, message.params ?? {});
      } catch (error) {
        this.warn(`the ${method} request did not decode: ${error_text(error)}`);
        await this.refuse(message.id, `${method} did not decode`);
        return;
      }
      if (decoded === null) {
        await this.refuse(message.id, `${method} is not a request this client answers`);
        return;
      }
      await this.handler.on_server_request(method, message.id, decoded);
    } else {
      if (!Object.hasOwn(this.notifications, method)) return;
      const reader = this.notifications[method]!;
      let decoded: unknown;
      try {
        decoded = reader.read(message.params ?? {});
      } catch (error) {
        this.warn(`the ${method} notification did not decode: ${error_text(error)}`);
        return;
      }
      await this.handler.on_notification(method, decoded);
    }
  }
  private async refuse(id: unknown, message: string): Promise<void> {
    try {
      await this.respond_with_error(id, { code: METHOD_NOT_FOUND_ERROR_CODE, message });
    } catch (error) {
      if (!(error instanceof CodexAppServerError)) throw error;
    }
  }
  private break_wire(why: string): void {
    this.wire_broken = true;
    for (const reject of this.active_writes)
      reject(new CodexWireFailed(why, { request_may_have_been_written: true }));
    this.active_writes.clear();
    for (const pending of this.pending.values())
      pending.reject(new CodexWireFailed(why, { request_may_have_been_written: true }));
    this.pending.clear();
  }
  private warn(detail: string): void {
    this.warning(`codex ${this.description}: ${detail}`);
  }
}
