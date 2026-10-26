import { queryOptions } from '@tanstack/svelte-query';
import { fetchJson } from '../api';
import type { ResourceBodyResponse } from './contracts';

/** The body and its version enter the query cache in one admission. */
export type ResourceQueryData<T = unknown> = {
  body: T;
  epoch: string | null;
  revision: string | null;
  decodedBytes: number;
};

export function resourceQuery<T>(queryKey: readonly unknown[], path: string, versioned = false) {
  return queryOptions<ResourceQueryData<T>, Error, T>({
    queryKey,
    meta: { resourceBody: true, resourceKey: versioned ? path : undefined },
    queryFn: async ({ signal }) => {
      const metadata: { decodedBytes?: number } = {};
      if (versioned) {
        const result = await fetchJson<ResourceBodyResponse>(
          `/api/resource-body?key=${encodeURIComponent(path)}`, { signal, responseMetadata: metadata });
        if (result.key !== path || typeof result.epoch !== 'string' || typeof result.revision !== 'string') {
          throw new Error('Invalid resource response');
        }
        return { body: result.body as T, epoch: result.epoch, revision: result.revision,
          decodedBytes: metadata.decodedBytes ?? 0 };
      }
      const body = await fetchJson<T>(path, { signal, responseMetadata: metadata });
      return { body, epoch: null, revision: null, decodedBytes: metadata.decodedBytes ?? 0 };
    },
    select: (result) => result.body
  });
}
