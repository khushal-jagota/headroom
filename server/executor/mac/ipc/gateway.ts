import { setTimeout as sleep } from 'node:timers/promises';
import { PreviewError } from '../../../previews/contracts.ts';
import { is_os_error } from '../../../core/system_errors.ts';
import { MacPermissionDenied } from '../runner/contracts.ts';
/** Capability-scoped CLI work remains owned after its caller loses the response. */
import { FifoLock } from '../../../conversation/runtime/state.ts';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { scalar_text } from '../../../cli/text.ts';
import { as_object, decode_value, ExecutorValidationError } from '../../protocol.ts';
import type {
  AcceptanceReceipt,
  IpcRequest,
  IpcResponse,
  JsonObject,
  LiveReadRequest,
} from '../../wire.generated.ts';
import { safe_component } from '../files.ts';
import { CLI_RESULT_TIMEOUT_SECONDS, CLI_READ_RETRY_SECONDS } from '../configuration.ts';
import { same_reference } from '../../child_reference.ts';
import { ExecutorHttpError, HttpTimeout } from '../transport/errors.ts';
import { failure_detail } from '../failure_detail.ts';
import type { GatewayOptions, Publication } from './contracts.ts';
import { GatewayListener } from './listener.ts';
import { child_ticket_id } from '../runner/logic/child_owner.ts';
const monotonic = () => {
  const [seconds, nanoseconds] = process.hrtime();
  return seconds + nanoseconds / 1_000_000_000;
};
/** Writes that an older home cannot accept. The executor asks before it saves one. */
const HOME_SUPPORT: ReadonlyMap<
  string,
  { readonly capability: string; readonly name: string; readonly label: string }
> = new Map([
  ['ticket.send_back', { capability: 'ticket_send_back', name: 'send-back', label: 'Send-back' }],
  [
    'cli.request',
    { capability: 'cli_request', name: 'command', label: 'This command through machine code' },
  ],
]);
class GatewayConnectionError extends Error {}
const permission = (message: string) => new MacPermissionDenied(message);
const connection = (message: string) => new GatewayConnectionError(message);

