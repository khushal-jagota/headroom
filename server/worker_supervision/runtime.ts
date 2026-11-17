import { AsyncLocalStorage } from 'node:async_hooks';
import { observe_shutdown } from '../conversation/shutdown.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { monotonic_seconds, SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';
/** One shared observation scope. Native processes remain with the invocation owner. */
import type {
  InspectionContext,
  WorkerCgroupPrimitives,
  WorkerProcessPrimitives,
  WorkerJobPrimitives,
} from './contracts.ts';
import { FreshProcessObservations, SharedProcessObserver } from './observation.ts';
export class WorkerSupervisionRuntime {
  readonly primitives: WorkerProcessPrimitives;
  readonly cgroups: WorkerCgroupPrimitives | undefined;
  readonly jobs: WorkerJobPrimitives | undefined;
  readonly active_jobs = new Set<string>();
  readonly clock: () => number;
  /** Record paths a durable owner still names. Without it, no record is pruned. */
  readonly record_paths_in_use: (() => ReadonlySet<string>) | undefined;
  readonly fresh: FreshProcessObservations;
  readonly observer: SharedProcessObserver;
  readonly #operations = new Set<Promise<unknown>>();
  #closed = false;
  #closing: Promise<void> | null = null;
  readonly #scope = new AsyncLocalStorage<{ active: boolean }>();
  constructor(options: {
    primitives: WorkerProcessPrimitives;
    cgroups?: WorkerCgroupPrimitives;
    jobs?: WorkerJobPrimitives;
    clock?: () => number;
    record_paths_in_use?: () => ReadonlySet<string>;
  }) {
    this.primitives = options.primitives;
    this.cgroups = options.cgroups;
    this.jobs = options.jobs;
    this.clock = options.clock ?? (() => performance.now() / 1000);
    this.record_paths_in_use = options.record_paths_in_use;
    this.fresh = new FreshProcessObservations(this.clock);
    this.observer = new SharedProcessObserver((deadline) => this.context(deadline), this.clock);
  }
  get closed(): boolean {
    return this.#closed;
  }
  async context(deadline: number): Promise<InspectionContext> {
    if (this.#closed && !this.#scope.getStore()?.active)
      throw new Error('Process supervision is closed');
    const host = await this.primitives.host_boot(deadline);
    if (!host.host || !host.boot) throw new Error('Worker host and boot identity are unavailable');
    return { primitives: this.primitives, host };
  }
  operation<T>(operation: () => Promise<T>): Promise<T> {
    const parent = this.#scope.getStore();
    if (this.#closed && !parent?.active)
      return Promise.reject(new Error('Process supervision is closed'));
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
  get shutdown_settled(): Promise<void> {
    return this.#closing ?? Promise.resolve();
  }
  close(
    options: StopOptions = { deadline: monotonic_seconds() + SHUTDOWN_GRACE_SECONDS },
  ): Promise<void> {
    if (this.#closing === null) {
      this.#closed = true;
      this.#closing = this.#close_owned();
    }
    return observe_shutdown(
      this.#closing,
      options,
      'Worker supervision metadata remains unsettled',
    );
  }
  async #close_owned(): Promise<void> {
    await this.observer.close();
    while (this.#operations.size) await Promise.allSettled([...this.#operations]);
    await this.fresh.close();
  }
}
