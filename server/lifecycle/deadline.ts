import { ShutdownDeadlineExceeded, type StopOptions } from './contracts.ts';

/** Only observes. The caller must retain the original actual settlement promise. */
export async function observe_shutdown(
  actual: Promise<unknown>,
  options: StopOptions,
  name: string,
): Promise<void> {
  // Install a rejection observer even if there is no budget left.
  void actual.catch(() => {});
  const remaining = options.deadline * 1000 - performance.now();
  if (remaining <= 0) throw new ShutdownDeadlineExceeded([name]);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      actual,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new ShutdownDeadlineExceeded([name])), remaining);
      }),
    ]);
    // A blocked event loop can settle work before an overdue timer callback runs.
    if (performance.now() / 1000 >= options.deadline) throw new ShutdownDeadlineExceeded([name]);
  } finally {
    clearTimeout(timer);
  }
}
