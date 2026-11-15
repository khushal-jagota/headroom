import { AsyncLocalStorage } from 'node:async_hooks';
/** One shared observation scope. Native processes remain with the invocation owner. */
import type {
  InspectionContext,
  WorkerCgroupPrimitives,
  WorkerProcessPrimitives,
} from './contracts.ts';
import { FreshProcessObservations, SharedProcessObserver } from './observation.ts';
export class WorkerSupervisionRuntime {
  readonly primitives: WorkerProcessPrimitives;
  readonly cgroups: WorkerCgroupPrimitives | undefined;
  readonly clock: () => number;
  readonly fresh: FreshProcessObservations;
  readonly observer: SharedProcessObserver;
  readonly #operations = new Set<Promise<unknown>>();
  #closed = false;
  readonly #scope = new AsyncLocalStorage<{ active: boolean }>();
  constructor(options: {
    primitives: WorkerProcessPrimitives;
    cgroups?: WorkerCgroupPrimitives;
    clock?: () => number;
  }) {
    this.primitives = options.primitives;
    this.cgroups = options.cgroups;
    this.clock = options.clock ?? (() => performance.now() / 1000);
    this.fresh = new FreshProcessObservations(this.clock);
    this.observer = new SharedProcessObserver((deadline) => this.context(deadline), this.clock);
  }
  get closed(): boolean {
    return this.#closed;
  }
  async context(deadline: number): Promise<InspectionContext> {
    if (this.#closed && !this.#scope.getStore()?.active)
      throw new Error('Worker supervision is closed');
    const host = await this.primitives.host_boot(deadline);
    if (!host.host || !host.boot) throw new Error('Worker host and boot identity are unavailable');
    return { primitives: this.primitives, host };
  }
  operation<T>(operation: () => Promise<T>): Promise<T> {
    const parent = this.#scope.getStore();
    if (this.#closed && !parent?.active)
      return Promise.reject(new Error('Worker supervision is closed'));
    const token = parent?.active ? parent : { active: true };
    const task = this.#scope.run(token, () => Promise.resolve().then(operation));
    this.#operations.add(task);
    void task
      .finally(() => {
        if (token !== parent) token.active = false;
        this.#operations.delete(task);
      })
      .catch(() => {});
    return task;
  }
  async close(): Promise<void> {
    this.#closed = true;
    await this.observer.close();
    while (this.#operations.size) await Promise.allSettled([...this.#operations]);
    await this.fresh.close();
  }
}
