/** HTTP writes remain owned through SQLite contention and process shutdown. */
import { HttpError } from '../../core/http/errors.ts';
import { contention_only_write } from '../runtime/synchronization.ts';
export class ConversationHttpWriteScope {
  readonly #pending = new Set<Promise<unknown>>();
  #sealed = false;
  run<T>(operation: () => T): Promise<T> {
    if (this.#sealed) throw new HttpError(503, 'the conversation system is unavailable');
    const task = contention_only_write(operation);
    this.#pending.add(task);
    void task.finally(() => this.#pending.delete(task)).catch(() => {});
    return task;
  }
  seal(): void {
    this.#sealed = true;
  }
  async drain(): Promise<void> {
    while (this.#pending.size) await Promise.allSettled([...this.#pending]);
  }
}
