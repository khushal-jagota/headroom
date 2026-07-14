// The manifest resource. GET /api/worker-types is the single source of stage order
// / labels / gates / fields / ceiling range per registered Worker type. Fetched ONCE under
// the keyed cache key "worker-types" and NEVER event-invalidated (Worker types are
// static per deploy — no event kind maps to it, so the event-mapping completeness
// test is untouched by construction). The pure per-type lookup `lifecycleFor` lives
// in the framework-free lifecycle.ts so the unit test exercises the real selector.
import { fetchJson } from "./api";
import { resource, type ResourceHandle } from "./resources";
import type { WorkerTypesResponse } from "./lifecycle";

export function manifestResource(): ResourceHandle<WorkerTypesResponse> {
  return resource<WorkerTypesResponse>("worker-types", (signal) =>
    fetchJson("/api/worker-types", { signal })
  );
}
