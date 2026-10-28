/** One attempt spends its budget only while its conversation is visible and open. */
export function restorationWait(milliseconds: number, expire: () => void) {
  let remaining = milliseconds;
  let started: number | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  function pause(): void {
    if (started !== null) remaining = Math.max(0, remaining - performance.now() + started);
    started = null;
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }
  return {
    active(value: boolean): void {
      if (stopped || value === (started !== null)) return;
      if (!value) { pause(); return; }
      started = performance.now();
      timer = setTimeout(() => {
        pause();
        stopped = true;
        expire();
      }, remaining);
    },
    stop(): void { pause(); stopped = true; }
  };
}
