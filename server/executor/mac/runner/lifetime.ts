/** Permanent shutdown owns actual producers and cleanup, separately from cancelable maintenance. */
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
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
interface StopAttempt {
  readonly actual: Promise<void>;
  resolve(): void;
  reject(error: unknown): void;
  failed: boolean;
}
function stop_attempt(): StopAttempt {
  let resolve!: () => void, reject!: (error: unknown) => void;
  const actual = new Promise<void>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  void actual.catch(() => {});
  return { actual, resolve, reject, failed: false };
}

export class MacRunnerLifetime implements MacRunnerLifetimeContract {
  private readonly dependencies: MacRunnerLifetimeDependencies;
  private readonly logged_failures = new Set<unknown>();
  private readonly callback_failures = new Set<unknown>();
  private permanent = false;
  private completed = false;
  private cleanup: Promise<void> | undefined;
  private attempt = stop_attempt();
  private maintenance_consumed = false;
  private fault_reported = false;
  constructor(dependencies: MacRunnerLifetimeDependencies) {
    this.dependencies = dependencies;
  }
  get permanently_stopped(): boolean {
    return this.permanent;
  }
  seal(): void {
    this.permanent = true;
    this.dependencies.state.seal_admission();
  }
  private record_failure(error: unknown, notify = true): void {
    const errors = new Set<unknown>();
    remember_model_catalog_failure(errors, error);
    for (const cause of errors)
      if (!this.logged_failures.has(cause)) {
        this.logged_failures.add(cause);
        console.warn('Mac runner cleanup failed:', cause);
      }
    if (notify && !this.fault_reported) {
      this.fault_reported = true;
      try {
        this.dependencies.on_failure(error instanceof Error ? error : new Error(String(error)));
      } catch (callback) {
        remember_model_catalog_failure(this.callback_failures, callback);
        this.record_failure(callback, false);
      }
    }
  }
  stop(options: StopOptions): Promise<void> {
    if (this.completed)
      return observe_shutdown(
        this.attempt.actual,
        options,
        'Mac runner cleanup remains unfinished.',
      ).catch((error: unknown) => {
        throw cleanup_failure(error);
      });
    if (!this.cleanup && this.attempt.failed) this.attempt = stop_attempt();
    const attempt = this.attempt;
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
    void native.catch(() => {});
    if (!this.cleanup) {
      const task = Promise.resolve().then(() => this.drain(maintenance));
      this.cleanup = task;
      void task.then(
        () => {
          this.completed = true;
          attempt.resolve();
        },
        (error) => {
          if (this.cleanup === task) this.cleanup = undefined;
          attempt.failed = true;
          attempt.reject(error);
          this.record_failure(error);
        },
      );
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
    const attempt_failures = new Set<unknown>();
    if (!this.maintenance_consumed) {
      const completed = await maintenance;
      this.maintenance_consumed = true;
      for (const error of completed) {
        remember_model_catalog_failure(attempt_failures, error);
        this.record_failure(error, false);
      }
    }
    const commands = this.dependencies.commands();
    while (commands.records().some((record) => record.task !== null)) {
      const results = await Promise.allSettled(
        commands.records().flatMap((record) => (record.task === null ? [] : [record.task])),
      );
      for (const result of results)
        if (result.status === 'rejected') this.record_failure(result.reason, false);
    }
    await commands.settled_replays();
    // A known outcome keeps its exact settlement retry until durable work succeeds.
    try {
      await commands.settle_known();
    } catch (error) {
      remember_model_catalog_failure(attempt_failures, error);
      this.record_failure(error, false);
    }
    const native = await Promise.allSettled([
      this.dependencies.retirement.settled(),
      this.dependencies.native_children.settled(),
    ]);
    for (const result of native)
      if (result.status === 'rejected') {
        remember_model_catalog_failure(attempt_failures, result.reason);
        this.record_failure(result.reason, false);
      }
    await this.dependencies.sources.finish();
    this.dependencies.sources.seal();
    for (const error of this.dependencies.sources.errors()) {
      remember_model_catalog_failure(attempt_failures, error);
      this.record_failure(error, false);
    }
    for (const error of this.callback_failures)
      remember_model_catalog_failure(attempt_failures, error);
    if (attempt_failures.size)
      throw new AggregateError(attempt_failures, 'Mac runner cleanup failed.');
  }
  settled(): Promise<void> {
    return this.attempt.actual;
  }
}
