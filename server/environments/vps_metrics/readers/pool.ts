import {
  ChildProcesses,
  child_lifetime,
  signal_child,
} from '../../../lifecycle/owned_processes.ts';
import type { ChildProcess, ForkOptions } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import type { Duplex } from 'node:stream';
import type { MetricsConfig } from '../configuration.ts';
import { decode } from '../codec.ts';
import { MetricsOperationalError } from '../contracts.ts';
import { error_from_wire } from '../errors.ts';
import type { MetricsRead, ReaderReply } from './contracts.ts';

interface Pending {
  request: string;
  deadline: string;
  operation: MetricsRead;
  resolve(value: unknown): void;
  reject(error: unknown): void;
  timer: ReturnType<typeof setTimeout>;
  settled: boolean;
}
interface Reader {
  child: ChildProcess;
  incarnation: string;
  liveness: Duplex;
  ready: boolean;
  active?: Pending;
  retiring?: Promise<void>;
  reaped: Promise<void>;
}
const unavailable = (): MetricsOperationalError =>
  new MetricsOperationalError('VPS metrics storage is unavailable');
const expired = (): MetricsOperationalError =>
  new MetricsOperationalError('VPS metrics read deadline exceeded');
type ReaderProcessFactory = (entry: URL, options: ForkOptions) => ChildProcess;

