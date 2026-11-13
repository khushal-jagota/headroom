/** Cancelable maintenance exposes its actual operation join to the complete runner. */
import { same_reference } from '../../child_reference.ts';
import {
  ConversationStartAborted,
  observe_conversation_operation,
} from '../../../runtime/abort.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import { normalize_integer } from '../../integers.ts';
import type { ChildCommand, Quiescence, WorkingTurn } from '../../wire.generated.ts';
import type { MacRunningChild } from './contracts.ts';
import type {
  MacMaintenanceContract,
  MacMaintenanceDependencies,
  MacMaintenanceOperations,
} from './maintenance_contracts.ts';
import { outcome } from './logic/outcomes.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { remember_model_catalog_failure } from '../../../conversation/backends/catalog_cleanup.ts';

class MacMaintenanceCancelled extends ConversationStartAborted {}
function assert_active(signal: AbortSignal): void {
  if (signal.aborted) throw new MacMaintenanceCancelled();
}

export class MacMaintenance implements MacMaintenanceContract, MacMaintenanceOperations {
  private readonly dependencies: MacMaintenanceDependencies;
  private readonly operations = new Set<Promise<unknown>>();
  private readonly failures = new Set<unknown>();
  private readonly update_commands = new Map<string, ChildCommand[]>();
  private finishing: Promise<readonly unknown[]> | undefined;
  private replaying = false;
  private replay_gate: Promise<void> = Promise.resolve();
  private release_replay: (() => void) | undefined;
  constructor(dependencies: MacMaintenanceDependencies) {
    this.dependencies = dependencies;
  }
  get planned_update_replay(): boolean {
    return this.replaying;
  }
  private assert_open(): void {
    if (this.dependencies.permanently_stopped())
      throw new ServerLifecycleError('Mac runner is shutting down.');
  }
  private own<T>(work: () => Promise<T>): Promise<T> {
    const task = Promise.resolve().then(work);
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      () => this.operations.delete(task),
    );
    return task;
  }
  private observe_operation<T>(actual: Promise<T>, signal: AbortSignal): Promise<T> {
    return observe_conversation_operation(
      () => this.dependencies.retirement.observe_work(actual),
      signal,
    ).catch((error: unknown) => {
      if (signal.aborted)
        void actual.catch((failure: unknown) => {
          if (!(failure instanceof MacMaintenanceCancelled))
            remember_model_catalog_failure(this.failures, failure);
        });
      throw error;
    });
  }
  working_turns(): readonly WorkingTurn[] {
    return this.dependencies.native_children.retained_children().flatMap((child) =>
      child.active_turn === null
        ? []
        : [
            {
              child: child.reference,
              turn: child.active_turn,
              tool_running: child.open_tool_calls.size !== 0,
            },
          ],
    );
  }
  private counts() {
    const children = this.dependencies.native_children.retained_children();
    const records = this.dependencies.commands().records();
    const stopped = records
      .filter(
        (record) =>
          record.command.operation === 'stop' &&
          record.outcome?.disposition === 'succeeded' &&
          record.settled &&
          same_reference(record.outcome.child, record.command.child),
      )
      .map((record) => record.command.child);
    const unresolved =
      records.filter((record) => record.task !== null).length +
      records.filter(
        (record) =>
          record.outcome?.disposition === 'uncertain' &&
          !(
            stopped.some((reference) => same_reference(reference, record.outcome!.child)) &&
            !children.some((child) => same_reference(child.reference, record.outcome!.child))
          ),
      ).length +
      records.filter((record) => record.outcome !== null && !record.settled).length +
      this.dependencies.sources.count();
    return {
      active: children.filter((child) => child.active_turn !== null).length,
      callbacks: children.reduce(
        (count, child) => count + this.dependencies.state.count(child.reference),
        0,
      ),
      unresolved,
      unsent: this.dependencies.outbox.pending_count(),
    };
  }
  private busy(counts: ReturnType<MacMaintenance['counts']>): boolean {
    return counts.active !== 0 || counts.callbacks !== 0 || counts.unresolved !== 0;
  }
  quiesce(signal: AbortSignal): Promise<Quiescence> {
    assert_active(signal);
    this.assert_open();
    this.dependencies.state.seal_admission();
    const actual = this.own(async () => {
      let counts = this.counts(),
        stopped = false;
      if (!this.busy(counts)) {
        for (const child of this.dependencies.native_children.retained_children()) {
          assert_active(signal);
          const closed = await child.lock.run(async () => {
            assert_active(signal);
            if (this.busy(this.counts())) return false;
            this.dependencies.native_children.begin_retirement(child.reference);
            await this.dependencies.native_children.stop_native(child.reference);
            this.dependencies.native_children.retire(child.reference);
            this.dependencies.state.clear(child.reference);
            this.dependencies.commands().prune(child.reference);
            return true;
          });
          if (!closed) break;
        }
        counts = this.counts();
        stopped =
          this.dependencies.native_children.retained_children().length === 0 &&
          !this.busy(counts) &&
          counts.unsent === 0n;
      }
      return {
        admission_stopped: true,
        active_turn_count: normalize_integer(counts.active),
        pending_callback_count: normalize_integer(counts.callbacks),
        uncertain_operation_count: normalize_integer(counts.unresolved),
        unsent_entry_count: normalize_integer(counts.unsent),
        idle_children_stopped: stopped,
      };
    });
    return this.observe_operation(actual, signal);
  }
  resume_after_cancelled_update(): void {
    this.assert_open();
    this.dependencies.state.resume_admission();
  }
  begin_planned_replay(): void {
    this.assert_open();
    if (!this.replaying)
      this.replay_gate = new Promise<void>((resolve) => {
        this.release_replay = resolve;
      });
    this.replaying = true;
    this.dependencies.state.seal_admission();
  }
  planned_replay_finished(signal: AbortSignal): Promise<void> {
    if (this.dependencies.permanently_stopped())
      return Promise.reject(new ServerLifecycleError('Mac runner is shutting down.'));
    return observe_conversation_operation(() => this.replay_gate, signal);
  }
  finish_planned_replay(): void {
    this.assert_open();
    this.replaying = false;
    this.release_replay?.();
  }
  prepare_update(mode: 'now' | 'idle', request_id: string, signal: AbortSignal): Promise<boolean> {
    assert_active(signal);
    this.assert_open();
    const children = this.dependencies.native_children.retained_children();
    if (
      mode === 'idle' &&
      (this.busy(this.counts()) || children.some((child) => child.background_task_ids.size !== 0))
    )
      return Promise.resolve(false);
    this.dependencies.state.seal_admission();
    const actual = this.own(() => this.prepare_children(children, request_id, signal));
    return this.observe_operation(actual, signal);
  }
  private async prepare_children(
    children: readonly MacRunningChild[],
    request_id: string,
    signal: AbortSignal,
  ): Promise<boolean> {
    assert_active(signal);
    for (const record of this.dependencies.commands().records())
      this.dependencies.commands().withdraw(record.command.command_id);
    // A provider can hold child.lock until this exact process stops.
    const stops = await Promise.allSettled(
      children.map((child) =>
        Promise.resolve().then(() => {
          if (signal.aborted) return;
          this.dependencies.state.begin_retirement(child.reference.child_id);
          this.dependencies.native_children.begin_retirement(child.reference);
          return this.dependencies.native_children.stop_native(child.reference);
        }),
      ),
    );
    const failed_stops = stops.filter((result) => result.status === 'rejected');
    if (failed_stops.length)
      throw new AggregateError(
        failed_stops.map((result) => result.reason),
        'Update native stops failed.',
      );
    assert_active(signal);
    const commands = this.dependencies.commands();
    const joined = await Promise.allSettled(
      commands.records().flatMap((record) => (record.task === null ? [] : [record.task])),
    );
    const failed_commands = joined.filter((result) => result.status === 'rejected');
    if (failed_commands.length)
      throw new AggregateError(
        failed_commands.map((result) => result.reason),
        'Update command joins failed.',
      );
    assert_active(signal);
    await commands.settled_replays();
    for (const child of children) {
      assert_active(signal);
      await this.dependencies.sources.finish(child.reference);
    }
    assert_active(signal);
    await commands.settle_known();
    assert_active(signal);
    if (
      this.dependencies.sources.failed() ||
      commands.records().some((record) => record.outcome !== null && !record.settled)
    )
      throw Object.assign(new Error('update cannot abandon required local output'), {
        name: 'RuntimeError',
      });
    let pending = this.update_commands.get(request_id);
    if (!pending) this.update_commands.set(request_id, (pending = []));
    for (const child of children) {
      assert_active(signal);
      let command = pending.find((value) => same_reference(value.child, child.reference));
      if (!command) {
        command = {
          command_id: 'update_stop_' + this.dependencies.new_update_id(),
          child: child.reference,
          operation: 'stop',
          turn: null,
          arguments: { kind: 'stop' },
        };
        pending.push(command);
      }
      await commands.adopt_unjournaled_outcome(command, outcome(command, 'succeeded', {}));
      this.dependencies.state.mark_retired(child.reference);
      this.dependencies.native_children.retire(child.reference);
      commands.prune(child.reference);
    }
    this.update_commands.delete(request_id);
    return true;
  }
  finish_operations(options: StopOptions): Promise<readonly unknown[]> {
    this.dependencies.retirement.begin_shutdown(options);
    return this.dependencies.retirement.observe_work(this.settled_operations());
  }
  settled_operations(): Promise<readonly unknown[]> {
    if (!this.finishing) this.finishing = this.join_operations();
    return this.finishing;
  }
  private async join_operations(): Promise<readonly unknown[]> {
    // Capture before releasing any continuation. Lifetime calls this synchronously
    // before it schedules native stop, which can make an owned operation reject.
    let pending = [...this.operations];
    this.replaying = false;
    this.release_replay?.();
    do {
      const results = await Promise.allSettled(pending);
      for (const result of results)
        if (result.status === 'rejected' && !(result.reason instanceof MacMaintenanceCancelled))
          remember_model_catalog_failure(this.failures, result.reason);
      pending = [...this.operations];
    } while (pending.length);
    return [...this.failures];
  }
}
