/** Rail actions share a physical scroller, but each action owns its page intent. */
export interface ScrollWindowRestoration {
  surface: HTMLElement;
  priority: "parent" | "card";
  /** Check the rendered page and retire an intent whose anchor disappeared. */
  eligible(): boolean;
  restore(): void;
  /** A scripted scroll must not look like user travel to another window. */
  synchronize(): void;
  dispose(): void;
}

export interface ScrollWindowCoordinator {
  register(entry: ScrollWindowRestoration): () => void;
  /** Restore at most one owner per scroller. Return only eligible deferred work. */
  flush(): boolean;
  dispose(): void;
}

/** This phase runs after row publication and before the rail's final measurement. */
export function createScrollWindowCoordinator(): ScrollWindowCoordinator {
  const entries = new Set<ScrollWindowRestoration>();
  let disposed = false;
  return {
    register(entry) {
      if (!disposed) entries.add(entry);
      return () => entries.delete(entry);
    },
    flush() {
      if (disposed) return false;
      const ready = new Map<HTMLElement, ScrollWindowRestoration[]>();
      for (const entry of entries) {
        if (!entry.eligible()) continue;
        const owners = ready.get(entry.surface) ?? [];
        owners.push(entry);
        ready.set(entry.surface, owners);
      }
      let deferred = false;
      for (const owners of ready.values()) {
        const winner = owners.find(entry => entry.priority === "parent") ?? owners[0];
        winner.restore();
        for (const entry of entries) if (entry.surface === winner.surface) entry.synchronize();
        // The losing intent remains owned by its action. The controller starts
        // another explicit publication phase after this beat, even without data.
        deferred ||= owners.some(entry => entry !== winner && entry.eligible());
      }
      return deferred;
    },
    dispose() {
      disposed = true;
      for (const entry of entries) entry.dispose();
      entries.clear();
    }
  };
}
