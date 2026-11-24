import type { InstallClock, UpdateLifecycle, WindowlessInstall } from './contracts';
import { installDelay } from './logic';

/** Window events and download completion share one continuous windowless interval. */
export function windowlessInstall(clock: InstallClock, windowCount: () => number, install: () => void, delay: number): WindowlessInstall {
  const state: UpdateLifecycle = { downloaded: false, noWindowSince: null };
  let timer: unknown;
  let stopped = false;
  function schedule(): void {
    if (timer !== undefined) clock.cancel(timer);
    const wait = installDelay(state, clock.now(), delay);
    if (stopped || wait === null) return;
    timer = clock.schedule(() => {
      if (!stopped && state.downloaded && windowCount() === 0) install();
    }, wait);
  }
  return {
    windowChanged: () => {
      if (windowCount() > 0) state.noWindowSince = null;
      else state.noWindowSince ??= clock.now();
      schedule();
    },
    downloadPending: () => { state.downloaded = false; schedule(); },
    downloaded: () => { state.downloaded = true; schedule(); },
    stop: () => { stopped = true; if (timer !== undefined) clock.cancel(timer); },
  };
}
