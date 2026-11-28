/** The change signal, coalesced for a sink that can fall behind. One ping stands for many commits. */
export interface ChangePings {
  /** The sink accepted more writes after `deliver` returned false. */
  drained(): void;
  /** A ping is waiting or in flight, so a quiet keep-alive adds nothing. */
  busy(): boolean;
  stop(): void;
}

export function coalesced_change_pings(
  signal: { subscribe(callback: () => void): () => void },
  /** Deliver one ping. False means that the sink is full and `drained` will say when it is not. */
  deliver: () => boolean,
): ChangePings {
  let changed = false;
  let owing = false;
  let stopped = false;
  let scheduled: NodeJS.Immediate | undefined;
  const flush = (): void => {
    scheduled = undefined;
    if (!owing && !stopped) {
      changed = false;
      owing = !deliver();
    }
  };
  const unsubscribe = signal.subscribe(() => {
    changed = true;
    if (!scheduled && !owing) scheduled = setImmediate(flush);
  });
  return {
    drained() {
      owing = false;
      if (changed && !stopped) flush();
    },
    busy: () => changed || owing,
    stop() {
      stopped = true;
      if (scheduled) clearImmediate(scheduled);
      unsubscribe();
    },
  };
}
