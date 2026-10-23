/** Rail actions share a physical scroller, but each action owns its page intent. */
export interface ScrollWindowRestoration {
  surface: HTMLElement;
  priority: "parent" | "card";
  /** Check the rendered page and retire an intent whose anchor disappeared. */
  eligible(): boolean;
  restore(): void;
  /** Capture movement before layout changes, then suspend until synchronization. */
  captureBeforePublication?(): void;
  /** A scripted scroll must not look like user travel to another window. */
  synchronize(): void;
  dispose(): void;
}

export interface ScrollWindowCoordinator {
  register(entry: ScrollWindowRestoration): () => void;
  captureBeforePublication(): void;
  /** Temporarily exclude native layout anchoring while explicit intents own this surface. */
  holdNativeAnchoring(surface: HTMLElement): () => void;
  /** Restore at most one owner per scroller. Return only eligible deferred work. */
  flush(): boolean;
  dispose(): void;
}

/** This phase runs after row publication and before the rail's final measurement. */
export function createScrollWindowCoordinator(): ScrollWindowCoordinator {
  const entries = new Set<ScrollWindowRestoration>();
  let disposed = false;
  const anchoring = new Map<HTMLElement, {holders:number; value:string; priority:string}>();
  function restoreAnchoring(surface:HTMLElement, held:{value:string;priority:string}) {
    if (held.value) surface.style.setProperty("overflow-anchor", held.value, held.priority);
    else surface.style.removeProperty("overflow-anchor");
  }
  return {
    register(entry) {
      if (!disposed) entries.add(entry);
      return () => entries.delete(entry);
    },
    captureBeforePublication() {
      if (!disposed) for (const entry of entries) entry.captureBeforePublication?.();
    },
    holdNativeAnchoring(surface) {
      if (disposed) return () => {};
      let held = anchoring.get(surface);
      if (!held) {
        held = {holders:0, value:surface.style.getPropertyValue("overflow-anchor"),
          priority:surface.style.getPropertyPriority("overflow-anchor")};
        anchoring.set(surface, held);
        surface.style.setProperty("overflow-anchor", "none");
      }
      held.holders++;
      let released = false;
      return () => {
        if (released) return;
        released = true;
        if (--held.holders === 0 && anchoring.get(surface) === held) {
          restoreAnchoring(surface, held);
          anchoring.delete(surface);
        }
      };
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
        // The losing intent remains owned by its action. The controller starts
        // another explicit publication phase after this beat, even without data.
        deferred ||= owners.some(entry => entry !== winner && entry.eligible());
      }
      // Publication itself can clamp any registered surface, even without a winner.
      // Reset every baseline without changing a deferred intent's held offset.
      for (const entry of entries) entry.synchronize();
      return deferred;
    },
    dispose() {
      disposed = true;
      for (const entry of entries) entry.dispose();
      entries.clear();
      for (const [surface, held] of anchoring) restoreAnchoring(surface, held);
      anchoring.clear();
    }
  };
}
