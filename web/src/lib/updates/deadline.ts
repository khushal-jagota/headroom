/** Browser timers overflow above 2^31-1 ms. Retained releases can outlive that. */
const MAX_TIMEOUT_MS = 2_147_483_647;

export function atUpdateDeadline(deadline: number, action: () => void): () => void {
  let timer: number;
  const schedule = () => {
    const remaining = deadline - Date.now();
    if (remaining <= 0) { action(); return; }
    timer = window.setTimeout(schedule, Math.min(remaining + 1, MAX_TIMEOUT_MS));
  };
  schedule();
  return () => window.clearTimeout(timer);
}
