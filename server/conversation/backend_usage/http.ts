/** The production transport refuses test contexts before any credential reaches fetch. */
import type { UsageHttpGet } from './contracts.ts';
import { backend_io_is_test } from '../snapshot/environment.ts';
export const http_get_usage: UsageHttpGet = async (url, headers, timeout_seconds, options = {}) => {
  if (backend_io_is_test())
    throw new Error('Production backend I/O is unavailable in tests. Inject recording adapters.');
  const timeout = AbortSignal.timeout(timeout_seconds * 1000);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const response = await fetch(url, { headers, signal });
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    signal.throwIfAborted();
  }
  return { status_code: response.status, body };
};
