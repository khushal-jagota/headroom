/** Retry complete record operations only after the previous transaction rolled back. */
import { setTimeout as pause } from 'node:timers/promises';
import { performance } from 'node:perf_hooks';
import { PlannerError } from '../../core/contracts.ts';

// Python core/config.py values. No transaction or provider action survives these waits.
const WRITE_LOCK_RETRY_MILLISECONDS = 50;
const WRITE_FAILURE_FIRST_RETRY_MILLISECONDS = 500;
const WRITE_FAILURE_MAX_RETRY_MILLISECONDS = 30_000;
// A permanent error gets this many retries on the backoff above (0.5 + 1 + 2 s), then the save gives up.
const PERMANENT_SAVE_FAILURE_RETRIES = 3;

export interface RecordingSettlementOptions {
  /** Monotonic seconds, as asyncio's loop.time() uses in Python. */
  readonly retry_later_at?: number | null;
  /** Runtime shutdown cancels outer generic retry waits, never an active write/BUSY loop. */
  readonly shutdown_signal?: AbortSignal;
  /** One attempt with no outer retry, for a conversation whose save already gave up. */
  readonly single_attempt?: boolean;
}
function error_code(error: unknown): string | null {
  if (
    error !== null &&
    typeof error === 'object' &&
    'code' in error &&
    typeof error.code === 'string'
  ) {
    return error.code;
  }
  return null;
}
/** Transient errors can pass later. Permanent ones fail this write. Unusable ones fail every write. */
export type SaveErrorKind = 'transient' | 'permanent' | 'database_unusable';
export function save_error_kind(error: unknown): SaveErrorKind {
  const code = error_code(error);
  if (code === null) return 'permanent';
  if (/^SQLITE_(?:CORRUPT|NOTADB)(?:_|$)/.test(code)) return 'database_unusable';
  // Contention, I/O, a full disk, and filesystem errors can pass on a later attempt.
  // SQLITE_ERROR is permanent: schema drift such as "no such column" reports itself that way.
  if (
    /^SQLITE_(?:BUSY|LOCKED|READONLY|INTERRUPT|IOERR|FULL|CANTOPEN|PROTOCOL)(?:_|$)/.test(code) ||
    /^E[A-Z0-9]+$/.test(code)
  )
    return 'transient';
  return 'permanent';
}

export class RecordSettlement {
  readonly #pending = new Set<Promise<unknown>>();
  #database_unusable: unknown = null;

  /** The first error that made the database unusable, or null. Every later write refuses with it. */
  get database_unusable_error(): unknown {
    return this.#database_unusable;
  }

  settle_recording<T>(
    conversation_id: string,
    operation: () => T | Promise<T>,
    options: RecordingSettlementOptions = {},
  ): Promise<T> {
    const task = this.#settle(
      conversation_id,
      operation,
      options.retry_later_at ?? null,
      options.shutdown_signal,
      options.single_attempt ?? false,
    );
    this.#pending.add(task);
    void task.finally(() => this.#pending.delete(task)).catch(() => {});
    return task;
  }

  retain_recording<T>(task: Promise<T>): Promise<T> {
    this.#pending.add(task);
    void task.finally(() => this.#pending.delete(task)).catch(() => {});
    return task;
  }

  write_recording<T>(operation: () => T | Promise<T>): Promise<T> {
    return this.retain_recording(this.#write(operation, null));
  }

  async #write<T>(operation: () => T | Promise<T>, retry_later_at: number | null): Promise<T> {
    while (true) {
      if (this.#database_unusable !== null) throw this.#database_unusable;
      try {
        return await operation();
      } catch (error) {
        if (save_error_kind(error) === 'database_unusable') this.#database_unusable ??= error;
        if (!error_code(error)?.startsWith('SQLITE_BUSY')) {
          throw error;
        }
        if (retry_later_at !== null && performance.now() / 1000 >= retry_later_at) {
          throw error;
        }
        await pause(WRITE_LOCK_RETRY_MILLISECONDS);
      }
    }
  }

  async #settle<T>(
    conversation_id: string,
    operation: () => T | Promise<T>,
    retry_later_at: number | null,
    shutdown_signal: AbortSignal | undefined,
    single_attempt: boolean,
  ): Promise<T> {
    shutdown_signal?.throwIfAborted();
    if (retry_later_at !== null) {
      try {
        return await this.#write(operation, retry_later_at);
      } catch (error) {
        if (save_error_kind(error) !== 'transient') {
          throw error;
        }
        const detail = error instanceof Error ? error.message : String(error);
        throw new PlannerError(
          'retry_later',
          `The conversation could not save this item yet: ${detail}`,
        );
      }
    }
    let delay = WRITE_FAILURE_FIRST_RETRY_MILLISECONDS;
    let failures = 0;
    let permanent_failures = 0;
    while (true) {
      shutdown_signal?.throwIfAborted();
      try {
        return await this.#write(operation, null);
      } catch (error) {
        // Only runtime shutdown cancels this outer wait. Caller departure keeps the save owned.
        if (error instanceof Error && error.name === 'AbortError') {
          throw error;
        }
        shutdown_signal?.throwIfAborted();
        // Transient errors retry until they pass. A permanent error gives up after a few retries.
        const kind = save_error_kind(error);
        if (single_attempt || kind === 'database_unusable') throw error;
        if (kind === 'permanent' && ++permanent_failures > PERMANENT_SAVE_FAILURE_RETRIES)
          throw error;
        failures += 1;
        console.error(
          `conversation ${conversation_id} save failed (failure ${failures}); retrying in ${delay / 1000} s`,
          error,
        );
        await pause(delay, undefined, shutdown_signal ? { signal: shutdown_signal } : undefined);
        delay = Math.min(delay * 2, WRITE_FAILURE_MAX_RETRY_MILLISECONDS);
      }
    }
  }

  async finish_writes(): Promise<void> {
    while (this.#pending.size > 0) {
      await Promise.allSettled([...this.#pending]);
    }
  }
}
