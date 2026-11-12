/** The sole production rates door. Tests supply a recording fetcher. */
import { backend_io_is_test } from '../snapshot/environment.ts';
import { LITELLM_RATES_URL, RATES_TIMEOUT_MS } from './configuration.ts';
export async function fetchRates(options: { signal?: AbortSignal } = {}): Promise<unknown> {
  if (backend_io_is_test())
    throw new Error(
      'Production usage rates I/O is unavailable in tests. Inject a recording fetcher.',
    );
  const timeout = AbortSignal.timeout(RATES_TIMEOUT_MS);
  const signal =
    options.signal === undefined ? timeout : AbortSignal.any([options.signal, timeout]);
  const response = await fetch(LITELLM_RATES_URL, { signal });
  if (!response.ok) throw new Error('The usage rates source did not answer successfully.');
  return await response.json();
}