export class AgentGateway {
  private readonly options: GatewayOptions;
  readonly controller = new AbortController();
  exit_committed = false;
  private closed = false;
  private reported = false;
  private readonly publishing = new Map<string, Publication>();
  private readonly operations = new Set<Promise<unknown>>();
  private readonly failures = new Set<unknown>();
  private readonly quiesce_lock = new FifoLock();
  private readonly listener: GatewayListener;
  private cleanup: Promise<void> | undefined;
  private resolve_actual!: () => void;
  private reject_actual!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.resolve_actual = resolve;
    this.reject_actual = reject;
  });
  constructor(options: GatewayOptions) {
    this.options = options;
    void this.actual.catch(() => {});
    this.listener = new GatewayListener(
      (request) => this.handle(request),
      (error) => this.failed(error),
    );
  }
  start(socket_path: string): Promise<void> {
    this.assert_open();
    return this.listener.start(socket_path);
  }
  private assert_open(): void {
    if (this.closed) throw new ServerLifecycleError('Mac IPC gateway is shutting down.');
  }
  private failed(error: unknown): void {
    this.failures.add(error);
    if (!this.reported) {
      this.reported = true;
      this.closed = true;
      console.warn('Mac IPC task failed:', failure_detail(error));
      try {
        this.options.on_failure(error instanceof Error ? error : new Error(String(error)));
      } catch (failure) {
        this.failures.add(failure);
      }
    }
  }
  handle(request: IpcRequest): Promise<IpcResponse> {
    this.assert_open();
    const task = Promise.resolve().then(() => this.dispatch(request));
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      (error) => {
        this.operations.delete(task);
        if (
          !this.closed ||
          !(error instanceof ServerLifecycleError || is_shutdown_business_cancellation(error))
        )
          this.failed(error);
      },
    );
    return task;
  }
  private async dispatch(request: IpcRequest): Promise<IpcResponse> {
    this.assert_open();
    try {
      if (request.operation === 'health')
        return {
          request_id: request.request_id,
          result: as_object(
            this.options.presence.status(this.options.runner.admission_stopped),
            'MacLocalStatus',
          ),
          error: null,
        };
      if (request.operation === 'quiesce') return await this.quiesce(request);
      const child = this.options.runner.authenticate(request.child_id, request.capability);
      let deadline = monotonic() + CLI_RESULT_TIMEOUT_SECONDS;
      if (request.response_deadline != null) {
        if (!Number.isFinite(request.response_deadline))
          throw new ExecutorValidationError('invalid IPC response deadline');
        deadline = Math.min(deadline, request.response_deadline);
      }
      if (request.operation === 'preview') {
        if (!this.options.previews) throw connection('Machine previews are unavailable');
        try {
          return {
            request_id: request.request_id,
            result: await this.options.previews.command(child, request.payload),
            error: null,
          };
        } catch (error) {
          if (error instanceof PreviewError) throw new ExecutorValidationError(error.message);
          if (error instanceof ExecutorHttpError) {
            if (error.status === 403) throw permission(error.message);
            throw connection(error.message);
          }
          throw error;
        }
      }
      if (request.operation === 'read') {
        if (this.options.client === null)
          throw connection('VPS unavailable; reads never use journal');
        let result: JsonObject;
        try {
          result = await this.read_through_restart(
            {
              child: child.reference,
              path: scalar_text(request.payload.path),
              params: (request.payload.params ?? {}) as JsonObject,
            },
            deadline,
          );
        } catch (error) {
          if (error instanceof ExecutorHttpError) {
            if (error.status === 400 && error.message === 'unsupported executor read')
              throw new ExecutorValidationError(
                'This read is not supported by Home yet; update Home before retrying',
              );
            if (error.status === 400)
              throw new ExecutorValidationError(error.message || 'read refused');
            if (error.status === 403) throw permission(error.message || 'read forbidden');
            throw connection(`VPS read failed: HTTP ${error.status} ${error.code}`);
          }
          throw connection(`VPS read failed: ${error instanceof Error ? error.name : 'Error'}`);
        }
        return { request_id: request.request_id, result, error: null };
      }
      if (request.operation === 'retry') {
        const submission = safe_component(
          decode_value(request.payload, 'IpcRetryPayload').submission_id,
        );
        const publication = this.publishing.get(submission);
        if (publication !== undefined) {
          if (!same_reference(publication.child, child.reference))
            throw permission('submission belongs to another executor child');
          return this.unconfirmed(request.request_id, submission, 'preparing');
        }
        const record = this.options.outbox.lookup(submission);
        if (record === null) return this.unconfirmed(request.request_id, submission, 'unknown');
        if (
          record.envelope.kind !== 'cli_write' ||
          !same_reference(decode_value(record.envelope.payload, 'CliWrite').child, child.reference)
        )
          throw permission('submission belongs to another executor child');
        return record.receipt === null
          ? this.unconfirmed(request.request_id, submission, 'queued')
          : this.receipt_response(request.request_id, record.receipt);
      }
      if (request.operation !== 'write')
        throw new ExecutorValidationError('unsupported executor IPC operation');
      const incoming = decode_value(request.payload, 'IpcWritePayload');
      const submission = safe_component(request.request_id);
      if (this.options.runner.admission_stopped) throw permission('executor admission stopped');
      const support = HOME_SUPPORT.get(incoming.operation);
      if (support !== undefined) {
        if (this.options.client === null)
          throw connection(`Home unavailable; ${support.name} support is unconfirmed`);
        const update = `${support.label} is not supported by Home yet; update Home before retrying`;
        let capabilities: JsonObject;
        try {
          capabilities = await this.read_through_restart(
            { child: child.reference, path: '/api/executor/capabilities', params: {} },
            deadline,
          );
        } catch (error) {
          if (error instanceof ExecutorHttpError) {
            if (error.status === 400 && error.message === 'unsupported executor read')
              throw new ExecutorValidationError(update);
            if (error.status === 403) throw permission(error.message || 'support read forbidden');
            throw connection(`Home support read failed: HTTP ${error.status} ${error.code}`);
          }
          throw connection(
            `Home support read failed: ${error instanceof Error ? error.name : 'Error'}`,
          );
        }
        if (capabilities[support.capability] !== true) throw new ExecutorValidationError(update);
      }
      const write = {
        operation: incoming.operation,
        arguments: incoming.arguments,
        child: child.reference,
        turn: child.active_turn,
      };
      const task = this.options.runner.source_work(child.reference, () =>
        this.options.outbox.add(write, submission, child_ticket_id(child.owner)),
      );
      const publication = { child: child.reference, task };
      this.publishing.set(submission, publication);
      this.operations.add(task);
      void task.then(
        () => this.release_publication(submission, publication),
        (error) => {
          // The runner also retains the raw source failure. Unknown is not acceptance.
          this.failures.add(error);
          console.warn('Mac IPC publication failed:', failure_detail(error));
          this.release_publication(submission, publication);
        },
      );
      const completed = await this.wait_publication(task, deadline);
      if (!completed) return this.unconfirmed(request.request_id, submission, 'preparing');
      const settled = await Promise.allSettled([task]);
      if (settled[0].status === 'rejected')
        return this.unconfirmed(request.request_id, submission, 'unknown');
      const receipt = await this.options.outbox.wait_confirmed(
        submission,
        Math.max(0, deadline - monotonic()),
        this.controller.signal,
      );
      return receipt === null
        ? this.unconfirmed(request.request_id, submission, 'queued')
        : this.receipt_response(request.request_id, receipt);
    } catch (error) {
      const code =
        error instanceof ExecutorValidationError
          ? 'validation'
          : error instanceof MacPermissionDenied
            ? 'agent_forbidden'
            : error instanceof GatewayConnectionError || is_os_error(error)
              ? 'connection'
              : null;
      if (code === null) throw error;
      return {
        request_id: request.request_id,
        result: null,
        error: { code, message: (error as Error).message, detail: {} },
      };
    }
  }
  /** Reads have no effects. Only transport failures and temporary refusals repeat. */
  private async read_through_restart(
    request: LiveReadRequest,
    deadline: number,
  ): Promise<JsonObject> {
    const signal = AbortSignal.any([
      this.controller.signal,
      AbortSignal.timeout(Math.max(1, Math.ceil((deadline - monotonic()) * 1000))),
    ]);
    for (;;) {
      try {
        signal.throwIfAborted();
        return await this.options.client!.read(request, signal);
      } catch (error) {
        const temporary =
          error instanceof ExecutorHttpError
            ? [408, 429, 502, 503, 504].includes(error.status) || error.code === 'retry_later'
            : error instanceof HttpTimeout ||
              is_os_error(error) ||
              (error instanceof Error &&
                'code' in error &&
                error.code === 'ERR_STREAM_PREMATURE_CLOSE');
        if (!temporary || signal.aborted || monotonic() >= deadline) throw error;
        await sleep(
          Math.min(CLI_READ_RETRY_SECONDS, Math.max(0, deadline - monotonic())) * 1000,
          undefined,
          { signal },
        );
      }
    }
  }
  private release_publication(submission: string, publication: Publication): void {
    if (this.publishing.get(submission) === publication) this.publishing.delete(submission);
    this.operations.delete(publication.task);
  }
  private wait_publication(task: Promise<unknown>, deadline: number): Promise<boolean> {
    return new Promise((resolve) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = (done: boolean) => {
        clearTimeout(timer);
        this.controller.signal.removeEventListener('abort', abort);
        resolve(done);
      };
      const abort = () => finish(false);
      this.controller.signal.addEventListener('abort', abort, { once: true });
      if (this.controller.signal.aborted) {
        finish(false);
        return;
      }
      timer = setTimeout(() => finish(false), Math.max(0, deadline - monotonic()) * 1000);
      void task.then(
        () => finish(true),
        () => finish(true),
      );
    });
  }
  private async quiesce(request: IpcRequest): Promise<IpcResponse> {
    const maintenance = decode_value(request.payload, 'QuiesceRequest');
    return this.quiesce_lock.run(async () => {
      this.controller.signal.throwIfAborted();
      this.options.runner.seal_admission();
      await this.options.settle_catalogs(this.controller.signal);
      const quiescence = await this.options.runner.quiesce(this.controller.signal);
      const ready =
        quiescence.admission_stopped &&
        quiescence.idle_children_stopped &&
        [
          quiescence.active_turn_count,
          quiescence.pending_callback_count,
          quiescence.uncertain_operation_count,
          quiescence.unsent_entry_count,
        ].every((n) => n === 0);
      const exit_committed = ready && maintenance.exit_when_ready;
      if (exit_committed) {
        this.exit_committed = true;
        this.options.request_exit();
      }
      return {
        request_id: request.request_id,
        result: as_object(
          {
            identity: this.options.presence.identity,
            process_id: process.pid,
            quiescence,
            exit_committed,
          },
          'QuiescenceReceipt',
        ),
        error: null,
      };
    });
  }
  private unconfirmed(
    request_id: string,
    submission_id: string,
    publication: 'unknown' | 'preparing' | 'queued',
  ): IpcResponse {
    return {
      request_id,
      result: null,
      error: null,
      unconfirmed: { request_id, submission_id, publication, acceptance: 'unconfirmed' },
    };
  }
  private receipt_response(request_id: string, receipt: AcceptanceReceipt): IpcResponse {
    if (receipt.disposition === 'uncertain')
      return this.unconfirmed(request_id, receipt.submission_id, 'queued');
    return receipt.disposition === 'refused'
      ? { request_id, result: null, error: (receipt.result.error ?? receipt.result) as JsonObject }
      : { request_id, result: receipt.result, error: null };
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.cleanup) {
      this.closed = true;
      let closing: Promise<void>;
      this.cleanup = Promise.resolve().then(async () => {
        await closing;
        while (this.operations.size) await Promise.allSettled([...this.operations]);
        if (this.failures.size)
          throw new AggregateError(
            [...this.failures],
            'Mac IPC work failed before cleanup completed.',
          );
      });
      void this.cleanup.catch(() => {});
      void this.cleanup.then(this.resolve_actual, this.reject_actual);
      closing = this.listener.close();
      this.controller.abort();
    }
    return observe_shutdown(
      this.actual,
      options,
      'Mac IPC work remains unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
}
