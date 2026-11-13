/** The local runner composes existing owners before any native admission can begin. */
import type {
  BackendEventSink,
  BackendEventValue,
} from '../../../conversation/backends/contracts.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type {
  ChildCommand,
  ChildReference,
  CommandOutcome,
  TurnToken,
} from '../../wire.generated.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import { MailboxRunCancelled } from '../mailbox/contracts.ts';
import type { MacNativeChildrenContract } from './contracts.ts';
import type { MacCommandServiceContract } from './command_contracts.ts';
import type { MacRunnerContract, MacRunnerOptions } from './lifecycle_contracts.ts';
import { MacChildState } from './state.ts';
import { MacSourceWork } from './source_work.ts';
import { MacNativeChildren } from './start_stop.ts';
import { MacRunnerEvents } from './events.ts';
import { MacRetirement } from './retirement.ts';
import { MacCommandService } from './service.ts';
import { MacMaintenance } from './maintenance.ts';
import { MacRunnerLifetime } from './lifetime.ts';
import { create_mac_output_publication } from './publication.ts';

export class MacRunner implements MacRunnerContract {
  readonly native_children: MacNativeChildrenContract;
  private readonly commands: MacCommandServiceContract;
  private readonly state: MacChildState;
  private readonly sources: MacSourceWork;
  private readonly events: MacRunnerEvents;
  private readonly maintenance: MacMaintenance;
  private readonly lifetime: MacRunnerLifetime;
  constructor(options: MacRunnerOptions) {
    const state = new MacChildState();
    let lifetime!: MacRunnerLifetime;
    let events!: MacRunnerEvents;
    let reported = false;
    const failure = (error: Error) => {
      // A provider cleanup fault closes the complete runner before the host callback can reenter it.
      lifetime.seal();
      if (!reported) {
        reported = true;
        options.native.on_failure(error);
      }
    };
    const sources = new MacSourceWork(state, () => lifetime.permanently_stopped);
    const native = new MacNativeChildren({
      ...options.native,
      event_sink: (reference) => events.event_sink(reference),
      on_failure: failure,
    });
    const publication = create_mac_output_publication(options.outbox);
    events = new MacRunnerEvents({
      native_children: native,
      state,
      sources,
      publication,
      live:
        options.live === null
          ? null
          : {
              send: (event) => {
                if (!lifetime.permanently_stopped) options.live!.send(event);
              },
            },
    });
    const retirement = new MacRetirement({ native_children: native, state, sources });
    const commands = new MacCommandService({
      incarnation: options.incarnation,
      native_children: native,
      journal: options.journal,
      publication,
      state,
      asks: state,
      inputs: options.inputs,
      now_ms: options.now_ms,
      stop_exact: (command, work, invoking) => retirement.stop_exact(command, work, invoking),
    });
    const maintenance = new MacMaintenance({
      native_children: native,
      state,
      sources,
      commands: () => commands,
      retirement,
      outbox: options.outbox,
      new_update_id: options.new_update_id,
      permanently_stopped: () => lifetime.permanently_stopped,
    });
    lifetime = new MacRunnerLifetime({
      native_children: native,
      state,
      sources,
      commands: () => commands,
      maintenance,
      retirement,
      on_failure: failure,
    });
    this.state = state;
    this.sources = sources;
    this.native_children = native;
    this.commands = commands;
    this.events = events;
    this.maintenance = maintenance;
    this.lifetime = lifetime;
  }
  get admission_stopped(): boolean {
    return this.state.admission_stopped;
  }
  get permanently_stopped(): boolean {
    return this.lifetime.permanently_stopped;
  }
  get planned_update_replay(): boolean {
    return this.maintenance.planned_update_replay;
  }
  private assert_admission(): void {
    if (this.lifetime.permanently_stopped)
      throw new ServerLifecycleError('Mac runner is shutting down.');
  }
  async run(command: ChildCommand, signal: AbortSignal): Promise<CommandOutcome> {
    signal.throwIfAborted();
    this.assert_admission();
    let stopping: Promise<void> | undefined;
    const abort = () => {
      this.commands.withdraw(command.command_id);
      stopping = this.finish_cancelled_run(signal);
      void stopping.catch(() => {});
    };
    signal.addEventListener('abort', abort, { once: true });
    try {
      let operation: Promise<CommandOutcome>;
      try {
        operation = this.commands.run(command);
      } catch (error) {
        operation = Promise.reject(error);
      }
      const [result] = await Promise.allSettled([operation]);
      if (stopping) {
        const [cleanup] = await Promise.allSettled([stopping]);
        const failures: unknown[] = [];
        if (result.status === 'rejected') failures.push(result.reason);
        if (cleanup.status === 'rejected') failures.push(cleanup.reason);
        if (failures.length === 1) throw failures[0];
        if (failures.length) throw new AggregateError(failures, 'Canceled Mac command failed.');
        throw signal.reason;
      }
      if (result.status === 'rejected') throw result.reason;
      return result.value;
    } finally {
      signal.removeEventListener('abort', abort);
    }
  }
  private async finish_cancelled_run(signal: AbortSignal): Promise<void> {
    const reason = signal.reason;
    const [observation, actual] = await Promise.allSettled([
      this.lifetime.stop(
        reason instanceof MailboxRunCancelled ? reason.stop_options : { deadline: 0 },
      ),
      this.lifetime.settled(),
    ]);
    const failures: unknown[] = actual.status === 'rejected' ? [actual.reason] : [];
    if (
      observation.status === 'rejected' &&
      !(observation.reason instanceof ShutdownDeadlineExceeded) &&
      !(
        actual.status === 'rejected' &&
        (observation.reason === actual.reason ||
          (observation.reason instanceof Error && observation.reason.cause === actual.reason))
      )
    )
      failures.push(observation.reason);
    if (failures.length === 1) throw failures[0];
    if (failures.length) throw new AggregateError(failures, 'Canceled Mac command cleanup failed.');
  }
  async publish_owed_outputs(command: CanonicalReader, result: CanonicalReader): Promise<void> {
    this.assert_admission();
    return this.commands.publish_owed_outputs(command, result);
  }
  authenticate(child_id: string | null, capability: string | null) {
    this.assert_admission();
    return this.native_children.authenticate(child_id, capability);
  }
  withdraw(command_id: string): boolean {
    return this.commands.withdraw(command_id);
  }
  source_work<T>(
    reference: ChildReference,
    work: () => Promise<T>,
    options?: { final_event?: boolean },
  ): Promise<T> {
    return this.sources.source_work(reference, work, options);
  }
  event_sink(reference: ChildReference): BackendEventSink {
    return this.events.event_sink(reference);
  }
  report_event(
    reference: ChildReference,
    turn: TurnToken | null,
    event: BackendEventValue,
  ): Promise<void> {
    return this.events.report_event(reference, turn, event);
  }
  working_turns() {
    return this.maintenance.working_turns();
  }
  quiesce(signal: AbortSignal) {
    return this.maintenance.quiesce(signal);
  }
  resume_after_cancelled_update(): void {
    this.maintenance.resume_after_cancelled_update();
  }
  begin_planned_replay(): void {
    this.maintenance.begin_planned_replay();
  }
  planned_replay_finished(signal: AbortSignal): Promise<void> {
    return this.maintenance.planned_replay_finished(signal);
  }
  finish_planned_replay(): void {
    this.maintenance.finish_planned_replay();
  }
  prepare_update(mode: 'now' | 'idle', request_id: string, signal: AbortSignal): Promise<boolean> {
    return this.maintenance.prepare_update(mode, request_id, signal);
  }
  seal(): void {
    this.lifetime.seal();
  }
  stop(options: StopOptions): Promise<void> {
    return this.lifetime.stop(options);
  }
  settled(): Promise<void> {
    return this.lifetime.settled();
  }
}
