/** Permanent shutdown owns actual producers and cleanup, separately from cancelable maintenance. */
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { remember_model_catalog_failure } from '../../../conversation/backends/catalog_cleanup.ts';
import type {
  MacRunnerLifetimeContract,
  MacRunnerLifetimeDependencies,
} from './lifetime_contracts.ts';

function cleanup_failure(error: unknown): ServerLifecycleError {
  return error instanceof ServerLifecycleError
    ? error
    : Object.assign(new ServerLifecycleError('Mac runner cleanup failed.'), { cause: error });
}

export class MacRunnerLifetime implements MacRunnerLifetimeContract {
  private readonly dependencies: MacRunnerLifetimeDependencies;
  private readonly failures = new Set<unknown>();
  private permanent = false;
  private completed = false;
  private cleanup: Promise<void> | undefined;
  private resolve_actual!: () => void;
  private reject_actual!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.resolve_actual = resolve;
    this.reject_actual = reject;
  });
  private fault_reported = false;
  constructor(dependencies: MacRunnerLifetimeDependencies) {
    this.dependencies = dependencies;
    void this.actual.catch(() => {});
  }
  get permanently_stopped(): boolean {
    return this.permanent;
  }
  seal(): void {
    this.permanent = true;
    this.dependencies.state.seal_admission();
  }
  private record_failure(error: unknown): void {
    remember_model_catalog_failure(this.failures, error);
    if (!this.fault_reported) {
      this.fault_reported = true;
      try {
        this.dependencies.on_failure(error instanceof Error ? error : new Error(String(error)));
      } catch (callback) {
        remember_model_catalog_failure(this.failures, callback);
      }
    }
  }
  stop(options: StopOptions): Promise<void> {
    if (this.completed)
      return observe_shutdown(this.actual, options, 'Mac runner cleanup remains unfinished.').catch(
        (error: unknown) => {
          throw cleanup_failure(error);
        },
      );
    this.seal();
    this.dependencies.retirement.begin_shutdown(options);
    // Reserve actual maintenance operations before native cleanup can release
    // their continuations and remove rejected operations from its active set.
    const maintenance_observed = this.dependencies.maintenance.finish_operations(options);
    const maintenance = this.dependencies.maintenance.settled_operations();
    for (const child of this.dependencies.native_children.retained_children()) {
      this.dependencies.state.begin_retirement(child.reference.child_id);
      this.dependencies.native_children.begin_retirement(child.reference);
    }
    for (const record of this.dependencies.commands().records())
      this.dependencies.commands().withdraw(record.command.command_id);
    // Repeated calls retry retained native/skills closes while actual custody remains pending.
    const native = Promise.resolve().then(() => this.dependencies.native_children.stop(options));
    void native.catch((error) => {
      if (!(error instanceof ShutdownDeadlineExceeded)) this.record_failure(error);
    });
    if (!this.cleanup) {
      const task = Promise.resolve().then(() => this.drain(maintenance));
      this.cleanup = task;
      void task.catch((error) => {
        this.record_failure(error);
        if (this.cleanup === task) this.cleanup = undefined;
      });
    }
    return observe_shutdown(
      Promise.all([native, maintenance_observed, this.cleanup]),
      options,
      'Mac runner work remains unfinished at the shutdown deadline.',
    ).catch((error: unknown) => {
      throw cleanup_failure(error);
    });
  }
  private async drain(maintenance: Promise<readonly unknown[]>): Promise<void> {
    for (const error of await maintenance) this.record_failure(error);
    const commands = this.dependencies.commands();
    while (commands.records().some((record) => record.task !== null)) {
      const results = await Promise.allSettled(
        commands.records().flatMap((record) => (record.task === null ? [] : [record.task])),
      );
      for (const result of results)
        if (result.status === 'rejected') this.record_failure(result.reason);
    }
    await commands.settled_replays();
    // A known outcome keeps its exact settlement retry until durable work succeeds.
    await commands.settle_known();
    const native = await Promise.allSettled([
      this.dependencies.retirement.settled(),
      this.dependencies.native_children.settled(),
    ]);
    for (const result of native)
      if (result.status === 'rejected') this.record_failure(result.reason);
    await this.dependencies.sources.finish();
    this.dependencies.sources.seal();
    for (const error of this.dependencies.sources.errors()) this.record_failure(error);
    this.completed = true;
    if (this.failures.size)
      this.reject_actual(new AggregateError(this.failures, 'Mac runner cleanup failed.'));
    else this.resolve_actual();
    await this.actual;
  }
  settled(): Promise<void> {
    return this.actual;
  }
}
