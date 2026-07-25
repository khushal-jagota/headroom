/// <reference types="svelte" />
/// <reference types="vite/client" />

interface PlannerDebug {
  sseOpens: number;
  flushes: number;
}

interface Window {
  __plannerDebug: PlannerDebug;
}
