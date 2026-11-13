/** A failed native close retains its capability until a later cleanup attempt. */
export function close_retained<T>(handles: Map<number, T>, close: (value: T) => void): unknown {
  const failures: unknown[] = [];
  for (const [handle, value] of handles) {
    try {
      close(value);
      handles.delete(handle);
    } catch (error) {
      failures.push(error);
    }
  }
  return failures.length > 1
    ? new AggregateError(failures, 'Native descriptor cleanup failed.')
    : failures[0];
}
