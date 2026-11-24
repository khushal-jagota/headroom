import { queryOptions } from "@tanstack/svelte-query";
import { fetchJson } from "../api";
import type { ResourceBodyResponse } from "../resourceVersions/contracts";
import type { ResourceQueryData } from "../resourceVersions/query";
import { BACKEND_READ_LIMIT_MS } from "../requestLimit/config";
import type { BackendSnapshot } from "./wire";

/** Discovery is explicit; ordinary reconciliation reads the saved machine facts. */
export function backendQuery(queryKey: readonly unknown[], ticketId?: string, refresh = false) {
  const path = "/api/conversation/backends";
  const key = ticketId === undefined ? path : `${path}?ticket_id=${encodeURIComponent(ticketId)}`;
  let refreshNextRead = refresh;
  return queryOptions<ResourceQueryData<BackendSnapshot[]>, Error, BackendSnapshot[]>({
    queryKey,
    meta: { resourceBody: true, resourceKey: key },
    staleTime: Infinity,
    retry: false,
    queryFn: async ({ signal }) => {
      const discover = refreshNextRead;
      refreshNextRead = false;
      const metadata: { decodedBytes?: number } = {};
      const result = await fetchJson<ResourceBodyResponse>(
        `${path}?versioned=true${discover ? "&refresh=true" : ""}`,
        { signal, method: ticketId === undefined ? "GET" : "POST",
          body: ticketId === undefined ? undefined : { ticket_id: ticketId },
          limitMs: BACKEND_READ_LIMIT_MS, responseMetadata: metadata });
      if (result.key !== key || typeof result.epoch !== "string" || typeof result.revision !== "string") {
        throw new Error("Invalid resource response");
      }
      return { body: (result.body as { backends: BackendSnapshot[] }).backends,
        epoch: result.epoch, revision: result.revision, decodedBytes: metadata.decodedBytes ?? 0 };
    },
    select: result => result.body
  });
}
