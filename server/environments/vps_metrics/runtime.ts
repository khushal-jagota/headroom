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
  private exited?: Promise<void>;
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
        this.owns_collection = false;
        this.ready = false;
        for (const entry of this.activities.values())
          entry.reject(new MetricsOperationalError('VPS metrics storage is unavailable'));
        this.activities.clear();
        if (!this.stopped) void this.reads.close();
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
      await this.reads.close();
      // A factory still under construction retains its handle until it settles.
      worker.unref();
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
  /** Deadline uses the server's monotonic seconds convention. */
  stop(options: { deadline?: number } = {}): Promise<void> {
    if (this.stopping) return this.stopping;
    this.stopped = true;
    this.ready = false;
    for (const entry of this.activities.values())
      entry.reject(new MetricsOperationalError('VPS metrics runtime is stopped'));
    this.activities.clear();
    this.worker?.postMessage({ type: 'stop' });
    const readers = this.reads.close();
    this.stopping = (async () => {
      const deadline = options.deadline ?? performance.now() / 1000 + 30;
      const remaining = Math.max(0, (deadline - performance.now() / 1000) * 1000);
      let timer: ReturnType<typeof setTimeout> | undefined;
      const all = Promise.all([readers, this.exited]).then(() => undefined);
      await Promise.race([
        all,
        new Promise<void>((resolve) => {
          timer = setTimeout(resolve, remaining);
        }),
      ]);
      if (timer) clearTimeout(timer);
      // Keep a pending collector's lock until settlement, like Python's daemon thread.
      // Unref permits APP exit without an indefinitely live collector retaining Node.
      this.worker?.unref();
      await readers;
    })();
    return this.stopping;
  }
  /** Tests that release a pending synthetic collector can await actual descriptor closure. */
  settled(): Promise<void> {
    this.worker?.ref();
    return this.exited ?? Promise.resolve();
  }
}
