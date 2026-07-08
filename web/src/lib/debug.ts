export function ensureDebug(): PlannerDebug {
  if (!window.__plannerDebug) {
    window.__plannerDebug = {
      flushes: 0,
      wsOpens: 0,
      cursor: 0,
      invalidations: {},
      events: 0
    };
  }
  return window.__plannerDebug;
}

export function countInvalidation(key: string): void {
  const debug = ensureDebug();
  debug.invalidations[key] = (debug.invalidations[key] || 0) + 1;
}
