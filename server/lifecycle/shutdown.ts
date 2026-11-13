import type { ShutdownFailure, ShutdownOwner, ShutdownState, StopOptions } from './contracts.ts';
import { CONTROL_TIMEOUT_SECONDS } from '../conversation/worker_unit/configuration.ts';

interface Stage {
  name: string;
  actual: Promise<void>;
  pending: boolean;
  failure?: ShutdownFailure;
}
export interface ShutdownStages {
  /** Reserve ownership before an operation can reenter shutdown. */
  own(name: string, operation: () => unknown | Promise<unknown>): Promise<void>;
}
export interface ShutdownComposition {
  grace_seconds: number;
  hard_seconds: number;
  /** All fences and independent stops begin before any wait. */
  begin(stages: ShutdownStages, options: StopOptions): void;
  grace_expired(stages: ShutdownStages): void;
  /** Terminal synchronous log/force/exit. No destructive finalizers run here. */
  hard_stop(state: ShutdownState, force_deadline: number): never;
  finalize: readonly { name: string; run(): unknown | Promise<unknown> }[];
}

/** Grace limits ordinary work; actual settlement, rather than timeout, permits disposal. */
export class ShutdownCoordinator implements ShutdownOwner, ShutdownStages {
  readonly #composition: ShutdownComposition;
  readonly #stages: Stage[] = [];
  #kind: ShutdownState['kind'] = 'running';
  #closing: Promise<void> | undefined;
  #grace_timer: ReturnType<typeof setTimeout> | undefined;
  #hard_timer: ReturnType<typeof setTimeout> | undefined;
  #soft_deadline = Infinity;
  #hard_deadline = Infinity;
  #grace_started = false;
  #hard_started = false;
  constructor(composition: ShutdownComposition) {
    this.#composition = composition;
  }
  get shutdown_state(): ShutdownState {
    return {
      kind: this.#kind,
      pending: this.#stages.filter((stage) => stage.pending).map((stage) => stage.name),
      failures: this.#stages.flatMap((stage) => (stage.failure ? [stage.failure] : [])),
    };
  }
  get shutdown_settled(): Promise<void> {
    return Promise.allSettled(this.#stages.map((stage) => stage.actual)).then((results) => {
      const failures = results.flatMap((result) =>
        result.status === 'rejected' ? [result.reason] : [],
      );
      if (failures.length)
        throw new AggregateError(failures, 'Shutdown stages failed after actual settlement');
    });
  }
  own(name: string, operation: () => unknown | Promise<unknown>): Promise<void> {
    let resolve!: () => void, reject!: (error: unknown) => void;
    const actual = new Promise<void>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    const stage: Stage = { name, actual, pending: true };
    this.#stages.push(stage);
    void actual.catch(() => {});
    const complete = (error?: { value: unknown }) => {
      stage.pending = false;
      if (error) {
        stage.failure = { name, error: error.value };
        reject(error.value);
      } else resolve();
    };
    try {
      Promise.resolve(operation()).then(
        () => complete(),
        (error) => complete({ value: error }),
      );
    } catch (error) {
      complete({ value: error });
    }
    return actual;
  }
  close(options: StopOptions): Promise<void> {
    if (this.#closing) return this.#closing;
    const started = performance.now() / 1000;
    this.#soft_deadline = Math.min(options.deadline, started + this.#composition.grace_seconds);
    this.#hard_deadline = started + this.#composition.hard_seconds;
    let resolve!: () => void, reject!: (error: unknown) => void;
    // Publish the stop flight and referenced deadline timers before user callbacks.
    this.#closing = new Promise<void>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    void this.#closing.catch(() => {});
    this.#kind = 'stopping';
    this.#grace_timer = setTimeout(
      () => this.#grace(),
      Math.max(0, (this.#soft_deadline - started) * 1000),
    );
    this.#hard_timer = setTimeout(
      () => this.#hard(),
      Math.max(0, (this.#hard_deadline - started) * 1000),
    );
    this.#run({ deadline: this.#soft_deadline }).then(resolve, reject);
    return this.#closing;
  }
  #grace(): void {
    if (this.#grace_started || this.#kind === 'closed') return;
    this.#grace_started = true;
    try {
      this.#composition.grace_expired(this);
    } catch (error) {
      this.own('grace actions', () => {
        throw error;
      });
    }
  }
  #hard(): never {
    this.#hard_started = true;
    // The control attempt is capped at the ORIGINAL hard trigger plus ten seconds.
    return this.#composition.hard_stop(
      this.shutdown_state,
      this.#hard_deadline + CONTROL_TIMEOUT_SECONDS,
    );
  }
  #check_time(): void {
    if (this.#hard_started || performance.now() / 1000 >= this.#hard_deadline) this.#hard();
    if (performance.now() / 1000 >= this.#soft_deadline) this.#grace();
  }
  async #drain(): Promise<void> {
    let count: number;
    do {
      this.#check_time();
      count = this.#stages.length;
      await Promise.allSettled(this.#stages.map((stage) => stage.actual));
      // A late completion microtask cannot outrun an overdue hard timer.
      this.#check_time();
    } while (count !== this.#stages.length);
  }
  async #run(stop: StopOptions): Promise<void> {
    try {
      this.#composition.begin(this, stop);
    } catch (error) {
      this.own('stop composition', () => {
        throw error;
      });
    }
    await this.#drain();
    for (const finalizer of this.#composition.finalize) {
      this.#check_time();
      this.own(finalizer.name, () => finalizer.run());
      // A settled failure is recorded; later safe finalizers still run.
      await this.#drain();
    }
    this.#check_time();
    this.#kind = 'closed';
    clearTimeout(this.#grace_timer);
    clearTimeout(this.#hard_timer);
    const failures = this.shutdown_state.failures;
    if (failures.length) {
      const error = new AggregateError(
        failures.map((failure) => failure.error),
        `Shutdown failed after settlement: ${failures.map((failure) => failure.name).join(', ')}`,
      );
      console.error(error);
      throw error;
    }
  }
}
