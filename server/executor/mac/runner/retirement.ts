/** A stop certificate follows the exact child's command and source publications. */
import type { ChildCommand, ChildReference, CommandOutcome } from '../../wire.generated.ts';
import type { MacCommandWork } from './command_contracts.ts';
import type { MacRetirementContract, MacRetirementDependencies } from './retirement_contracts.ts';
import { error_message, error_outcome, outcome, same_reference } from './logic/outcomes.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { remember_model_catalog_failure } from '../../../conversation/backends/catalog_cleanup.ts';

export class MacRetirement implements MacRetirementContract {
  private readonly dependencies: MacRetirementDependencies;
  private readonly joined_failures: { reference: ChildReference; errors: Set<unknown> }[] = [];
  private shutdown_options: StopOptions | undefined;
  private readonly shutdown = new AbortController();
  private readonly operations = new Set<Promise<CommandOutcome>>();
  private readonly failures = new Set<unknown>();
  private actual_join: Promise<void> | undefined;
  constructor(dependencies: MacRetirementDependencies) {
    this.dependencies = dependencies;
  }
  begin_shutdown(options: StopOptions): void {
    if (this.shutdown_options) return;
    this.shutdown_options = options;
    this.shutdown.abort();
  }
  async observe_work<T>(actual: Promise<T>): Promise<T> {
    let started!: () => void;
    const stopped = new Promise<void>((resolve) => {
      started = resolve;
    });
    this.shutdown.signal.addEventListener('abort', started, { once: true });
    try {
      if (!this.shutdown_options) await Promise.race([actual, stopped]);
      if (this.shutdown_options)
        await observe_shutdown(
          actual,
          this.shutdown_options,
          'Mac runner work remains unfinished.',
        );
      return await actual;
    } finally {
      this.shutdown.signal.removeEventListener('abort', started);
    }
  }
  settled(): Promise<void> {
    this.actual_join ??= (async () => {
      while (this.operations.size) await Promise.allSettled([...this.operations]);
      if (this.failures.size)
        throw new AggregateError(this.failures, 'Mac exact-stop work failed.');
    })();
    return this.actual_join;
  }
  prune(reference: ChildReference): void {
    for (let index = this.joined_failures.length - 1; index >= 0; index--)
      if (same_reference(this.joined_failures[index]!.reference, reference))
        this.joined_failures.splice(index, 1);
  }
  stop_exact(
    command: ChildCommand,
    work: MacCommandWork,
    invoking: () => void,
  ): Promise<CommandOutcome> {
    const actual = Promise.resolve().then(() => this.retire(command, work, invoking));
    this.operations.add(actual);
    void actual.then(
      () => this.operations.delete(actual),
      (error) => {
        remember_model_catalog_failure(this.failures, error);
        this.operations.delete(actual);
      },
    );
    return this.observe_work(actual);
  }
  private async retire(
    command: ChildCommand,
    work: MacCommandWork,
    invoking: () => void,
  ): Promise<CommandOutcome> {
    const reference = command.child;
    const records = work.records(reference);
    const owner = records.find(
      (record) => record.task !== null && record.command.operation === 'stop',
    );
    if (owner && owner.command.command_id !== command.command_id)
      return outcome(command, 'refused', { reason: 'child_stop_in_progress' });
    this.dependencies.state.begin_retirement(reference.child_id);
    this.dependencies.native_children.begin_retirement(reference);
    for (const record of records)
      if (record.command.operation !== 'stop') work.withdraw(record.command.command_id);
    const previous = this.joined_failures.find((failure) =>
      same_reference(failure.reference, reference),
    );
    const errors = previous?.errors ?? new Set<unknown>();
    // A rejection must not let another actual command escape the native-stop barrier.
    const joined = await Promise.allSettled(
      records
        .filter((record) => record.command.operation !== 'stop' && record.task !== null)
        .map((record) => record.task!),
    );
    for (const result of joined)
      if (result.status === 'rejected') remember_model_catalog_failure(errors, result.reason);
    if (!previous && errors.size) this.joined_failures.push({ reference, errors });
    const collect = async (operation: () => Promise<void>) => {
      try {
        await operation();
      } catch (error) {
        remember_model_catalog_failure(errors, error);
      }
    };
    await collect(() => this.dependencies.sources.finish(reference));
    await collect(() => this.dependencies.native_children.stop_child(reference, invoking));
    await collect(() => this.dependencies.sources.finish(reference));
    const failures = [...errors];
    if (
      errors.size === 0 &&
      (this.dependencies.sources.failed(reference) ||
        work
          .records(reference)
          .some(
            (record) =>
              record.command.command_id !== command.command_id &&
              record.outcome !== null &&
              !record.settled,
          ))
    )
      failures.push(
        Object.assign(new Error('required source publication failed'), {
          name: 'RuntimeError',
        }),
      );
    if (failures.length) {
      const error =
        failures.length === 1
          ? failures[0]
          : new AggregateError(failures, failures.map(error_message).join('; '));
      return outcome(command, 'uncertain', error_outcome(command, error).result);
    }
    this.dependencies.state.mark_retired(reference);
    this.dependencies.native_children.retire(reference);
    this.prune(reference);
    work.prune(reference);
    return outcome(command, 'succeeded', {});
  }
}
