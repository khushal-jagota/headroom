/** Paired inventories share discovery work without weakening the freshness boundary. */
import { setTimeout as pause } from 'node:timers/promises';
import { observe_promise } from '../core/promise_observer.ts';
import type { InspectionContext, ProcessObservation, WorkerProcess } from './contracts.ts';
import { same_process } from './logic/ownership.ts';
import {
  INSPECTION_SECONDS,
  OBSERVATION_SECONDS,
  OBSERVATION_ERROR_LOG_SECONDS,
  STOP_OBSERVATION_BATCH_SECONDS,
} from './config.ts';

export function index_processes(table: ReadonlyMap<number, WorkerProcess>): {
  children: Map<number, WorkerProcess[]>;
  sessions: Map<number, WorkerProcess[]>;
} {
  const children = new Map<number, WorkerProcess[]>(),
    sessions = new Map<number, WorkerProcess[]>();
  for (const process of table.values())
    if (!process.zombie) {
      const child_bucket = children.get(process.parent);
      if (child_bucket) child_bucket.push(process);
      else children.set(process.parent, [process]);
      const session_bucket = sessions.get(process.session);
      if (session_bucket) session_bucket.push(process);
      else sessions.set(process.session, [process]);
    }
  return { children, sessions };
}
export async function capture_observation(
  context: InspectionContext,
  deadline: number,
): Promise<ProcessObservation> {
  const first = await context.primitives.snapshot(deadline);
  const second = await context.primitives.snapshot(deadline);
  const stable = new Map(
    [...second].filter(([pid, process]) => same_process(process, first.get(pid))),
  );
  return { stable, ...index_processes(stable), latest_sessions: index_processes(second).sessions };
}
interface ObservationRequest {
  context: InspectionContext;
  deadline: number;
  active: boolean;
  resolve(value: ProcessObservation): void;
  reject(error: unknown): void;
}
interface ObservationGroup {
  pending: ObservationRequest[];
  running: boolean;
}
export class FreshProcessObservations {
  readonly #groups = new Map<string, ObservationGroup>();
  readonly #pumps = new Set<Promise<void>>();
  readonly #clock: () => number;
  #closed = false;
  constructor(clock: () => number) {
    this.#clock = clock;
  }
  async capture(context: InspectionContext, deadline: number): Promise<ProcessObservation> {
    if (this.#closed) throw new Error('Worker process observations are closed');
    const key = JSON.stringify([context.host.host, context.host.boot]);
    const group = this.#groups.get(key) ?? { pending: [], running: false };
    this.#groups.set(key, group);
    const pending = Promise.withResolvers<ProcessObservation>();
    const request: ObservationRequest = {
      context,
      deadline,
      active: true,
      resolve: pending.resolve,
      reject: pending.reject,
    };
    group.pending.push(request);
    if (!group.running) {
      group.running = true;
      const pump = this.#pump(key, group);
      this.#pumps.add(pump);
      void pump.finally(() => this.#pumps.delete(pump)).catch(() => {});
    }
    try {
      const result = await observe_promise(
        pending.promise,
        Math.max(0, deadline - this.#clock()) * 1000,
      );
      if (!result.completed || this.#clock() >= deadline)
        throw new DOMException('Worker inspection deadline expired', 'TimeoutError');
      return result.value;
    } finally {
      request.active = false;
    }
  }
  async #pump(key: string, group: ObservationGroup): Promise<void> {
    try {
      while (group.pending.length) {
        const leader = group.pending.find((request) => request.active);
        if (!leader) break;
        await pause(
          Math.min(
            STOP_OBSERVATION_BATCH_SECONDS,
            Math.max(0, leader.deadline - this.#clock()) / 4,
          ) * 1000,
        );
        // Detach the cohort before the first inventory starts. A later caller gets a fresh pair.
        const batch = group.pending.splice(0).filter((request) => request.active);
        const alive = batch.filter((request) => request.deadline > this.#clock());
        for (const request of batch)
          if (!alive.includes(request))
            request.reject(new DOMException('Worker inspection deadline expired', 'TimeoutError'));
        if (!alive.length) continue;
        try {
          // The shorter observer cannot cancel a longer member of the same cohort.
          const pair = await capture_observation(
            alive[0].context,
            Math.max(...alive.map((request) => request.deadline)),
          );
          for (const request of alive) request.resolve(pair);
        } catch (error) {
          for (const request of alive) {
            if (
              error instanceof Error &&
              error.name === 'TimeoutError' &&
              request.active &&
              request.deadline > this.#clock()
            )
              group.pending.push(request);
            else request.reject(error);
          }
        }
      }
    } finally {
      group.running = false;
      if (this.#groups.get(key) === group) this.#groups.delete(key);
    }
  }
  async close(): Promise<void> {
    this.#closed = true;
    for (const group of this.#groups.values())
      for (const request of group.pending.splice(0))
        request.reject(new Error('Worker process observations closed'));
    await Promise.allSettled([...this.#pumps]);
  }
}
export type ObservationCallback = (
  context: InspectionContext | null,
  observation: ProcessObservation | null,
) => Promise<void>;
export class SharedProcessObserver {
  readonly #callbacks = new Map<string, ObservationCallback>();
  readonly #context: (deadline: number) => Promise<InspectionContext>;
  readonly #clock: () => number;
  readonly #next_log = new Map<string, number>();
  #timer: ReturnType<typeof setTimeout> | null = null;
  #pass: Promise<void> | null = null;
  #closed = false;
  constructor(context: (deadline: number) => Promise<InspectionContext>, clock: () => number) {
    this.#context = context;
    this.#clock = clock;
  }
  register(path: string, callback: ObservationCallback): void {
    if (this.#closed) throw new Error('Worker process observer is closed');
    this.#callbacks.set(path, callback);
    this.#schedule();
  }
  async unregister(path: string): Promise<void> {
    this.#callbacks.delete(path);
    this.#next_log.delete(path);
    if (!this.#callbacks.size && this.#timer !== null) {
      clearTimeout(this.#timer);
      this.#timer = null;
    }
  }
  #schedule(): void {
    if (this.#closed || this.#timer !== null || this.#pass !== null || !this.#callbacks.size)
      return;
    this.#timer = setTimeout(() => {
      this.#timer = null;
      const pass = this.#run();
      this.#pass = pass;
      void pass
        .finally(() => {
          this.#pass = null;
          this.#schedule();
        })
        .catch((error) => this.#log('', error));
    }, OBSERVATION_SECONDS * 1000);
  }
  #log(path: string, error: unknown): void {
    if (this.#clock() < (this.#next_log.get(path) ?? -Infinity)) return;
    this.#next_log.set(path, this.#clock() + OBSERVATION_ERROR_LOG_SECONDS);
    try {
      console.error('Worker process observation failed', path, error);
    } catch {
      /* Diagnostics cannot stop custody for another Worker. */
    }
  }
  async #run(): Promise<void> {
    let context: InspectionContext | null = null,
      observation: ProcessObservation | null = null;
    try {
      const deadline = this.#clock() + INSPECTION_SECONDS;
      context = await this.#context(deadline);
      observation = await capture_observation(context, deadline);
    } catch (error) {
      this.#log('', error);
    }
    for (const [path, callback] of [...this.#callbacks]) {
      if (this.#callbacks.get(path) !== callback) continue;
      try {
        await callback(context, observation);
      } catch (error) {
        this.#log(path, error);
      }
    }
  }
  async close(): Promise<void> {
    this.#closed = true;
    this.#callbacks.clear();
    if (this.#timer !== null) clearTimeout(this.#timer);
    this.#timer = null;
    await this.#pass;
  }
}
