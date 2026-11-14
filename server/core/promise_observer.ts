/** Stop observation at the time limit without cancelling admitted work. */
export async function observe_promise<T>(
  promise: Promise<T>,
  milliseconds: number,
): Promise<{ completed: true; value: T } | { completed: false }> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise.then((value) => ({ completed: true as const, value })),
      new Promise<{ completed: false }>((resolve) => {
        timer = setTimeout(() => resolve({ completed: false }), milliseconds);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
