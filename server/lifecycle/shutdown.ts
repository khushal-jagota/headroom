import type { ShutdownFailure, ShutdownOwner, ShutdownState, StopOptions } from './contracts.ts';
import { ShutdownDeadlineExceeded } from './contracts.ts';
import { observe_shutdown } from './deadline.ts';
import { OWNERSHIP_RETENTION_INTERVAL_MS } from './configuration.ts';

interface Stage {
  name: string;
  actual: Promise<void>;
  pending: boolean;
  failure?: ShutdownFailure;
}
export interface ShutdownStages {
  /** Reserves ownership before calling an operation that can reenter shutdown. */
  own(name: string, operation: () => unknown | Promise<unknown>): Promise<void>;
}
export interface ShutdownComposition {
  grace_seconds: number;
  /** Every synchronous fence and independent stop begins here before any wait. */
  begin(stages: ShutdownStages, options: StopOptions): void;
  /** Ordered destructive phases. Each begins at most once, only after actual quiescence. */
  finalize: readonly { name: string; run(): unknown | Promise<unknown> }[];
}

export class ShutdownCoordinator implements ShutdownOwner, ShutdownStages {
  readonly #composition: ShutdownComposition;
  readonly #stages: Stage[] = [];
  #kind: ShutdownState['kind'] = 'running';
  #closing: Promise<void> | undefined;
  #finishing: Promise<void> | undefined;
  #retention: ReturnType<typeof setInterval> | undefined;
  #next_finalizer = 0;
  #logged = false;
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
    return Promise.all(this.#stages.map((stage) => stage.actual)).then(() => undefined);
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
  close(options?: StopOptions): Promise<void> {
    if (this.#closing) return this.#closing;
    const stop = options ?? {
      deadline: performance.now() / 1000 + this.#composition.grace_seconds,
    };
    let resolve!: () => void, reject!: (error: unknown) => void;
    // Publish ALL latches before a fence/provider callback can synchronously reenter.
    this.#closing = new Promise<void>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    void this.#closing.catch(() => {});
    this.#kind = 'stopping';
    this.#retention = setInterval(() => {}, OWNERSHIP_RETENTION_INTERVAL_MS);
    this.#run(stop).then(resolve, (error) => {
      this.#retain(error);
      reject(error);
    });
    return this.#closing;
  }
  async #run(stop: StopOptions): Promise<void> {
    try {
      this.#composition.begin(this, stop);
    } catch (error) {
      this.own('stop composition', () => {
        throw error;
      });
    }
    await this.#observe(stop);
    await this.#finalize(stop);
  }
  #failure(): Error | null {
    const state = this.shutdown_state;
    if (state.failures.length)
      return new AggregateError(
        state.failures.map((failure) => failure.error),
        `Shutdown failed; ownership retained: ${state.failures.map((failure) => failure.name).join(', ')}`,
      );
    if (state.pending.length) return new ShutdownDeadlineExceeded(state.pending);
    return null;
  }
  async #observe(stop: StopOptions): Promise<void> {
    try {
      await observe_shutdown(
        Promise.allSettled(this.#stages.map((stage) => stage.actual)),
        stop,
        'shutdown',
      );
    } catch (error) {
      throw this.#failure() ?? error;
    }
    const failure = this.#failure();
    if (failure) throw failure;
  }
  async #finalize(stop: StopOptions): Promise<void> {
    while (this.#next_finalizer < this.#composition.finalize.length) {
      if (performance.now() / 1000 >= stop.deadline)
        throw new ShutdownDeadlineExceeded([this.#composition.finalize[this.#next_finalizer].name]);
      const finalizer = this.#composition.finalize[this.#next_finalizer++];
      this.own(finalizer.name, () => finalizer.run());
      await this.#observe(stop);
    }
    this.#kind = 'closed';
    clearInterval(this.#retention);
    this.#retention = undefined;
  }
  #retain(error: unknown): void {
    this.#kind = 'retained';
    if (!this.#logged) {
      this.#logged = true;
      console.error(error);
    }
  }
  finish_shutdown(options: StopOptions): Promise<void> {
    if (this.#finishing) return this.#finishing;
    if (this.#kind === 'closed') return Promise.resolve();
    const failure = this.#failure();
    if (this.#kind !== 'retained' || failure)
      return Promise.reject(
        failure ?? new Error('Shutdown is not ready for explicit finalization'),
      );
    // Reserve the shared finish flight before any destructor can reenter.
    let resolve!: () => void, reject!: (error: unknown) => void;
    this.#finishing = new Promise<void>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    void this.#finishing.catch(() => {});
    this.#finalize(options).then(resolve, (error) => {
      this.#retain(error);
      reject(error);
    });
    return this.#finishing;
  }
}
