/** Retry complete record operations only after the previous transaction rolled back. */
import { setTimeout as pause } from 'node:timers/promises';
import { performance } from 'node:perf_hooks';
import { PlannerError } from '../../core/contracts.ts';

// Python core/config.py values. No transaction or provider action survives these waits.
const WRITE_LOCK_RETRY_MILLISECONDS = 50;
const WRITE_FAILURE_FIRST_RETRY_MILLISECONDS = 500;
const WRITE_FAILURE_MAX_RETRY_MILLISECONDS = 30_000;

export interface RecordingSettlementOptions {
  /** Monotonic seconds, as asyncio's loop.time() uses in Python. */
  readonly retry_later_at?: number | null;
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
function operational_error(error: unknown): boolean {
  const code = error_code(error);
  if (code === null) {
    return false;
  }
  // SQLite OperationalError and filesystem OSError can pass on a later attempt.
  return (
    /^SQLITE_(?:ERROR|BUSY|LOCKED|READONLY|INTERRUPT|IOERR|FULL|CANTOPEN|PROTOCOL)(?:_|$)/.test(
      code,
    ) || /^E[A-Z0-9]+$/.test(code)
  );
}

export class RecordSettlement {
  readonly #pending = new Set<Promise<unknown>>();

  settle_recording<T>(
    conversation_id: string,
    operation: () => T | Promise<T>,
    options: RecordingSettlementOptions = {},
  ): Promise<T> {
    const task = this.#settle(conversation_id, operation, options.retry_later_at ?? null);
    this.#pending.add(task);
    void task.finally(() => this.#pending.delete(task)).catch(() => {});
    return task;
  }

  async #write<T>(operation: () => T | Promise<T>, retry_later_at: number | null): Promise<T> {
    while (true) {
      try {
        return await operation();
      } catch (error) {
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
  ): Promise<T> {
    if (retry_later_at !== null) {
      try {
        return await this.#write(operation, retry_later_at);
      } catch (error) {
        if (!operational_error(error)) {
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
    while (true) {
      try {
        return await this.#write(operation, null);
      } catch (error) {
        // The future core uses AbortError for caller cancellation, not save failure.
        if (error instanceof Error && error.name === 'AbortError') {
          throw error;
        }
        failures += 1;
        console.error(
          `conversation ${conversation_id} save failed (failure ${failures}); retrying in ${delay / 1000} s`,
          error,
        );
        await pause(delay);
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
