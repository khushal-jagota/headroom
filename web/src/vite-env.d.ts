/// <reference types="svelte" />
/// <reference types="vite/client" />

interface PlannerDebug {
  flushes: number;
  wsOpens: number;
  cursor: number;
  invalidations: Record<string, number>;
  events: number;
}

interface Window {
  __plannerDebug: PlannerDebug;
  Planner?: {
    markdown?: {
      render(text: string): HTMLElement;
    };
  };
}
