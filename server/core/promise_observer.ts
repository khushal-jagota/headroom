import { addAbortListener } from 'node:events';

/** Stop observation at the time limit without cancelling admitted work. */
export async function observe_promise<T>(
  promise: Promise<T>,
  limit: number | AbortSignal,
): Promise<{ completed: true; value: T } | { completed: false }> {
  const { promise: expired, resolve } = Promise.withResolvers<{ completed: false }>();
  const elapsed = () => resolve({ completed: false });
  let timer: ReturnType<typeof setTimeout> | undefined;
  let subscription: ReturnType<typeof addAbortListener> | undefined;
  if (typeof limit === 'number') timer = setTimeout(elapsed, limit);
  else if (limit.aborted) elapsed();
  else subscription = addAbortListener(limit, elapsed);
  try {
    return await Promise.race([
      promise.then((value) => ({ completed: true as const, value })),
      expired,
    ]);
  } finally {
    clearTimeout(timer);
    subscription?.[Symbol.dispose]();
  }
}
