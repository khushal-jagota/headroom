// The manifest resource. GET /api/ticket-types is the single source of stage order
// / labels / gates / fields / ceiling range per registered type. Fetched ONCE under
// the keyed cache key "ticket-types" and NEVER event-invalidated (manifest types are
// static per deploy — no event kind maps to it, so the event-mapping completeness
// test is untouched by construction). The pure per-type lookup `lifecycleFor` lives
// in the framework-free lifecycle.ts so the unit test exercises the real selector.
import { fetchJson } from "./api";
import { resource, type ResourceHandle } from "./resources";
import type { TicketTypesResponse } from "./lifecycle";

export function manifestResource(): ResourceHandle<TicketTypesResponse> {
  return resource<TicketTypesResponse>("ticket-types", (signal) =>
    fetchJson("/api/ticket-types", { signal })
  );
}
