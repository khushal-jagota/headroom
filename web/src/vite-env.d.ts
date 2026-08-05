/// <reference types="svelte" />
/// <reference types="vite/client" />

interface PlannerDebug {
  sseOpens: number;
  sseReconciliations: number;
  flushes: number;
}

interface Window {
  __plannerDebug: PlannerDebug;
}
