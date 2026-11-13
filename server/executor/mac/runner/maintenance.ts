/** Maintenance observes actual custody. A deadline never substitutes for that join. */
import { observe_conversation_operation } from '../../../runtime/abort.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { normalize_integer } from '../../integers.ts';
import type { ChildCommand, Quiescence, WorkingTurn } from '../../wire.generated.ts';
import type { MacRunningChild } from './contracts.ts';
import type {
  MacMaintenanceContract,
  MacMaintenanceDependencies,
} from './maintenance_contracts.ts';
import { outcome, same_reference } from './logic/outcomes.ts';

function cleanup_failure(error: unknown): ServerLifecycleError {
  return error instanceof ServerLifecycleError
    ? error
    : Object.assign(new ServerLifecycleError('Mac runner cleanup failed.'), { cause: error });
}

export class MacMaintenance implements MacMaintenanceContract {
  private readonly dependencies: MacMaintenanceDependencies;
  private readonly operations = new Set<Promise<unknown>>();
  private readonly failures = new Set<unknown>();
  private permanent = false;
  private completed = false;
  private replaying = false;
  private replay_gate: Promise<void> = Promise.resolve();
  private release_replay: (() => void) | undefined;
  private cleanup: Promise<void> | undefined;
  private resolve_actual!: () => void;
  private reject_actual!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.resolve_actual = resolve;
    this.reject_actual = reject;
  });
  private fault_reported = false;
  constructor(dependencies: MacMaintenanceDependencies) {
    this.dependencies = dependencies;
    void this.actual.catch(() => {});
  }
  get permanently_stopped(): boolean {
    return this.permanent;
  }
  get planned_update_replay(): boolean {
    return this.replaying;
  }
  seal(): void {
    this.permanent = true;
    this.dependencies.state.seal_admission();
  }
  private assert_open(): void {
    if (this.permanent) throw new ServerLifecycleError('Mac runner is shutting down.');
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
      records.filter((record) => record.has_outputs && !record.outputs_published).length +
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
  quiesce(): Promise<Quiescence> {
    this.assert_open();
    this.dependencies.state.seal_admission();
    return this.own(async () => {
      let counts = this.counts(),
        stopped = false;
      if (!this.busy(counts)) {
        for (const child of this.dependencies.native_children.retained_children()) {
          const closed = await child.lock.run(async () => {
            if (this.busy(this.counts())) return false;
            await this.dependencies.native_children.stop_native(child.reference);
            this.dependencies.native_children.retire(child.reference);
            this.dependencies.state.clear(child.reference);
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
    if (this.permanent)
      return Promise.reject(new ServerLifecycleError('Mac runner is shutting down.'));
    return observe_conversation_operation(() => this.replay_gate, signal);
  }
  finish_planned_replay(): void {
    this.assert_open();
    this.replaying = false;
    this.release_replay?.();
  }
  prepare_update(mode: 'now' | 'idle'): Promise<boolean> {
    this.assert_open();
    const children = this.dependencies.native_children.retained_children();
    if (
      mode === 'idle' &&
      (this.busy(this.counts()) || children.some((child) => child.background_task_ids.size !== 0))
    )
      return Promise.resolve(false);
    this.dependencies.state.seal_admission();
    for (const child of children) {
      this.dependencies.state.begin_retirement(child.reference.child_id);
      this.dependencies.native_children.begin_retirement(child.reference);
    }
    for (const record of this.dependencies.commands().records())
      this.dependencies.commands().withdraw(record.command.command_id);
    return this.own(() => this.prepare_children(children));
  }
  private async prepare_children(children: readonly MacRunningChild[]): Promise<boolean> {
    // A provider can hold child.lock until this exact process stops.
    await Promise.all(
      children.map((child) => this.dependencies.native_children.stop_native(child.reference)),
    );
    const commands = this.dependencies.commands();
    await Promise.all(
      commands.records().flatMap((record) => (record.task === null ? [] : [record.task])),
    );
    await commands.settled_replays();
    for (const child of children) await this.dependencies.sources.finish(child.reference);
    await commands.settle_known();
    if (
      this.dependencies.sources.failed() ||
      commands.records().some((record) => record.has_outputs && !record.outputs_published)
    )
      throw Object.assign(new Error('update cannot abandon required local output'), {
        name: 'RuntimeError',
      });
    for (const child of children) {
      const command: ChildCommand = {
        command_id: 'update_stop_' + this.dependencies.new_update_id(),
        child: child.reference,
        operation: 'stop',
        turn: null,
        arguments: { kind: 'stop' },
      };
      await commands.adopt_unjournaled_outcome(command, outcome(command, 'succeeded', {}));
      this.dependencies.state.mark_retired(child.reference);
      this.dependencies.native_children.retire(child.reference);
    }
    return true;
  }
  private record_failure(error: unknown): void {
    this.failures.add(error);
    if (!this.fault_reported) {
      this.fault_reported = true;
      try {
        this.dependencies.on_failure(error instanceof Error ? error : new Error(String(error)));
      } catch (callback) {
        this.failures.add(callback);
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
    this.replaying = false;
    this.release_replay?.();
    for (const child of this.dependencies.native_children.retained_children()) {
      this.dependencies.state.begin_retirement(child.reference.child_id);
      this.dependencies.native_children.begin_retirement(child.reference);
    }
    for (const record of this.dependencies.commands().records())
      this.dependencies.commands().withdraw(record.command.command_id);
    // Repeated calls retry native/skills close capabilities while the raw join remains pending.
    const native = Promise.resolve().then(() => this.dependencies.native_children.stop(options));
    void native.catch((error) => {
      if (!(error instanceof ShutdownDeadlineExceeded)) this.record_failure(error);
    });
    if (!this.cleanup) {
      const task = Promise.resolve().then(() => this.drain());
      this.cleanup = task;
      void task.catch((error) => {
        this.record_failure(error);
        if (this.cleanup === task) this.cleanup = undefined;
      });
    }
    return observe_shutdown(
      Promise.all([native, this.cleanup]),
      options,
      'Mac runner work remains unfinished at the shutdown deadline.',
    ).catch((error: unknown) => {
      throw cleanup_failure(error);
    });
  }
  private async drain(): Promise<void> {
    while (this.operations.size) {
      const results = await Promise.allSettled([...this.operations]);
      for (const result of results)
        if (result.status === 'rejected') this.record_failure(result.reason);
    }
    const commands = this.dependencies.commands();
    while (commands.records().some((record) => record.task !== null)) {
      const results = await Promise.allSettled(
        commands.records().flatMap((record) => (record.task === null ? [] : [record.task])),
      );
      for (const result of results)
        if (result.status === 'rejected') this.record_failure(result.reason);
    }
    await commands.settled_replays();
    // A failed settlement retains a known outcome and an exact retry point. Do not
    // certify the raw join until a later stop attempt completes that settlement.
    await commands.settle_known();
    const native = await Promise.allSettled([this.dependencies.native_children.settled()]);
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
