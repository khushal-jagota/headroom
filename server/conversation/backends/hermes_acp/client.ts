/** ACP owns framing, validation, correlation, and cancellation. This layer owns ordering. */
import { Readable } from 'node:stream';
import { StringDecoder } from 'node:string_decoder';
import { ndJsonStream, RequestError } from '@agentclientprotocol/sdk/unstable';
import type {
  AnyMessage,
  JsonRpcId,
  Stream,
  SessionNotification,
  RequestPermissionRequest,
  RequestPermissionResponse,
} from '@agentclientprotocol/sdk/unstable';
import type * as Rpc from '../../../node_modules/@agentclientprotocol/sdk/dist/core/jsonrpc.js';
import type * as Validators from '../../../node_modules/@agentclientprotocol/sdk/dist/v1/unstable/schema/zod.gen.js';
import type { BackendEventSink } from '../contracts.ts';
import { WorkerSupervisor } from '../../../worker_supervision/index.ts';
import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import { local_process_identity } from '../../../worker_supervision/identity.ts';
import { WorkerScratch } from '../../worker_scratch.ts';
import type { LocalChildIdentity } from '../../local_child_contracts.ts';
import { BackendAdapterError, BackendSpawnFailed } from '../contracts.ts';
import { spawn_owned_process } from '../../../lifecycle/owned_processes.ts';
import { STOP_SECONDS } from '../../../worker_supervision/config.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import { observe_shutdown } from '../../shutdown.ts';
import { observe_promise } from '../../../core/promise_observer.ts';
import type { OwnedProcess } from '../../../lifecycle/owned_processes.ts';
import type { ResolvedConversationStart } from '../../contracts.ts';
import { child_environment } from './launch.ts';
import { require_strict_session_notification } from './strict_updates.ts';
import type { AcpChildLaunch } from './launch.ts';
import type { OwnedChildProcesses, StopOptions } from '../../../lifecycle/contracts.ts';
import {
  CHILD_OUTPUT_LINE_LIMIT_BYTES,
  STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS,
  ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS,
} from './configuration.ts';
import { Deferred } from '../../../core/deferred.ts';

// 2.6.0's public ClientApp starts handlers without awaiting them, and replies bypass
// its handlers. Use the official lower-level middleware and schemas from this exact
// pin, with an admission gate before the SDK can read the next frame. No JSON-RPC
// correlation or protocol parser is implemented here. Tests pin these internal APIs.
// The package exports neither Connection nor its generated validators. Relative
// node_modules imports above preserve their exact official types without a second
// handwritten protocol interface. An SDK upgrade requires a new review of this file.
const sdk_root = import.meta.resolve('@agentclientprotocol/sdk/unstable');
const rpc = (await import(new URL('../../core/jsonrpc.js', sdk_root).href)) as typeof Rpc;
const validators = (await import(
  new URL('./schema/zod.gen.js', sdk_root).href
)) as typeof Validators;

export interface HermesAcpHandlers {
  session_update?(params: SessionNotification): Promise<void>;
  /** Admission completes after the ask reaches the core. The answer stays parked. */
  request_permission?(
    params: RequestPermissionRequest,
    id: JsonRpcId,
  ): Promise<{ answer: Promise<RequestPermissionResponse> }>;
  log?(error: unknown): void;
}
export interface AcpRequest<T> {
  readonly written: Promise<void>;
  readonly response: Promise<T>;
  cancel(reason?: unknown): void;
}
export interface AcpRequestOptions {
  timeout_ms?: number;
  signal?: AbortSignal;
  /** Complete caller-owned reply effects before the next inbound frame. Raw response stays independent. */
  after_response?: () => Promise<void>;
}
interface WriteReceipt {
  method: string;
  params: unknown;
  request: boolean;
  pending: Deferred<void>;
  effect: ResponseEffect;
}
interface ResponseEffect {
  readonly pending: Deferred<void>;
  completed: boolean;
  id?: JsonRpcId;
}

