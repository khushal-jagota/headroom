/** A deadline observes owned cleanup. It never replaces the actual settlement promise. */
import { SHUTDOWN_GRACE_SECONDS } from '../core/config.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';

export async function observe_shutdown(
  settlement: Promise<void>,
  options: Partial<StopOptions>,
  message: string,
): Promise<void> {
  const deadline = options.deadline ?? performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      settlement,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(
          () => reject(new ServerLifecycleError(message)),
          Math.max(0, (deadline - performance.now() / 1000) * 1000),
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
