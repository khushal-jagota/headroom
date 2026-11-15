import { elapsedSecondsSince, millisecondsUntilNextSecond } from './transcript';

/** The turn head, dock, and preview count from the same delivered prompt instant. */
export function elapsedTurnTime(startedAt: () => number | null) {
  let seconds = $state<number | null>(null);
  $effect(() => {
    const begun = startedAt();
    if (begun === null) { seconds = null; return; }
    let waiting: ReturnType<typeof setTimeout>;
    const tick = () => {
      const now = Date.now();
      seconds = elapsedSecondsSince(begun, now);
      waiting = setTimeout(tick, millisecondsUntilNextSecond(begun, now));
    };
    tick();
    return () => clearTimeout(waiting);
  });
  return { get seconds() { return seconds; } };
}
