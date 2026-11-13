/** Own optional reads, canonical worker custody, and actual source cleanup. */
import { Worker } from 'node:worker_threads';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import type { BackendSnapshot } from '../../../conversation/snapshot/contracts.ts';
import { ValueError, KeyError } from '../../protocol.ts';
import {
  model_catalog_cleanup_pending,
  remember_model_catalog_failure,
} from '../../../conversation/backends/catalog_cleanup.ts';
import type { ModelListRequest } from '../../wire.generated.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import type { CanonicalCatalogResult } from '../mailbox/contracts.ts';
import { pack } from '../storage/journal_worker_contracts.ts';
import { FILE_CHUNK_BYTES } from '../configuration.ts';
import type {
  MacCatalogDependencies,
  CatalogWorkerReply,
  CatalogWorkerRequest,
  CatalogAction,
  CatalogResultDescriptor,
  CatalogReaderDescriptor,
} from './contracts.ts';
export type { MacCatalogDependencies } from './contracts.ts';
class Result implements CanonicalCatalogResult {
  private readonly service: MacCatalog;
  private readonly descriptor: CatalogResultDescriptor;
  private closed = false;
  private closing: Promise<void> | undefined;
  cleanup_failed = false;
  constructor(service: MacCatalog, descriptor: CatalogResultDescriptor) {
    this.service = service;
    this.descriptor = descriptor;
  }
  async open(): Promise<CanonicalReader> {
    if (this.closed) throw new ServerLifecycleError('Catalog result is closed.');
    const descriptor = await this.service.call<CatalogReaderDescriptor>({
      operation: 'open',
      result: this.descriptor.result,
    });
    const service = this.service;
    let closed = false;
    return {
      byte_count: descriptor.byte_count,
      async *[Symbol.asyncIterator]() {
        let received = 0;
        while (!closed) {
          const chunk = await service.call<ArrayBuffer | null>({
            operation: 'read',
            reader: descriptor.reader,
          });
          if (chunk === null) {
            closed = true;
            if (received !== descriptor.byte_count)
              throw service.resource_failure(
                new Error('Catalog reader ended before its byte count.'),
              );
            return;
          }
          if (
            !chunk.byteLength ||
            chunk.byteLength > FILE_CHUNK_BYTES ||
            received + chunk.byteLength > descriptor.byte_count
          )
            throw service.resource_failure(
              new Error('Catalog reader exceeded its byte or chunk count.'),
            );
          received += chunk.byteLength;
          yield new Uint8Array(chunk);
        }
      },
      async close() {
        if (closed) return;
        try {
          await service.close_reference({ operation: 'close_reader', reader: descriptor.reader });
        } catch (error) {
          throw service.resource_failure(error);
        }
        closed = true;
      },
    };
  }
  close(): Promise<void> {
    if (this.closing) return this.closing;
    if (this.closed) return Promise.resolve();
    if (!this.closing) {
      const task = Promise.resolve().then(async () => {
        try {
          await this.service.close_reference({
            operation: 'close_result',
            result: this.descriptor.result,
          });
        } catch (error) {
          this.cleanup_failed = true;
          throw this.service.resource_failure(error);
        }
        this.closed = true;
        this.service.release_result(this);
      });
      this.closing = task;
      void task.catch(() => {
        if (this.closing === task) this.closing = undefined;
      });
    }
    return this.closing;
  }
}
function optional_failure(error: unknown): boolean {
  return (
    error instanceof TypeError ||
    error instanceof ValueError ||
    error instanceof KeyError ||
    (error instanceof Error &&
      'code' in error &&
      typeof error.code === 'string' &&
      !error.code.startsWith('ERR_WORKER_'))
  );
}
export class MacCatalog {
  private readonly dependencies: MacCatalogDependencies;
  private readonly cancellation = new AbortController();
  private readonly reads = new Set<Promise<CanonicalCatalogResult>>();
  private readonly results = new Set<Result>();
  private readonly pending = new Map<
    number,
    { resolve(value: unknown): void; reject(error: unknown): void }
  >();
  private readonly failures = new Set<unknown>();
  private worker: Worker | undefined;
  private worker_stopping = false;
  private sequence = 0;
  private failure: ServerLifecycleError | undefined;
  private closed = false;
  private sources_done = false;
  private source_stop: Promise<void> | undefined;
  private invoking_sources = false;
  private resolve_actual!: () => void;
  private reject_actual!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.resolve_actual = resolve;
    this.reject_actual = reject;
  });
  constructor(dependencies: MacCatalogDependencies) {
    this.dependencies = dependencies;
    void this.actual.catch(() => {});
  }
  /** Sources invoke this before optional-read fallback can hide native cleanup failure. */
  resource_failure(error: unknown): ServerLifecycleError {
    remember_model_catalog_failure(this.failures, error);
    if (!this.failure) {
      this.failure = Object.assign(
        new ServerLifecycleError('Mac catalog resource cleanup failed.'),
        { cause: error },
      );
      this.closed = true;
      this.cancellation.abort();
      for (const operation of this.pending.values()) operation.reject(this.failure);
      this.pending.clear();
      try {
        this.dependencies.on_failure(this.failure);
      } catch (callback) {
        remember_model_catalog_failure(this.failures, callback);
      }
    }
    return this.failure;
  }
  read_result(command_id: string, request: ModelListRequest): Promise<CanonicalCatalogResult> {
    if (this.closed)
      return Promise.reject(this.failure ?? new ServerLifecycleError('Mac catalog is stopped.'));
    try {
      this.ensure_worker();
    } catch (error) {
      return Promise.reject(this.resource_failure(error));
    }
    // Publish custody before a source can synchronously reenter stop().
    const task = Promise.resolve().then(() => this.read(command_id, request));
    this.reads.add(task);
    void task.then(
      () => this.release_read(task),
      () => this.release_read(task),
    );
    return task;
  }
  private release_read(task: Promise<CanonicalCatalogResult>): void {
    this.reads.delete(task);
    this.maybe_stop_worker();
    this.complete();
  }
  release_result(result: Result): void {
    this.results.delete(result);
    this.maybe_stop_worker();
    this.complete();
  }
  private ensure_worker(): Worker {
    if (this.worker) return this.worker;
    const worker = new Worker(new URL('./worker.ts', import.meta.url), { execArgv: [] });
    this.worker = worker;
    worker.on('error', (error) => this.resource_failure(error));
    worker.on('message', (reply: CatalogWorkerReply) => {
      const operation = this.pending.get(reply.id);
      if (!operation) {
        this.resource_failure(new Error('Catalog encoder returned an unknown operation.'));
        return;
      }
      this.pending.delete(reply.id);
      if (reply.error !== undefined) operation.reject(new Error(reply.error));
      else operation.resolve(reply.value);
      this.maybe_stop_worker();
    });
    worker.once('exit', (code) => {
      if (!this.worker_stopping || code !== 0 || this.pending.size)
        this.resource_failure(new Error('Mac catalog encoder exited before confirmed cleanup.'));
      this.worker = undefined;
      this.complete();
    });
    return worker;
  }
  call<T>(action: CatalogAction): Promise<T> {
    if (this.failure) return Promise.reject(this.failure);
    return this.send(action) as Promise<T>;
  }
  close_reference(
    action: Extract<CatalogAction, { operation: 'close_result' | 'close_reader' }>,
  ): Promise<void> {
    if (!this.worker) return Promise.resolve();
    return this.send(action).then(() => undefined);
  }
  private send(action: CatalogAction): Promise<unknown> {
    const worker = this.worker;
    if (!worker || this.worker_stopping)
      return Promise.reject(
        this.resource_failure(new Error('Mac catalog encoder is unavailable.')),
      );
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      this.pending.set(id, { resolve, reject });
      try {
        worker.postMessage({ id, action } satisfies CatalogWorkerRequest);
      } catch (error) {
        const failure = this.resource_failure(error);
        this.pending.delete(id);
        reject(failure);
      }
    });
  }
  private async read(
    command_id: string,
    request: ModelListRequest,
  ): Promise<CanonicalCatalogResult> {
    if (this.closed) throw this.failure ?? new ServerLifecycleError('Mac catalog is stopped.');
    let snapshot: BackendSnapshot | null = null;
    try {
      if (
        request.backend_key === 'claude' ||
        request.backend_key === 'codex' ||
        request.backend_key === 'hermes'
      )
        snapshot = await this.dependencies.snapshots.snapshot(request.backend_key, {
          refresh: request.refresh,
          discover_update_advisory: false,
          signal: this.cancellation.signal,
        });
    } catch (error) {
      if (this.failure) throw this.failure;
      if (!(optional_failure(error) || (this.closed && is_shutdown_business_cancellation(error))))
        throw error;
    }
    if (this.failure) throw this.failure;
    const descriptor = await this.call<CatalogResultDescriptor>({
      operation: 'encode',
      command_id,
      snapshot: pack(snapshot),
    });
    const result = new Result(this, descriptor);
    this.results.add(result);
    return result;
  }
  private maybe_stop_worker(): void {
    if (
      !this.closed ||
      this.reads.size ||
      this.results.size ||
      this.pending.size ||
      !this.worker ||
      this.worker_stopping
    )
      return;
    this.worker_stopping = true;
    try {
      this.worker.postMessage({ stop: true } satisfies CatalogWorkerRequest);
    } catch (error) {
      this.worker_stopping = false;
      this.resource_failure(error);
    }
  }
  private complete(): void {
    if (
      !this.closed ||
      !this.sources_done ||
      this.reads.size ||
      this.worker ||
      this.results.size ||
      this.pending.size
    )
      return;
    if (this.failures.size)
      this.reject_actual(new AggregateError(this.failures, 'Mac catalog cleanup failed.'));
    else this.resolve_actual();
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    if (!this.sources_done && !this.invoking_sources) {
      let resolve!: () => void, reject!: (error: unknown) => void;
      const task = new Promise<void>((done, failed) => {
        resolve = done;
        reject = failed;
      });
      this.source_stop = task;
      void task.catch(() => {});
      this.invoking_sources = true;
      try {
        // Publish the original stop options before cancellation reaches a probe.
        Promise.resolve(this.dependencies.stop_sources(options)).then(
          () => {
            this.sources_done = true;
            this.complete();
            resolve();
          },
          (error: unknown) => {
            if (!model_catalog_cleanup_pending(error)) this.sources_done = true;
            this.resource_failure(error);
            this.complete();
            reject(error);
          },
        );
      } catch (error) {
        if (!model_catalog_cleanup_pending(error)) this.sources_done = true;
        this.resource_failure(error);
        this.complete();
        reject(error);
      } finally {
        this.invoking_sources = false;
      }
    }
    this.cancellation.abort();
    this.maybe_stop_worker();
    const retries = [...this.results]
      .filter((result) => result.cleanup_failed)
      .map((result) => result.close());
    return observe_shutdown(
      Promise.all([this.source_stop, ...retries, this.actual]),
      options,
      'Mac catalog work remains unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
}
