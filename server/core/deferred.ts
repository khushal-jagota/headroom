/** One promise settled from outside. The first resolve or reject wins; later calls do nothing. */
export class Deferred<T> {
  readonly promise: Promise<T>;
  settled = false;
  value: T | undefined;
  #resolve!: (value: T) => void;
  #reject!: (cause: unknown) => void;
  constructor() {
    this.promise = new Promise<T>((resolve, reject) => {
      this.#resolve = resolve;
      this.#reject = reject;
    });
    // Producers can fail before their owner begins to await the result.
    void this.promise.catch(() => {});
  }
  resolve(value: T): void {
    if (this.settled) return;
    this.settled = true;
    this.value = value;
    this.#resolve(value);
  }
  reject(cause: unknown): void {
    if (this.settled) return;
    this.settled = true;
    this.#reject(cause);
  }
}
