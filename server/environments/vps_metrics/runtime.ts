import { Worker } from 'node:worker_threads';
import { randomUUID } from 'node:crypto';
import type { MetricsConfig } from './configuration.ts';
import type { CollectionStatus, LatestResponse, MetricNumber } from './contracts.ts';
import { MetricsOperationalError } from './contracts.ts';
import { decode } from './codec.ts';
import { error_from_wire } from './errors.ts';
import { MetricsReaders } from './readers/pool.ts';
import type { MetricsRead } from './readers/contracts.ts';
import type { CollectorFactory, SamplerReply } from './sampler/contracts.ts';
import { observe_shutdown } from '../../lifecycle/deadline.ts';
import { ShutdownDeadlineExceeded, type StopOptions } from '../../lifecycle/contracts.ts';

export interface MetricsRuntimeOptions {
  path: string;
  config: MetricsConfig;
  collector: CollectorFactory | null;
  wall_clock?: () => number;
}
/** This runtime owns only its sidecar. It never receives the application database. */
export class MetricsRuntime {
  readonly path: string;
  readonly config: MetricsConfig;
  readonly wall_clock: () => number;
  readonly reads: MetricsReaders;
  failure: CollectionStatus | null = null;
  owns_collection = false;
  private collector: CollectorFactory | null;
  private worker?: Worker;
  private ready = false;
  private stopped = false;
  private starting?: Promise<void>;
  private stopping?: Promise<void>;
  private shutdown_settlement?: Promise<void>;
  private readers_closing?: Promise<void>;
  private exited?: Promise<void>;
  private worker_exited = false;
  private terminating?: Promise<void>;
  private activities = new Map<
    string,
    { resolve(value: MetricNumber): void; reject(error: unknown): void }
  >();
  constructor(options: MetricsRuntimeOptions) {
    this.path = options.path;
    this.config = options.config;
    this.collector = options.collector;
    this.wall_clock = options.wall_clock ?? (() => Date.now() / 1000);
    this.reads = new MetricsReaders(this.path, this.config);
  }
  start(): Promise<void> {
    if (this.stopped)
      return Promise.reject(new MetricsOperationalError('VPS metrics runtime is stopped'));
    if (this.starting) return this.starting;
    this.starting = this.initialize();
    return this.starting;
  }
  private async initialize(): Promise<void> {
    const worker = new Worker(new URL('./sampler/worker.ts', import.meta.url), {
      workerData: { path: this.path, config: this.config, collector: this.collector },
    });
    this.worker = worker;
    this.exited = new Promise<void>((resolve) =>
      worker.once('exit', () => {
        this.worker_exited = true;
        this.owns_collection = false;
        this.ready = false;
        for (const entry of this.activities.values())
          entry.reject(new MetricsOperationalError('VPS metrics storage is unavailable'));
        this.activities.clear();
        if (!this.stopped) void this.close_readers().catch(() => {});
        resolve();
      }),
    );
    try {
      await new Promise<void>((resolve, reject) => {
        worker.once('error', reject);
        worker.once('exit', () =>
          reject(new MetricsOperationalError('VPS metrics sampler stopped before READY')),
        );
        worker.on('message', (message: SamplerReply) => {
          if (message.type === 'ready') resolve();
          else if (message.type === 'startup_error') reject(new Error(message.message));
          else if (message.type === 'ownership') this.owns_collection = message.owner;
          else if (message.type === 'failure')
            this.failure =
              message.payload === null ? null : decode<CollectionStatus>(message.payload);
          else if (message.type === 'activity' || message.type === 'error') {
            const pending = this.activities.get(message.request);
            if (!pending) return;
            this.activities.delete(message.request);
            if (message.type === 'activity') pending.resolve(decode<MetricNumber>(message.expiry));
            else pending.reject(error_from_wire(message.error));
          }
        });
      });
      if (this.stopped) throw new MetricsOperationalError('VPS metrics runtime is stopped');
      await this.reads.start();
      if (this.stopped) throw new MetricsOperationalError('VPS metrics runtime is stopped');
      this.ready = true;
    } catch (error) {
      this.stopped = true;
      worker.postMessage({ type: 'stop' });
      await this.close_readers();
      // A factory still under construction remains referenced and owned until exit.
      throw error;
    }
  }
  read<T>(operation: MetricsRead): Promise<T> {
    if (!this.ready || this.stopped)
      return Promise.reject(new MetricsOperationalError('VPS metrics runtime is unavailable'));
    return this.reads.read<T>(operation);
  }
  async latest(): Promise<LatestResponse> {
    const result = await this.read<LatestResponse>({ kind: 'latest', now: this.wall_clock() });
    if (this.failure)
      result.collection = { ...this.failure, last_sample_at: result.collection.last_sample_at };
    return result;
  }
  renew_activity(): Promise<MetricNumber> {
    if (!this.ready || this.stopped || !this.worker)
      return Promise.reject(new MetricsOperationalError('VPS metrics runtime is unavailable'));
    const request = randomUUID();
    return new Promise((resolve, reject) => {
      this.activities.set(request, { resolve, reject });
      this.worker!.postMessage({ type: 'activity', request, now: this.wall_clock() });
    });
  }
  private close_readers(): Promise<void> {
    return (this.readers_closing ??= this.reads.close());
  }
  private terminate_worker(): void {
    if (!this.worker || this.worker_exited || this.terminating) return;
    let resolve!: () => void, reject!: (error: unknown) => void;
    // Publish the actual result before Worker.terminate can synchronously fail.
    this.terminating = new Promise<void>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    void this.terminating.catch(() => {});
    try {
      void this.worker.terminate().then(() => resolve(), reject);
    } catch (error) {
      reject(error);
    }
  }
  /** Deadline uses the server's monotonic seconds convention. */
  stop(options: StopOptions): Promise<void> {
    if (this.stopping) return this.stopping;
    this.stopped = true;
    this.ready = false;
    for (const entry of this.activities.values())
      entry.reject(new MetricsOperationalError('VPS metrics runtime is stopped'));
    this.activities.clear();
    const actuals: Promise<unknown>[] = [];
    this.shutdown_settlement = Promise.resolve().then(async () => {
      const results = await Promise.allSettled([
        ...actuals,
        this.starting?.catch(() => {}),
        this.exited,
      ]);
      // A deadline can reserve termination after the first snapshot. Its real
      // result is joined only after the exact worker's exit, never in place of it.
      results.push(...(await Promise.allSettled([this.terminating])));
      const failures = results.filter((result) => result.status === 'rejected');
      if (failures.length)
        throw new AggregateError(
          failures.map((result) => result.reason),
          'VPS metrics shutdown failed',
        );
    });
    this.stopping = observe_shutdown(this.shutdown_settlement, options, 'VPS metrics').catch(
      (error) => {
        if (error instanceof ShutdownDeadlineExceeded) this.terminate_worker();
        throw error;
      },
    );
    try {
      this.worker?.postMessage({ type: 'stop' });
    } catch (error) {
      actuals.push(Promise.reject(error));
    }
    try {
      actuals.push(this.close_readers());
    } catch (error) {
      actuals.push(Promise.reject(error));
    }
    return this.stopping;
  }
  /** Reader reap and collector exit remain owned beyond a caller's deadline. */
  settled(): Promise<void> {
    return (
      this.shutdown_settlement ??
      Promise.all([this.starting?.catch(() => {}), this.readers_closing, this.exited]).then(
        () => {},
      )
    );
  }
}