export class HermesAcpClient {
  readonly closed: Promise<void>;
  private readonly connection: Rpc.Connection;
  private readonly receipts = new Set<WriteReceipt>();
  private readonly response_effects = new Map<JsonRpcId, ResponseEffect>();
  private readonly response_receipts = new Map<JsonRpcId, Deferred<void>>();
  private readonly active_answers = new Set<Deferred<never>>();
  private failure: unknown = null;
  private gate: Deferred<void> | null = null;
  private stopped = false;
  private readonly handlers: HermesAcpHandlers;
  constructor(stream: Stream, handlers: HermesAcpHandlers = {}) {
    this.handlers = handlers;
    const reader = stream.readable.getReader();
    const writer = stream.writable.getWriter();
    const ordered: Stream = {
      readable: new ReadableStream<AnyMessage>(
        {
          pull: async (controller) => {
            try {
              if (this.gate) await this.gate.promise;
              if (this.stopped) {
                controller.close();
                return;
              }
              const next = await reader.read();
              if (next.done) {
                controller.close();
                return;
              }
              const message = next.value;
              if (rpc.isRequestMessage(message) || rpc.isNotificationMessage(message))
                this.gate = new Deferred<void>();
              else
                this.gate =
                  'id' in message ? (this.response_effects.get(message.id)?.pending ?? null) : null;
              controller.enqueue(message);
            } catch (error) {
              controller.error(error);
              if (!this.stopped) this.log(error);
              this.fail(error);
            }
          },
          cancel: (reason) => {
            this.gate?.resolve();
            return reader.cancel(reason);
          },
        },
        { highWaterMark: 0 },
      ),
      writable: new WritableStream<AnyMessage>({
        write: async (message) => {
          this.require_wire();
          try {
            let receipt: WriteReceipt | undefined;
            if ('method' in message) {
              receipt = [...this.receipts].find(
                (candidate) =>
                  candidate.method === message.method &&
                  candidate.params === message.params &&
                  candidate.request === 'id' in message,
              );
              if (receipt && 'id' in message && !receipt.effect.completed) {
                // Observe only the official SDK-generated id. The SDK still owns
                // correlation and response parsing. The write receipt remains later.
                receipt.effect.id = message.id;
                this.response_effects.set(message.id, receipt.effect);
              }
            }
            await writer.write(message);
            if (receipt) {
              this.receipts.delete(receipt);
              receipt.pending.resolve();
            }
            if (!('method' in message)) {
              this.response_receipts.get(message.id)?.resolve();
              this.response_receipts.delete(message.id);
            }
          } catch (error) {
            this.fail(error);
            throw error;
          }
        },
        abort: (reason) => {
          this.fail(reason);
        },
      }),
    };
    const handler: Rpc.JsonRpcHandler = {
      handleMessage: async (message) => {
        // Capture this frame's gate before an answer or callback yields control.
        const admitted = this.gate;
        try {
          if (message.kind === 'notification') {
            if (message.method === 'session/update') {
              require_strict_session_notification(message.params);
              const params = validators.zSessionNotification.parse(message.params);
              await this.handlers.session_update?.(params);
            }
          } else if (
            message.method === 'session/request_permission' &&
            this.handlers.request_permission
          ) {
            const params = validators.zRequestPermissionRequest.parse(message.params);
            const parked = await this.handlers.request_permission(params, message.responder.id);
            const interrupted = new Deferred<never>();
            this.active_answers.add(interrupted);
            void Promise.race([parked.answer, interrupted.promise])
              .then((answer) => message.responder.respond(answer))
              .catch((error) => {
                if (!this.stopped) this.log(error);
              })
              .finally(() => this.active_answers.delete(interrupted));
          } else if (message.kind === 'request') {
            await message.responder.respondWithError(RequestError.methodNotFound(message.method));
          }
          return rpc.Handled.yes();
        } catch (error) {
          // Schema failures still release admission. The SDK owns the error response
          // for requests; malformed notifications are logged and dropped.
          this.log(error);
          if (message.kind === 'notification') return rpc.Handled.yes();
          throw error;
        } finally {
          admitted?.resolve();
        }
      },
    };
    this.connection = rpc.Connection.builder().withHandler(handler).connect(ordered);
    this.closed = this.connection.closed;
    void this.closed.then(() =>
      this.fail(this.connection.signal.reason ?? new Error('the backend process ended')),
    );
  }
  get signal(): AbortSignal {
    return this.connection.signal;
  }
  private async wait_for_permission_write<T>(promise: Promise<T>): Promise<T> {
    const answer = await observe_promise(promise, ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS * 1000);
    if (!answer.completed) throw new Error('the permission answer did not reach the wire');
    return answer.value;
  }
  private log(error: unknown): void {
    try {
      this.handlers.log?.(error);
    } catch {
      /* Diagnostic failure must not block cleanup. */
    }
  }
  private require_wire(): void {
    if (this.failure !== null) throw this.failure;
    if (this.stopped) throw new Error('this child is being shut down');
    if (this.connection?.signal.aborted) throw this.connection.signal.reason;
  }
  private fail(error: unknown): void {
    if (this.failure !== null) return;
    this.failure = error ?? new Error('the ACP wire ended');
    for (const receipt of this.receipts) receipt.pending.reject(this.failure);
    this.receipts.clear();
    for (const effect of this.response_effects.values()) effect.pending.resolve();
    this.response_effects.clear();
    for (const receipt of this.response_receipts.values()) receipt.reject(this.failure);
    this.response_receipts.clear();
    for (const answer of this.active_answers) answer.reject(this.failure);
    this.active_answers.clear();
    this.gate?.resolve();
    this.connection?.close(this.failure);
  }
  begin_request<T>(
    method: string,
    params: unknown,
    options: AcpRequestOptions = {},
  ): AcpRequest<T> {
    const written = new Deferred<void>();
    const effect: ResponseEffect = { pending: new Deferred<void>(), completed: false };
    const receipt = { method, params, request: true, pending: written, effect };
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cancel = (reason?: unknown) => controller.abort(reason);
    const aborted = () => cancel(options.signal?.reason);
    try {
      this.require_wire();
      this.receipts.add(receipt);
      options.signal?.addEventListener('abort', aborted, { once: true });
      if (options.signal?.aborted) aborted();
      if (options.timeout_ms !== undefined)
        timer = setTimeout(
          () => cancel(new Error('the ACP request timed out')),
          options.timeout_ms,
        );
      const response_schema = (
        {
          initialize: validators.zInitializeResponse,
          'session/new': validators.zNewSessionResponse,
          'session/load': validators.zLoadSessionResponse,
          'session/set_mode': validators.zSetSessionModeResponse,
          'session/set_config_option': validators.zSetSessionConfigOptionResponse,
          'session/prompt': validators.zPromptResponse,
        } as Record<string, { parse(value: unknown): unknown }>
      )[method];
      const response = this.connection
        .sendRequest<unknown, T>(
          method,
          params,
          (value) => (response_schema ? response_schema.parse(value) : value) as T,
          { cancellationSignal: controller.signal, stopWaitingOnCancel: true },
        )
        .finally(() => {
          clearTimeout(timer);
          options.signal?.removeEventListener('abort', aborted);
        });
      // A cancellation can precede the actual request write. Keep its write
      // receipt independent: the SDK still sends that request and its cancellation.
      // Do not put effects behind response itself: turn ending awaits that raw
      // promise, while this separate barrier awaits the completed ending.
      const finish_effect = () => options.after_response?.();
      void response
        .then(finish_effect, finish_effect)
        .catch((error) => {
          if (!this.stopped) this.log(error);
        })
        .finally(() => {
          effect.completed = true;
          effect.pending.resolve();
          if (effect.id !== undefined) this.response_effects.delete(effect.id);
        });
      return { written: written.promise, response, cancel };
    } catch (error) {
      written.reject(error);
      const response = Promise.reject<T>(error);
      void response.catch(() => {});
      return { written: written.promise, response, cancel };
    }
  }
  request<T>(method: string, params: unknown, options?: AcpRequestOptions): Promise<T> {
    return this.begin_request<T>(method, params, options).response;
  }
  async notify(method: string, params: unknown): Promise<void> {
    this.require_wire();
    await this.connection.sendNotification(method, params);
  }
  /** Register before the permission handler releases its parked answer. */
  response_written(id: JsonRpcId): Promise<void> {
    this.require_wire();
    let receipt = this.response_receipts.get(id);
    if (!receipt) {
      receipt = new Deferred<void>();
      this.response_receipts.set(id, receipt);
    }
    return this.wait_for_permission_write(receipt.promise).finally(() =>
      this.response_receipts.delete(id),
    );
  }
  async stop(): Promise<void> {
    this.close();
    await this.closed;
  }
  close(reason: unknown = new Error('this child is being shut down')): void {
    this.stopped = true;
    this.fail(reason);
  }
}

