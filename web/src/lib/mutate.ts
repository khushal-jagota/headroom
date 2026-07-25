import { fetchJson, type FetchOptions } from "./api";
import { queryClient } from "./queryClient";

// The browser's write door. Send the request; only once the server has accepted
// it, mark every cached read stale so the screens on display refetch. A write
// that failed changed nothing, so it invalidates nothing. Awaiting the returned
// promise waits for those refetches, so a caller can act on fresh data.
export async function mutateJson<T = unknown>(
  path: string,
  options: FetchOptions = {}
): Promise<T> {
  const result = await fetchJson<T>(path, options);
  await queryClient.invalidateQueries();
  return result;
}
