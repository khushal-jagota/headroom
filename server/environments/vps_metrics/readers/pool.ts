import { fork } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
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

/** Queue deadlines start on admission, after the pool's cold-start READY boundary. */
export class MetricsReaders {
  private readers = new Set<Reader>();
  private queue: Pending[] = [];
  private closing = false;
  private started = false;
  private starting?: Promise<void>;
  readonly path: string;
  readonly config: MetricsConfig;
  constructor(path: string, config: MetricsConfig) {
    this.path = path;
    this.config = config;
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
    const child = fork(new URL('./child.ts', import.meta.url), [], {
      stdio: ['ignore', 'ignore', 'pipe', 'ipc', 'pipe'],
    });
    child.stderr?.on('data', (chunk: Buffer) => console.error(chunk.toString()));
    const liveness = child.stdio[4] as Duplex;
    let resolve_reaped!: () => void;
    const reader: Reader = {
      child,
      incarnation,
      liveness,
      ready: false,
      reaped: new Promise<void>((resolve) => {
        resolve_reaped = resolve;
      }),
    };
    this.readers.add(reader);
    return new Promise<void>((resolve, reject) => {
      let admitted = false;
      child.on('error', () => {
        void this.retire(reader);
      });
      child.once('close', () => {
        resolve_reaped();
        liveness.destroy();
        this.readers.delete(reader);
        if (!admitted) reject(unavailable());
        if (!reader.retiring) {
          if (reader.active) this.settle(reader.active, undefined, unavailable());
          if (admitted && !this.closing) void this.replace();
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
  private async replace(): Promise<void> {
    try {
      await this.spawn();
    } catch {
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
      reader.child.kill('SIGKILL');
      await reader.reaped;
      if (reader.active) this.settle(reader.active, undefined, expired());
      reader.active = undefined;
      if (!this.closing) await this.replace();
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
    });
  }
  private fail_queue(): void {
    for (const job of this.queue.splice(0)) this.settle(job, undefined, unavailable());
  }
  async close(): Promise<void> {
    this.closing = true;
    this.started = false;
    this.fail_queue();
    await Promise.all([...this.readers].map((reader) => this.retire(reader)));
  }
}
