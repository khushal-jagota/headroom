import type { ReviewHistoryKey } from '../navigation/contracts';

/**
 * The check of the skipped cards against the queue, before the first card shows. It ends in one of two ways. It
 * finishes with the skipped cohort that the check found. Or the wait runs out, and the cohort is what the page knows by then.
 * The wait starts at the first queue read, not at mount, so a slow first read does not use it up.
 */
export function createEntryCheck(options: { waitMs: number; knownCohort: () => ReviewHistoryKey[]; settle: (cohort: ReviewHistoryKey[]) => void }) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let done = false;
  return {
    /** The first queue read arrived. */
    started(): void {
      if (done || timer !== null) return;
      timer = setTimeout(() => { if (!done) { done = true; options.settle(options.knownCohort()); } }, options.waitMs);
    },
    finish(cohort: ReviewHistoryKey[]): void {
      if (done) return;
      done = true;
      if (timer !== null) clearTimeout(timer);
      options.settle(cohort);
    },
    dispose(): void { if (timer !== null) clearTimeout(timer); },
  };
}
