/** Command identity survives failed durable steps. Actual work ends independently of dedupe. */
import { same_reference } from '../../child_reference.ts';
import { stringify_pydantic_json } from '../../../core/http/numeric_json.ts';
import {
  BackendSpawnFailed,
  PromptWriteRefused,
} from '../../../conversation/backends/contracts.ts';
import { decode_json, decode_value, encode_value, ValueError } from '../../protocol.ts';
import { class_name } from '../../protocol_errors.ts';
import type { ChildCommand, ChildReference, CommandOutcome } from '../../wire.generated.ts';
import { read_canonical_body } from '../mailbox/poll.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import { MacChildAdmissionRefused } from './contracts.ts';
import type {
  MacCommandDependencies,
  MacCommandRecordView,
  MacCommandServiceContract,
  OwedOutput,
} from './command_contracts.ts';
import { dispatch_command } from './commands.ts';
import {
  error_message,
  error_outcome,
  owed_outputs,
  same_wire,
} from './logic/outcomes.ts';

class Record implements MacCommandRecordView {
  readonly command: ChildCommand;
  readonly journaled: boolean;
  task: Promise<CommandOutcome> | null = null;
  outcome: CommandOutcome | null = null;
  invoking = false;
  preparation: AbortController | null = null;
  journal_finished = false;
  outputs: readonly OwedOutput[] | null = null;
  next_output = 0;
  outputs_published = false;
  settlement: Promise<void> | undefined;
  pruned = false;
  constructor(command: ChildCommand, journaled: boolean) {
    this.command = command;
    this.journaled = journaled;
  }
  get phase(): 'preparing' | 'invoking' | 'finished' {
    return this.outcome !== null ? 'finished' : this.invoking ? 'invoking' : 'preparing';
  }
  get has_outputs(): boolean {
    return this.outputs !== null && this.outputs.length !== 0;
  }
  get settled(): boolean {
    return (
      (!this.journaled || this.journal_finished) &&
      this.outputs !== null &&
      (this.outputs.length === 0 || this.outputs_published)
    );
  }
}
const withdrawal_message = 'command withdrawn before provider invocation';
async function borrowed_bytes(reader: CanonicalReader): Promise<Uint8Array> {
  // read_canonical_body owns only this borrowed wrapper. The mailbox caller
  // retains the underlying reader and closes it in its own finally block.
  return read_canonical_body({
    byte_count: reader.byte_count,
    [Symbol.asyncIterator]: () => reader[Symbol.asyncIterator](),
    async close() {},
  });
}
export class MacCommandService implements MacCommandServiceContract {
  private readonly dependencies: MacCommandDependencies;
  private readonly commands = new Map<string, Record>();
  private readonly withdrawn = new Set<string>();
  private readonly start_failed = new Map<string, CommandOutcome>();
  private readonly replays = new Set<Promise<void>>();
  constructor(dependencies: MacCommandDependencies) {
    this.dependencies = dependencies;
  }
  records(reference?: ChildReference): readonly MacCommandRecordView[] {
    return [...this.commands.values()].filter(
      (record) => reference === undefined || same_reference(record.command.child, reference),
    );
  }
  prune(reference: ChildReference): void {
    for (const [id, record] of this.commands) {
      if (!same_reference(record.command.child, reference)) continue;
      record.pruned = true;
      this.withdrawn.delete(id);
      if (record.task === null && record.settled) this.commands.delete(id);
    }
    const failed = this.start_failed.get(reference.child_id);
    if (failed && same_reference(failed.child, reference))
      this.start_failed.delete(reference.child_id);
  }
  withdraw(command_id: string): boolean {
    if (this.withdrawn.has(command_id)) return true;
    const record = this.commands.get(command_id);
    if (record && (record.invoking || record.outcome !== null)) return false;
    this.withdrawn.add(command_id);
    record?.preparation?.abort(new MacChildAdmissionRefused(withdrawal_message));
    return true;
  }
  run(input: ChildCommand): Promise<CommandOutcome> {
    const command = decode_value(input, 'ChildCommand');
    let record = this.commands.get(command.command_id);
    if (record && (!record.journaled || !same_wire(record.command, command, 'ChildCommand')))
      return Promise.reject(new ValueError('command id reused with different content'));
    if (!record) {
      record = new Record(command, true);
      this.commands.set(command.command_id, record);
    }
    if (record.task !== null) return record.task;
    if (record.outcome !== null && record.settled) return Promise.resolve(record.outcome);
    return this.own(record, () =>
      record!.outcome !== null ? this.finish_known(record!) : this.execute(record!),
    );
  }
  adopt_unjournaled_outcome(input: ChildCommand, value: CommandOutcome): Promise<CommandOutcome> {
    const command = decode_value(input, 'ChildCommand');
    const result = decode_value(value, 'CommandOutcome');
    if (command.command_id !== result.command_id || !same_reference(command.child, result.child))
      return Promise.reject(new ValueError('outcome conflicts with command'));
    let record = this.commands.get(command.command_id);
    if (
      record &&
      (record.journaled ||
        !same_wire(record.command, command, 'ChildCommand') ||
        record.outcome === null ||
        !same_wire(record.outcome, result, 'CommandOutcome'))
    )
      return Promise.reject(new ValueError('command id reused with different content'));
    if (!record) {
      record = new Record(command, false);
      record.outcome = result;
      this.commands.set(command.command_id, record);
    }
    if (record.task !== null) return record.task;
    if (record.settled) return Promise.resolve(record.outcome!);
    return this.own(record, () => this.finish_known(record!));
  }
  /** Publish the actual promise before any preparation, native call, or durable write can reenter. */
  private own(record: Record, operation: () => Promise<CommandOutcome>): Promise<CommandOutcome> {
    const task = Promise.resolve().then(operation);
    record.task = task;
    const finished = () => {
      if (record.task === task) record.task = null;
      if (record.pruned && record.settled) this.commands.delete(record.command.command_id);
    };
    void task.then(finished, finished);
    return task;
  }
  private mark_invoking(record: Record): void {
    if (this.withdrawn.has(record.command.command_id))
      throw new MacChildAdmissionRefused(withdrawal_message);
    record.invoking = true;
  }
  private async prepare(record: Record): Promise<void> {
    const command = record.command;
    if (this.withdrawn.has(command.command_id))
      throw new MacChildAdmissionRefused(withdrawal_message);
    const args = command.arguments;
    const content =
      args.kind === 'write_prompt' || args.kind === 'steer'
        ? [...args.content, ...(args.sender_content ?? [])]
        : [];
    if (
      command.operation !== 'stop' &&
      content.some((piece) => class_name(piece) === 'MessageFile')
    ) {
      const preparation = new AbortController();
      record.preparation = preparation;
      try {
        await this.dependencies.inputs.download_inputs(command, preparation.signal);
      } catch (error) {
        if (!this.withdrawn.has(command.command_id)) {
          const refusal = command.operation === 'start' ? BackendSpawnFailed : PromptWriteRefused;
          throw new refusal(error_message(error));
        }
      } finally {
        record.preparation = null;
      }
    }
    if (this.withdrawn.has(command.command_id))
      throw new MacChildAdmissionRefused(withdrawal_message);
  }
  private async execute(record: Record): Promise<CommandOutcome> {
    let result: CommandOutcome;
    try {
      await this.prepare(record);
      result = await dispatch_command(
        this.dependencies,
        record.command,
        this.start_failed,
        this,
        () => this.mark_invoking(record),
      );
    } catch (error) {
      result = error_outcome(record.command, error);
    }
    const child_id = record.command.child.child_id;
    if (
      record.command.operation === 'start' &&
      result.disposition !== 'succeeded' &&
      !this.dependencies.state.is_retiring(child_id) &&
      !this.dependencies.state.is_retired(child_id)
    )
      this.start_failed.set(child_id, result);
    // Retain the known provider fate before any output derivation or storage can fail.
    record.outcome = result;
    await this.settle(record);
    return result;
  }
  private async finish_known(record: Record): Promise<CommandOutcome> {
    await this.settle(record);
    return record.outcome!;
  }
  private settle(record: Record): Promise<void> {
    if (!record.settlement) {
      const task = Promise.resolve().then(async () => {
        const result = record.outcome!;
        record.outputs ??= owed_outputs(record.command, result);
        if (record.journaled && !record.journal_finished) {
          await this.dependencies.journal.finish(
            record.command.command_id,
            stringify_pydantic_json(
              encode_value(
                {
                  command_id: record.command.command_id,
                  child_outcome: result,
                },
                'CommandResult',
              ),
            ),
            record.has_outputs,
          );
          record.journal_finished = true;
        }
        if (record.outputs_published || record.outputs.length === 0) return;
        while (record.next_output < record.outputs.length) {
          await this.dependencies.publication.add(record.outputs[record.next_output]!);
          record.next_output++;
        }
        if (record.journaled)
          await this.dependencies.journal.mark_outputs_published(record.command.command_id);
        record.outputs_published = true;
      });
      record.settlement = task;
      void task.catch(() => {
        if (record.settlement === task) record.settlement = undefined;
      });
    }
    return record.settlement;
  }
  async settle_known(reference?: ChildReference): Promise<void> {
    const records = [...this.commands.values()].filter(
      (record) =>
        record.outcome !== null &&
        !record.settled &&
        (reference === undefined || same_reference(record.command.child, reference)),
    );
    await Promise.all(
      records.map((record) => record.task ?? this.own(record, () => this.finish_known(record))),
    );
  }
  publish_owed_outputs(
    command_reader: CanonicalReader,
    result_reader: CanonicalReader,
  ): Promise<void> {
    const task = Promise.resolve().then(() => this.replay_outputs(command_reader, result_reader));
    this.replays.add(task);
    void task.then(
      () => this.replays.delete(task),
      () => this.replays.delete(task),
    );
    return task;
  }
  async settled_replays(): Promise<void> {
    while (this.replays.size) await Promise.allSettled([...this.replays]);
  }
  private async replay_outputs(
    command_reader: CanonicalReader,
    result_reader: CanonicalReader,
  ): Promise<void> {
    const command = decode_json(await borrowed_bytes(command_reader), 'MailboxCommand');
    const result = decode_json(await borrowed_bytes(result_reader), 'CommandResult');
    if (command.child_command != null && result.child_outcome != null)
      for (const entry of owed_outputs(command.child_command, result.child_outcome))
        await this.dependencies.publication.add(entry);
    await this.dependencies.journal.mark_outputs_published(command.command_id);
  }
}
