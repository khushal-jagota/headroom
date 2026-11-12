/** A deadline observes owned cleanup. It never replaces the actual settlement promise. */
import { SHUTDOWN_GRACE_SECONDS } from '../core/config.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { ConversationStartAborted } from '../runtime/abort.ts';
import { DeliveryCancelled } from './backends/contracts.ts';

/** Expected caller cancellation during stop is not an excuse for cleanup failure. */
export function is_shutdown_business_cancellation(error: unknown): boolean {
  return (
    error instanceof ConversationStartAborted ||
    error instanceof DeliveryCancelled ||
    (error instanceof DOMException && error.name === 'AbortError')
  );
}

export async function observe_shutdown(
  settlement: Promise<unknown>,
  options: Partial<StopOptions>,
  message: string,
  pending: readonly string[] = [],
): Promise<void> {
  const deadline = options.deadline ?? performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS;
  // A zero-budget observer still owns the original promise's rejection.
  void settlement.catch(() => {});
  const remaining = deadline * 1000 - performance.now();
  if (remaining <= 0) throw new ShutdownDeadlineExceeded(pending, message);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      settlement,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new ShutdownDeadlineExceeded(pending, message)), remaining);
      }),
    ]);
    // Timers cannot preempt synchronous JavaScript or SQLite. Check after completion too.
    if (performance.now() / 1000 >= deadline) throw new ShutdownDeadlineExceeded(pending, message);
  } finally {
    clearTimeout(timer);
  }
}