/** Queue deadlines start on admission, after the pool's cold-start READY boundary. */
export class MetricsReaders {
  private readonly children = new ChildProcesses();
  private readers = new Set<Reader>();
  private queue: Pending[] = [];
  private closing = false;
  private started = false;
  private starting?: Promise<void>;
  private replacing?: Promise<void>;
  private retry_after = 0n;
  private retry_timer?: ReturnType<typeof setTimeout>;
  private retry_wake?: () => void;
  private readonly spawn_reader: ReaderProcessFactory;
  readonly path: string;
  readonly config: MetricsConfig;
  constructor(path: string, config: MetricsConfig, spawn_reader?: ReaderProcessFactory) {
    this.path = path;
    this.config = config;
    this.spawn_reader =
      spawn_reader ?? ((entry, options) => this.children.fork(entry, [], options));
  }
  start(): Promise<void> {
    if (this.closing) return Promise.reject(unavailable());
    if (this.starting) return this.starting;
    this.starting = Promise.all(
      Array.from({ length: this.config.read_workers }, () => this.spawn()),
    )
      .then(() => {
        if (this.closing) throw unavailable();
        this.started = true;
        this.fill_deficit();
      })
      .catch(async (error) => {
        await this.close();
        throw error;
      });
    return this.starting;
  }
  private spawn(): Promise<void> {
    if (this.closing) return Promise.reject(unavailable());
    const incarnation = randomUUID();
    // fd4 is a dedicated liveness pipe. Only this APP keeps its writer endpoint.
    const child = this.spawn_reader(new URL('./child.ts', import.meta.url), {
      stdio: ['ignore', 'ignore', 'pipe', 'ipc', 'pipe'],
    });
    child.stderr?.on('data', (chunk: Buffer) => console.error(chunk.toString()));
    const liveness = child.stdio[4] as Duplex;
    const reader: Reader = {
      child,
      incarnation,
      liveness,
      ready: false,
      reaped: child_lifetime(child).closed,
    };
    this.readers.add(reader);
    return new Promise<void>((resolve, reject) => {
      let admitted = false;
      child.on('error', () => {
        void this.retire(reader);
      });
      child.once('close', () => {
        liveness.destroy();
        this.readers.delete(reader);
        if (!admitted) reject(unavailable());
        if (!reader.retiring) {
          if (reader.active) this.settle(reader.active, undefined, unavailable());
          if (admitted) queueMicrotask(() => this.fill_deficit());
        }
      });
      child.on('message', (message: ReaderReply) => {
        if (reader.retiring || message.incarnation !== incarnation) return;
        if (message.type === 'ready') {
          if (reader.ready) return;
          reader.ready = true;
          admitted = true;
          resolve();
          this.dispatch();
          return;
        }
        const job = reader.active;
        if (!job || message.request !== job.request) return;
        if (process.hrtime.bigint() >= BigInt(job.deadline)) {
          void this.expire(job, reader);
          return;
        }
        reader.active = undefined;
        if (message.type === 'result') {
          try {
            this.settle(job, decode(message.payload));
          } catch (error) {
            this.settle(job, undefined, error);
          }
        } else {
          this.settle(job, undefined, error_from_wire(message.error));
        }
        this.dispatch();
      });
      child.send({ incarnation, path: this.path, config: this.config });
    });
  }
  private fill_deficit(): void {
    if (
      !this.started ||
      this.closing ||
      this.replacing ||
      this.readers.size >= this.config.read_workers
    )
      return;
    this.replacing = this.replace().finally(() => {
      this.replacing = undefined;
      // A new read may arrive from a rejected request's continuation before this finalizer.
      if (this.queue.length) this.fill_deficit();
    });
  }
  private async replace(): Promise<void> {
    const remaining = this.retry_after - process.hrtime.bigint();
    if (remaining > 0n) {
      const { promise, resolve } = Promise.withResolvers<void>();
      this.retry_wake = resolve;
      this.retry_timer = setTimeout(resolve, Math.ceil(Number(remaining) / 1e6));
      await promise;
      this.retry_timer = undefined;
      this.retry_wake = undefined;
    }
    try {
      while (!this.closing && this.readers.size < this.config.read_workers) await this.spawn();
      this.retry_after = 0n;
    } catch {
      this.retry_after = process.hrtime.bigint() + 50_000_000n;
      if (!this.closing) this.fail_queue();
    }
  }
  private settle(job: Pending, value?: unknown, error?: unknown): void {
    if (job.settled) return;
    job.settled = true;
    clearTimeout(job.timer);
    if (error) job.reject(error);
    else job.resolve(value);
  }
  private retire(reader: Reader): Promise<void> {
    if (reader.retiring) return reader.retiring;
    reader.ready = false;
    reader.retiring = (async () => {
      signal_child(reader.child, 'SIGKILL', false);
      await reader.reaped;
      if (reader.active) this.settle(reader.active, undefined, expired());
      reader.active = undefined;
      // Reaping and request settlement never wait for another reader's cold boot or backoff.
      queueMicrotask(() => this.fill_deficit());
    })();
    return reader.retiring;
  }
  private async expire(job: Pending, reader?: Reader): Promise<void> {
    if (job.settled) return;
    if (reader) await this.retire(reader);
    else {
      this.queue = this.queue.filter((candidate) => candidate !== job);
      this.settle(job, undefined, expired());
    }
  }
  private dispatch(): void {
    if (this.closing) return;
    for (const reader of this.readers) {
      if (!reader.ready || reader.active || reader.retiring) continue;
      let job: Pending | undefined;
      while ((job = this.queue.shift())) {
        if (job.settled) continue;
        if (process.hrtime.bigint() >= BigInt(job.deadline)) {
          this.settle(job, undefined, expired());
          continue;
        }
        reader.active = job;
        reader.child.send(
          {
            type: 'read',
            incarnation: reader.incarnation,
            request: job.request,
            deadline: job.deadline,
            operation: job.operation,
          },
          (error) => {
            if (error) void this.retire(reader);
          },
        );
        break;
      }
    }
  }
  read<T>(operation: MetricsRead): Promise<T> {
    if (!this.started || this.closing) return Promise.reject(unavailable());
    const deadline = (
      process.hrtime.bigint() + BigInt(Math.round(this.config.read_seconds * 1e9))
    ).toString();
    return new Promise<T>((resolve, reject) => {
      const job: Pending = {
        operation,
        request: randomUUID(),
        deadline,
        resolve: (value) => resolve(value as T),
        reject,
        settled: false,
        timer: setTimeout(() => {
          const reader = [...this.readers].find((candidate) => candidate.active === job);
          void this.expire(job, reader);
        }, this.config.read_seconds * 1000),
      };
      this.queue.push(job);
      this.dispatch();
      this.fill_deficit();
    });
  }
  private fail_queue(): void {
    for (const job of this.queue.splice(0)) this.settle(job, undefined, unavailable());
  }
  async close(): Promise<void> {
    this.closing = true;
    this.children.seal();
    this.started = false;
    this.fail_queue();
    if (this.retry_timer) clearTimeout(this.retry_timer);
    this.retry_wake?.();
    await Promise.all([...[...this.readers].map((reader) => this.retire(reader)), this.replacing]);
    await this.children.settled();
  }
}
