/** A stop certificate follows the exact child's command and source publications. */
import type { ChildCommand, CommandOutcome } from '../../wire.generated.ts';
import type { MacCommandWork } from './command_contracts.ts';
import type { MacRetirementContract, MacRetirementDependencies } from './retirement_contracts.ts';
import { error_outcome, outcome } from './logic/outcomes.ts';

export class MacRetirement implements MacRetirementContract {
  private readonly dependencies: MacRetirementDependencies;
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
    try {
      // Preserve Python's early uncertain result if a currently joined command fails.
      // Continuing native cleanup across that failure still needs the Manager's decision.
      await Promise.all(
        records
          .filter((record) => record.command.operation !== 'stop' && record.task !== null)
          .map((record) => record.task!),
      );
      await this.dependencies.sources.finish(reference);
      await this.dependencies.native_children.stop_child(reference, invoking);
      await this.dependencies.sources.finish(reference);
      if (
        this.dependencies.sources.failed(reference) ||
        work.records(reference).some((record) => record.has_outputs && !record.outputs_published)
      )
        throw Object.assign(new Error('required source publication failed'), {
          name: 'RuntimeError',
        });
    } catch (error) {
      return outcome(command, 'uncertain', error_outcome(command, error).result);
    }
    this.dependencies.state.mark_retired(reference);
    this.dependencies.native_children.retire(reference);
    return outcome(command, 'succeeded', {});
  }
}