/** Launch ownership exists before startup, so shutdown can seal an in-flight spawn. */
export class HermesAcpLauncher {
  private readonly child_processes: OwnedChildProcesses;
  private readonly worker_supervision: WorkerSupervisionRuntime | undefined;
  private readonly custody_sink: BackendEventSink | undefined;
  constructor(
    child_processes: OwnedChildProcesses,
    worker_supervision?: WorkerSupervisionRuntime,
    custody_sink?: BackendEventSink,
  ) {
    this.child_processes = child_processes;
    this.worker_supervision = worker_supervision;
    this.custody_sink = custody_sink;
  }
  private supervisor: WorkerSupervisor | null = null;
  private scratch: WorkerScratch | null = null;
  private identity: LocalChildIdentity | null = null;
  private registration: Promise<void> = Promise.resolve();
  private cleanup: Promise<void> | null = null;
  private supervision_settlement: Promise<void> = Promise.resolve();
  private sealed = false;
  private owned: OwnedProcess | null = null;
  private client: HermesAcpClient | null = null;
  private starting: Promise<HermesAcpClient> | null = null;
  private stopping: Promise<void> | null = null;
  private stop_options: StopOptions | undefined;
  private readonly error_lines: string[] = [];
  private error_line_characters = 0;
  private error_partial = '';
  private error_line_bytes = 0;
  private stderr_reader_failure: unknown = null;
  private stderr_failure_reported = false;
  private cleanup_log: HermesAcpHandlers['log'];
  private cleanup_failure_reported = false;
  start(
    launch: AcpChildLaunch,
    resolved_start: ResolvedConversationStart,
    handlers: HermesAcpHandlers = {},
  ): Promise<HermesAcpClient> {
    if (this.sealed) return Promise.reject(new BackendSpawnFailed('this child is being shut down'));
    if (this.starting) return this.starting;
    this.cleanup_log = handlers.log;
    this.starting = this.spawn(launch, resolved_start, handlers);
    return this.starting;
  }
  private async spawn(
    launch: AcpChildLaunch,
    resolved_start: ResolvedConversationStart,
    handlers: HermesAcpHandlers,
  ): Promise<HermesAcpClient> {
    try {
      const environment = child_environment(launch, resolved_start);
      const identity = Object.fromEntries(
        resolved_start.role_materials?.identity_environment_variables ?? [],
      );
      if (identity.PLAN_TICKET_ID) {
        if (!this.worker_supervision) throw new Error('Worker supervision is required');
        this.supervisor = await WorkerSupervisor.create(
          environment,
          resolved_start.workspace_folder,
          {
            runtime: this.worker_supervision,
          },
        );
        if (this.supervisor !== null) Object.assign(environment, this.supervisor.environment);
        this.scratch = await WorkerScratch.create(environment, { processes: this.child_processes });
        Object.assign(environment, this.scratch.environment);
        if (this.supervisor !== null) {
          Object.assign(this.supervisor.environment, environment);
          await this.custody_sink?.record_local_child_supervision?.(this.supervisor.path);
        }
      }
      if (this.sealed) throw new Error('this child is being shut down');
      const argv = this.supervisor?.argv(launch.argv) ?? launch.argv;
      const owned = await spawn_owned_process(argv, {
        child_processes: this.child_processes,
        on_created: (owned) => {
          this.owned = owned;
          if (owned.child.pid !== undefined && this.supervisor !== null) {
            this.registration = this.supervisor.spawned(owned.child.pid);
            void this.registration.catch(() => {});
          }
        },
        environment,
        working_directory: resolved_start.workspace_folder,
      });
      this.owned = owned;
      await this.registration;
      if (owned.child.pid === undefined && this.supervisor !== null)
        throw new Error('Worker process PID is unavailable');
      if (owned.child.pid !== undefined) {
        if (this.supervisor !== null) {
          await this.supervisor.started(owned.child.pid);
          this.identity = await this.supervisor.identity();
        } else if (this.worker_supervision) {
          this.identity = await local_process_identity(owned.child.pid, this.worker_supervision);
        }
      }
      if (this.custody_sink?.record_local_child_custody) {
        if (this.identity === null) throw new Error('Cannot establish the Hermes process identity');
        await this.custody_sink.record_local_child_custody(this.identity);
      }
      if (this.sealed) throw new Error('this child is being shut down');
      const decoder = new StringDecoder('utf8');
      const keep_stderr = (text: string, ended = false) => {
        this.error_partial += text;
        let newline: number;
        while ((newline = this.error_partial.indexOf('\n')) !== -1) {
          this.keep_error_line(this.error_partial.slice(0, newline + 1));
          this.error_partial = this.error_partial.slice(newline + 1);
        }
        if (ended && this.error_partial) {
          this.keep_error_line(this.error_partial);
          this.error_partial = '';
        }
      };
      const fail_stderr_reader = (error: unknown) => {
        if (this.stderr_reader_failure !== null) return;
        this.stderr_reader_failure = error;
        this.error_partial = '';
        this.error_line_bytes = 0;
        owned.child.stderr.pause();
      };
      owned.child.stderr.on('data', (chunk: Buffer) => {
        if (this.stderr_reader_failure !== null) return;
        try {
          let cursor = 0;
          while (cursor < chunk.length) {
            const newline = chunk.indexOf(10, cursor);
            const end = newline === -1 ? chunk.length : newline;
            const bytes = end - cursor;
            if (this.error_line_bytes + bytes > CHILD_OUTPUT_LINE_LIMIT_BYTES) {
              fail_stderr_reader(
                new Error(
                  newline === -1
                    ? 'Separator is not found, and chunk exceed the limit'
                    : 'Separator is found, but chunk is longer than limit',
                ),
              );
              return;
            }
            this.error_line_bytes = newline === -1 ? this.error_line_bytes + bytes : 0;
            const through = newline === -1 ? end : end + 1;
            keep_stderr(decoder.write(chunk.subarray(cursor, through)));
            cursor = through;
          }
        } catch (error) {
          fail_stderr_reader(error);
        }
      });
      owned.child.stderr.on('end', () => {
        if (this.stderr_reader_failure !== null) return;
        try {
          keep_stderr(decoder.end(), true);
        } catch (error) {
          fail_stderr_reader(error);
        }
      });
      const output = new WritableStream<Uint8Array>({
        write: (chunk) =>
          new Promise<void>((resolve, reject) => {
            const failed = (error: Error) => {
              owned.child.stdin.removeListener('error', failed);
              reject(error);
            };
            owned.child.stdin.once('error', failed);
            owned.child.stdin.write(chunk, (error) => {
              owned.child.stdin.removeListener('error', failed);
              if (error) reject(error);
              else resolve();
            });
          }),
      });
      const input = Readable.toWeb(owned.child.stdout) as ReadableStream<Uint8Array>;
      const client = new HermesAcpClient(
        ndJsonStream(output, input, { maxMessageBytes: CHILD_OUTPUT_LINE_LIMIT_BYTES }),
        handlers,
      );
      this.client = client;
      // stdout can still contain a final reply when the direct child exits. Let
      // the SDK drain it in order, with a bound for pipes held by descendants.
      void owned.exited
        .then(async () => {
          if (
            !(await observe_promise(client.closed, ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS * 1000))
              .completed
          )
            client.close(new Error('the backend process ended'));
          await this.stop_owned();
        })
        .catch((error: unknown) => {
          client.close(error);
        });
      void owned.output_failed.then((error) => {
        client.close(error);
        try {
          handlers.log?.(error);
        } catch {
          /* Cleanup precedes diagnostics. */
        }
      });
      return client;
    } catch (error) {
      try {
        await this.registration.catch(() => {});
        if (this.owned?.child.pid === undefined) await this.supervisor?.cancel_unspawned();
        await this.stop_owned();
      } catch {
        // Cleanup reports its own failure. Startup preserves its original failure.
      }
      const detail = error instanceof Error ? error.message : String(error);
      throw new BackendSpawnFailed(detail);
    }
  }
  private keep_error_line(line: string): void {
    this.error_lines.push(line);
    this.error_line_characters += Array.from(line).length;
    while (
      this.error_line_characters > STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS &&
      this.error_lines.length > 1
    ) {
      this.error_line_characters -= Array.from(this.error_lines.shift()!).length;
    }
  }
  standard_error_tail(): string | null {
    if (!this.error_lines.length) return null;
    return Array.from(this.error_lines.join(''))
      .slice(-STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS)
      .join('');
  }
  async local_child_identity(): Promise<LocalChildIdentity | null> {
    await this.registration;
    return this.identity;
  }
  async settled(): Promise<void> {
    await this.starting?.catch(() => {});
    await this.stopping?.catch(() => {});
    await this.cleanup?.catch(() => {});
    await this.supervisor?.shutdown_settled;
    await this.supervision_settlement;
    await this.owned?.exited;
  }
  stop(options?: StopOptions): Promise<void> {
    this.sealed = true;
    if (!this.stopping) {
      this.stop_options = options ?? { deadline: monotonic_seconds() + STOP_SECONDS };
      const stopping = this.finish_stop(this.stop_options);
      this.stopping = stopping;
      void stopping.catch(() => {
        if (this.stopping === stopping) this.stopping = null;
      });
    }
    return options === undefined
      ? this.stopping
      : observe_shutdown(this.stopping, options, 'Hermes cleanup remains unconfirmed');
  }
  private async finish_stop(options?: StopOptions): Promise<void> {
    this.client?.close();
    try {
      await this.starting;
    } catch {
      /* Startup owns its own reported failure. */
    }
    this.client?.close();
    await this.stop_owned(options);
  }
  private stop_owned(options?: StopOptions): Promise<void> {
    if (this.cleanup !== null) return this.cleanup;
    const cleanup = this.cleanup_owned(
      options ?? this.stop_options ?? { deadline: monotonic_seconds() + STOP_SECONDS },
    );
    this.cleanup = cleanup;
    void cleanup.catch(() => {
      if (this.cleanup === cleanup) this.cleanup = null;
    });
    return cleanup;
  }
  private async cleanup_owned(options?: StopOptions): Promise<void> {
    if (this.stderr_reader_failure !== null && !this.stderr_failure_reported) {
      this.stderr_failure_reported = true;
      try {
        this.cleanup_log?.(this.stderr_reader_failure);
      } catch {
        /* A failed diagnostic cannot block process cleanup. */
      }
    }
    try {
      const supervisor = this.supervisor;
      if (supervisor !== null) {
        const result = await supervisor.stop(async () => {
          await this.owned?.stop(options);
        }, options);
        this.supervision_settlement = supervisor.shutdown_settled;
        if (!result.complete) throw new Error(result.reason);
        this.supervisor = null;
      } else await this.owned?.stop(options);
      await this.scratch?.close();
      this.scratch = null;
    } catch (error) {
      const failure = new BackendAdapterError('the Hermes process did not stop', { cause: error });
      if (!this.cleanup_failure_reported) {
        this.cleanup_failure_reported = true;
        try {
          this.cleanup_log?.(failure);
        } catch {
          /* Diagnostics cannot change the cleanup result. */
        }
      }
      throw failure;
    }
  }
}
