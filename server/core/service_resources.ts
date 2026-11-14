import type { StopOptions } from '../lifecycle/contracts.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { ShutdownStages } from '../lifecycle/shutdown.ts';

/** Reservations exist before factories run, including factories that reenter shutdown. */
export class ServiceResources {
  readonly #late: Promise<void>[] = [];

  service<T>(
    name: string,
    stop: (value: T, options: StopOptions) => unknown,
    settled?: (value: T) => unknown,
  ): ServiceResource<T> {
    return new ServiceResource(name, stop, settled, (actual) => this.#late.push(actual));
  }

  async settle_late(): Promise<void> {
    const results = await Promise.allSettled(this.#late);
    const failures = results.flatMap((result) =>
      result.status === 'rejected' ? [result.reason] : [],
    );
    if (failures.length) throw new AggregateError(failures, 'Late startup cleanup failed');
  }
}

export class ServiceResource<T> {
  #value: T | undefined;
  #options: StopOptions | undefined;
  #actual: Promise<void> | undefined;
  readonly name: string;
  readonly stop: (value: T, options: StopOptions) => unknown;
  readonly settled: ((value: T) => unknown) | undefined;
  readonly retain_late: (actual: Promise<void>) => void;
  constructor(
    name: string,
    stop: (value: T, options: StopOptions) => unknown,
    settled: ((value: T) => unknown) | undefined,
    retain_late: (actual: Promise<void>) => void,
  ) {
    this.name = name;
    this.stop = stop;
    this.settled = settled;
    this.retain_late = retain_late;
  }

  get value(): T | undefined {
    return this.#value;
  }

  own(value: T | undefined): T | undefined {
    // createApp publishes its identity before registration, then returns that identity.
    if (value === undefined || this.#value === value) return value;
    if (this.#value !== undefined) throw new Error(`${this.name} already owns a service`);
    this.#value = value;
    if (this.#options) this.retain_late(this.#dispose(value, this.#options));
    return value;
  }

  begin(stages: ShutdownStages, options: StopOptions): void {
    this.#options = options;
    stages.own(this.name, () =>
      this.#value === undefined ? undefined : this.#dispose(this.#value, options),
    );
  }

  #dispose(value: T, options: StopOptions): Promise<void> {
    if (this.#actual) return this.#actual;
    const { promise, resolve, reject } = Promise.withResolvers<void>();
    // Reserve disposal before either callback can reenter startup or stop.
    this.#actual = promise;
    void promise.catch(() => {});
    const flights = [() => this.stop(value, options), () => this.settled?.(value)].map(
      (operation) => {
        try {
          return Promise.resolve(operation());
        } catch (error) {
          return Promise.reject(error);
        }
      },
    );
    void Promise.allSettled(flights).then((results) => {
      const failures = results.flatMap((result, index) =>
        result.status === 'rejected' &&
        !(
          index === 0 &&
          this.settled !== undefined &&
          result.reason instanceof ShutdownDeadlineExceeded
        )
          ? [result.reason]
          : [],
      );
      if (failures.length) reject(new AggregateError(failures, `${this.name} shutdown failed`));
      else resolve();
    });
    return promise;
  }
}
