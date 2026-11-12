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
import { BackendAdapterError, BackendSpawnFailed } from '../contracts.ts';
import { spawn_codex_process, within } from '../codex_app_server/process.ts';
import type { OwnedCodexProcess } from '../codex_app_server/process.ts';
import type { ResolvedConversationStart } from '../../contracts.ts';
import { child_environment } from './launch.ts';
import type { AcpChildLaunch } from './launch.ts';
import {
  CHILD_OUTPUT_LINE_LIMIT_BYTES,
  STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS,
  ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS,
} from './configuration.ts';

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

class Pending<T> {
  readonly promise: Promise<T>;
  private resolve_value!: (value: T) => void;
  private reject_value!: (error: unknown) => void;
  constructor() {
    this.promise = new Promise<T>((resolve, reject) => {
      this.resolve_value = resolve;
      this.reject_value = reject;
    });
    void this.promise.catch(() => {});
  }
  resolve(value: T): void {
    this.resolve_value(value);
  }
  reject(error: unknown): void {
    this.reject_value(error);
  }
}
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
}
interface WriteReceipt {
  method: string;
  params: unknown;
  request: boolean;
  pending: Pending<void>;
}

export class HermesAcpClient {
  readonly closed: Promise<void>;
  private readonly connection: Rpc.Connection;
  private readonly receipts = new Set<WriteReceipt>();
  private readonly response_receipts = new Map<JsonRpcId, Pending<void>>();
  private readonly active_answers = new Set<Pending<never>>();
  private failure: unknown = null;
  private gate: Pending<void> | null = null;
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
                this.gate = new Pending<void>();
              else this.gate = null;
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
            await writer.write(message);
            if ('method' in message) {
              for (const receipt of this.receipts) {
                if (
                  receipt.method === message.method &&
                  receipt.params === message.params &&
                  receipt.request === 'id' in message
                ) {
                  this.receipts.delete(receipt);
                  receipt.pending.resolve();
                  break;
                }
              }
            } else {
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
              const params = validators.zSessionNotification.parse(message.params);
              await this.handlers.session_update?.(params);
            }
          } else if (
            message.method === 'session/request_permission' &&
            this.handlers.request_permission
          ) {
            const params = validators.zRequestPermissionRequest.parse(message.params);
            const parked = await this.handlers.request_permission(params, message.responder.id);
            const interrupted = new Pending<never>();
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
  private wait_for_permission_write<T>(promise: Promise<T>): Promise<T> {
    let timer: ReturnType<typeof setTimeout>;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(
        () => reject(new Error('the permission answer did not reach the wire')),
        ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS * 1000,
      );
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
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
    const written = new Pending<void>();
    const receipt = { method, params, request: true, pending: written };
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
      void response.catch(() => {});
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
      receipt = new Pending<void>();
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
  private sealed = false;
  private owned: OwnedCodexProcess | null = null;
  private client: HermesAcpClient | null = null;
  private starting: Promise<HermesAcpClient> | null = null;
  private stopping: Promise<void> | null = null;
  private error_tail = '';
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
      const owned = await spawn_codex_process(launch.argv, {
        environment: child_environment(launch, resolved_start),
        working_directory: resolved_start.workspace_folder,
      });
      this.owned = owned;
      if (this.sealed) throw new Error('this child is being shut down');
      const decoder = new StringDecoder('utf8');
      const keep_stderr = (text: string) => {
        this.error_tail = Array.from(this.error_tail + text)
          .slice(-STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS)
          .join('');
      };
      owned.child.stderr.on('data', (chunk: Buffer) => keep_stderr(decoder.write(chunk)));
      owned.child.stderr.on('end', () => keep_stderr(decoder.end()));
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
      void owned.exited.then(async () => {
        if (!(await within(client.closed, ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS)))
          client.close(new Error('the backend process ended'));
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
        await this.stop_owned();
      } catch {
        // Cleanup reports its own failure. Startup preserves its original failure.
      }
      throw new BackendSpawnFailed(error instanceof Error ? error.message : String(error));
    }
  }
  standard_error_tail(): string | null {
    return this.error_tail || null;
  }
  stop(): Promise<void> {
    this.sealed = true;
    if (!this.stopping) {
      const stopping = this.finish_stop();
      this.stopping = stopping;
      void stopping.catch(() => {
        if (this.stopping === stopping) this.stopping = null;
      });
    }
    return this.stopping;
  }
  private async finish_stop(): Promise<void> {
    this.client?.close();
    try {
      await this.starting;
    } catch {
      /* Startup owns its own reported failure. */
    }
    this.client?.close();
    await this.stop_owned();
  }
  private async stop_owned(): Promise<void> {
    try {
      await this.owned?.stop();
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
