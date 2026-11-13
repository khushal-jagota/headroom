/** A stop certificate follows the exact child's command and source publications. */
import type { ChildCommand, ChildReference, CommandOutcome } from '../../wire.generated.ts';
import type { MacCommandWork } from './command_contracts.ts';
import type { MacRetirementContract, MacRetirementDependencies } from './retirement_contracts.ts';
import { error_message, error_outcome, outcome, same_reference } from './logic/outcomes.ts';

export class MacRetirement implements MacRetirementContract {
  private readonly dependencies: MacRetirementDependencies;
  private readonly joined_failures: { reference: ChildReference; errors: Set<unknown> }[] = [];
  constructor(dependencies: MacRetirementDependencies) {
    this.dependencies = dependencies;
  }
  async stop_exact(
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
    for (const result of joined) if (result.status === 'rejected') errors.add(result.reason);
    if (!previous && errors.size) this.joined_failures.push({ reference, errors });
    const collect = async (operation: () => Promise<void>) => {
      try {
        await operation();
      } catch (error) {
        errors.add(error);
      }
    };
    await collect(() => this.dependencies.sources.finish(reference));
    await collect(() => this.dependencies.native_children.stop_child(reference, invoking));
    await collect(() => this.dependencies.sources.finish(reference));
    const failures = [...errors];
    if (
      this.dependencies.sources.failed(reference) ||
      work.records(reference).some((record) => record.has_outputs && !record.outputs_published)
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
    return outcome(command, 'succeeded', {});
  }
}
