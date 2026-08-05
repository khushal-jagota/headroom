// The browser-test synchronisation primitive: how many times the change stream
// has opened, how many initial reconciliations have completed, and how many
// rounds of invalidation it has run.
export function ensureDebug(): PlannerDebug {
  if (!window.__plannerDebug) {
    window.__plannerDebug = {
      sseOpens: 0,
      sseReconciliations: 0,
      flushes: 0
    };
  }
  return window.__plannerDebug;
}
