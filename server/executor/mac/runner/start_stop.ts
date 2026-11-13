/** Native starts and stops retain custody independently of command/result retries. */
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { BackendSpawnFailed } from '../../../conversation/backends/contracts.ts';
import { FifoLock } from '../../../conversation/runtime/state.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { ChildReference, StartRun } from '../../wire.generated.ts';
import { localize_start } from './localization.ts';
import type {
  MacNativeChildrenContract,
  MacNativeChildrenOptions,
  MacRunningChild,
} from './contracts.ts';
import { MacChildAdmissionRefused } from './contracts.ts';
import { same_reference } from './logic/outcomes.ts';
import { remember_model_catalog_failure } from '../../../conversation/backends/catalog_cleanup.ts';
interface Entry {
  reference: ChildReference;
  ticket_id: string;
  retiring: boolean;
  stopped: boolean;
  child: MacRunningChild | null;
  start: Promise<MacRunningChild> | undefined;
  stop: Promise<void> | undefined;
  native_stop: Promise<void> | undefined;
}
export class MacNativeChildren implements MacNativeChildrenContract {
  private readonly options: MacNativeChildrenOptions;
  private readonly entries = new Map<string, Entry>();
  private closed = false;
  private cleanup: Promise<void> | undefined;
  private readonly logged_failures = new Set<unknown>();
  private readonly callback_failures = new Set<unknown>();
  private faulted = false;
  private actual = Promise.withResolvers<void>();
  private actual_failed = false;
  constructor(options: MacNativeChildrenOptions) {
    this.options = options;
    void this.actual.promise.catch(() => {});
  }
  get admission_stopped(): boolean {
    return this.closed;
  }
  get(child_id: string): MacRunningChild | null {
    return this.entries.get(child_id)?.child ?? null;
  }
  retained_children(): readonly MacRunningChild[] {
    return [...this.entries.values()].flatMap((entry) =>
      entry.child === null ? [] : [entry.child],
    );
  }
  is_retiring(child_id: string): boolean {
    return this.entries.get(child_id)?.retiring ?? false;
  }
  authenticate(child_id: string | null, capability: string | null): MacRunningChild {
    const entry = this.entries.get(child_id ?? ''),
      child = entry?.child;
    const expected = Buffer.from(child?.capability ?? ''),
      supplied = Buffer.from(capability ?? '');
    if (
      !child ||
      entry!.retiring ||
      capability === null ||
      supplied.length !== expected.length ||
      !timingSafeEqual(supplied, expected)
    )
      throw Object.assign(new Error('unknown executor child capability'), {
        name: 'PermissionError',
      });
    return child;
  }
  start(
    reference: ChildReference,
    input: StartRun,
    invoking: () => void,
  ): Promise<MacRunningChild> {
    if (this.closed) return Promise.reject(new MacChildAdmissionRefused('executor is quiescing'));
    if (this.entries.has(reference.child_id))
      return Promise.reject(new BackendSpawnFailed('child identity already has a native start'));
    if (input.resolved_start.conversation_id !== reference.conversation_id)
      return Promise.reject(new Error('child conversation mismatch'));
    const ticket_id = new Map(
      input.resolved_start.role_materials?.identity_environment_variables ?? [],
    ).get('PLAN_TICKET_ID');
    if (!ticket_id)
      return Promise.reject(new BackendSpawnFailed('Mac runs require a Ticket identity'));
    if ([...this.entries.values()].some((entry) => entry.ticket_id === ticket_id))
      return Promise.reject(new BackendSpawnFailed('Ticket already has a retained source child'));
    const entry: Entry = {
      reference,
      ticket_id,
      retiring: false,
      stopped: false,
      child: null,
      start: undefined,
      stop: undefined,
      native_stop: undefined,
    };
    this.entries.set(reference.child_id, entry);
    const task = Promise.resolve().then(() => this.start_native(entry, input, invoking));
    entry.start = task;
    void task.catch(() => {});
    return task;
  }
  private async start_native(
    entry: Entry,
    input: StartRun,
    invoking: () => void,
  ): Promise<MacRunningChild> {
    const capability = randomBytes(32).toString('base64url');
    try {
      this.assert_start(entry);
      let generation: string;
      try {
        generation = await this.options.skills.install_native_skill_snapshot(
          input.canonical_skill_snapshot,
          {
            root: this.options.paths.root,
            native_home: this.options.native_skill_home(input.resolved_start.backend_key),
          },
        );
      } catch (error) {
        throw new BackendSpawnFailed(
          `Canonical skill bootstrap failed: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
      this.assert_start(entry);
      const localized_start = await localize_start(input, entry.reference, capability, generation, {
        ...this.options,
        on_failure: (error) => this.record_failure(error),
      });
      this.assert_start(entry);
      const native = this.options.factory(localized_start)({
        resolved_start: localized_start,
        event_sink: this.options.event_sink(entry.reference),
        message_files: this.options.message_files,
      });
      const child: MacRunningChild = {
        reference: entry.reference,
        capability,
        native,
        ticket_id: entry.ticket_id,
        launch_input: input,
        localized_start,
        lock: new FifoLock(),
        active_turn: null,
        open_tool_calls: new Set(),
        background_task_ids: new Set(),
        model: localized_start.model,
        reasoning_effort: localized_start.reasoning_effort,
      };
      entry.child = child;
      await child.lock.run(async () => {
        this.assert_start(entry);
        invoking();
        await native.start(localized_start, {
          vendor_session_cursor: input.vendor_session_cursor ?? null,
        });
      });
      return child;
    } catch (error) {
      if (entry.child !== null) {
        entry.retiring = true;
        try {
          await this.stop_locked(entry);
        } catch (cleanup) {
          this.record_failure(cleanup);
          throw new AggregateError([error, cleanup], 'Native start and cleanup failed.');
        }
      }
      if (this.entries.get(entry.reference.child_id) === entry)
        this.entries.delete(entry.reference.child_id);
      throw error;
    } finally {
      if (entry.child !== null) {
        try {
          await this.options.record_children();
        } catch (error) {
          console.warn(
            'agent processes were not recorded error=',
            error instanceof Error ? error.constructor.name : typeof error,
          );
        }
      }
    }
  }
  private assert_start(entry: Entry): void {
    if (this.closed) throw new MacChildAdmissionRefused('executor is quiescing');
    if (entry.retiring) throw new MacChildAdmissionRefused('source child identity is retiring');
  }
  begin_retirement(reference: ChildReference): void {
    const entry = this.entries.get(reference.child_id);
    if (entry && same_reference(entry.reference, reference)) entry.retiring = true;
  }
  stop_child(reference: ChildReference, invoking: () => void): Promise<void> {
    const entry = this.entries.get(reference.child_id);
    if (!entry || !same_reference(entry.reference, reference)) return Promise.resolve();
    entry.retiring = true;
    if (entry.stop) {
      const stopped = entry.stop;
      return Promise.resolve().then(() => {
        invoking();
        return stopped;
      });
    }
    const task = Promise.resolve().then(async () => {
      await entry.start?.catch(() => {});
      await this.stop_locked(entry, invoking);
    });
    entry.stop = task;
    void task.catch((error) => {
      if (!(error instanceof MacChildAdmissionRefused)) this.record_failure(error);
      if (entry.stop === task) entry.stop = undefined;
    });
    return entry.stop;
  }
  private async stop_locked(entry: Entry, invoking?: () => void): Promise<void> {
    if (entry.stopped) {
      invoking?.();
      return;
    }
    if (entry.child) await entry.child.lock.run(() => this.stop_process(entry, invoking));
    else entry.stopped = true;
  }
  stop_native(reference: ChildReference): Promise<void> {
    const entry = this.entries.get(reference.child_id);
    if (!entry || !same_reference(entry.reference, reference)) return Promise.resolve();
    return this.stop_process(entry).catch((error: unknown) => {
      if (error instanceof ServerLifecycleError) throw error;
      throw Object.assign(new ServerLifecycleError('Mac native cleanup failed.'), { cause: error });
    });
  }
  /** Host shutdown closes the process before a provider call can release its lock. */
  private stop_process(entry: Entry, invoking?: () => void): Promise<void> {
    if (entry.stopped || entry.child === null) {
      invoking?.();
      return Promise.resolve();
    }
    if (entry.native_stop) {
      invoking?.();
      return entry.native_stop;
    }
    const native = entry.child.native;
    const task = Promise.resolve()
      .then(() => {
        invoking?.();
        return native.stop();
      })
      .then(() => {
        entry.stopped = true;
      });
    entry.native_stop = task;
    void task.catch(() => {
      if (entry.native_stop === task) entry.native_stop = undefined;
    });
    return entry.native_stop;
  }
  retire(reference: ChildReference): void {
    const entry = this.entries.get(reference.child_id);
    if (!entry || !same_reference(entry.reference, reference)) return;
    if (!entry.stopped) throw new ServerLifecycleError('Native child has not stopped.');
    this.entries.delete(reference.child_id);
  }
  private record_failure(error: unknown, notify = true): void {
    const errors = new Set<unknown>();
    remember_model_catalog_failure(errors, error);
    for (const cause of errors)
      if (!this.logged_failures.has(cause)) {
        this.logged_failures.add(cause);
        console.warn('Mac native cleanup failed:', cause);
      }
    if (notify && !this.faulted) {
      this.faulted = true;
      this.closed = true;
      try {
        this.options.on_failure(error instanceof Error ? error : new Error(String(error)));
      } catch (callback) {
        remember_model_catalog_failure(this.callback_failures, callback);
        this.record_failure(callback, false);
      }
    }
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.cleanup && this.actual_failed) {
      this.actual = Promise.withResolvers<void>();
      this.actual_failed = false;
      void this.actual.promise.catch(() => {});
    }
    const actual = this.actual;
    this.closed = true;
    const entries = [...this.entries.values()];
    for (const entry of entries) entry.retiring = true;
    // Every call retries retained native/publisher close capabilities, even while
    // the original actual chain still joins a provider start that needs that stop.
    const observe = (operation: () => Promise<void>, bounded: boolean) =>
      Promise.resolve()
        .then(operation)
        .catch((error) => {
          if (!bounded || !(error instanceof ShutdownDeadlineExceeded)) this.record_failure(error);
          throw error;
        });
    const observations = [
      observe(() => this.options.skills.stop(options), true),
      ...entries.map((entry) => observe(() => this.stop_process(entry), false)),
    ];
    if (!this.cleanup) {
      const task = Promise.resolve().then(async () => {
        // Reentrant factory construction can publish a child after stop was called,
        // but before this chain starts. Fence and stop that actual handle too.
        const first_stops = await Promise.allSettled(
          entries.map((entry) => this.stop_process(entry)),
        );
        for (const result of first_stops)
          if (result.status === 'rejected') this.record_failure(result.reason, false);
        const skills_actual = this.options.skills.settled();
        void skills_actual.catch(() => {});
        await Promise.allSettled(entries.map((entry) => entry.start));
        const children = await Promise.allSettled(entries.map((entry) => this.stop_process(entry)));
        const skills = await Promise.allSettled([skills_actual]);
        for (const result of skills)
          if (result.status === 'rejected') this.record_failure(result.reason, false);
        for (const result of children)
          if (result.status === 'rejected') this.record_failure(result.reason, false);
        const unfinished = [...children, ...skills].filter(
          (result) => result.status === 'rejected',
        );
        if (unfinished.length || this.callback_failures.size)
          throw new AggregateError(
            [...unfinished.map((result) => result.reason), ...this.callback_failures],
            'Native children did not stop.',
          );
        actual.resolve();
      });
      this.cleanup = task;
      void task.catch((error: unknown) => {
        if (this.cleanup === task) {
          this.cleanup = undefined;
          this.actual_failed = true;
          actual.reject(error);
        }
        this.record_failure(error);
      });
    }
    return observe_shutdown(
      Promise.all([this.cleanup, ...observations]).catch((error: unknown) => {
        if (error instanceof ServerLifecycleError) throw error;
        throw Object.assign(new ServerLifecycleError('Mac native cleanup failed.'), {
          cause: error,
        });
      }),
      options,
      'Mac native children remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual.promise;
  }
}
