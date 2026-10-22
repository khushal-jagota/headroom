import { describe, expect, it, vi } from "vitest";
import { COMPOSITION_STATE_EVENT, createReleaseMonitor, type ReleaseSnapshot } from "../src/lib/releaseMonitor";

// One monitor now feeds both ShellStatus mounts, so the hold that keeps a reload
// back until a message is safe is no longer owned by the component that shows it.
// Nothing else proves a reload cannot land mid-message.
function listeners() {
  const handlers = new Map<string, Set<EventListener>>();
  return {
    target: {
      addEventListener: (type: string, handler: EventListener) =>
        void (handlers.get(type) ?? handlers.set(type, new Set()).get(type)!).add(handler),
      removeEventListener: (type: string, handler: EventListener) => void handlers.get(type)?.delete(handler),
      visibilityState: "visible" as const
    },
    fire(type: string, event: Event) {
      for (const handler of [...(handlers.get(type) ?? [])]) handler(event);
    }
  };
}

function composition(active: boolean): Event {
  return { type: COMPOSITION_STATE_EVENT, detail: { active } } as unknown as Event;
}

describe("the release monitor's composition hold", () => {
  it("keeps a requested reload back until the message is safe, then reloads once", async () => {
    const page = listeners();
    const reload = vi.fn();
    const fetchMeta = vi.fn(async () => ({ ok: true, json: async () => ({ app_sha: "new" }) }) as unknown as Response);
    const monitor = createReleaseMonitor({
      bootSha: "booted", document: page.target, window: page.target, fetchMeta, reload
    });
    const seen: ReleaseSnapshot[] = [];
    monitor.subscribe((value) => seen.push(value));

    monitor.start();
    await monitor.check();
    expect(seen.at(-1)?.updateAvailable).toBe(true);

    page.fire(COMPOSITION_STATE_EVENT, composition(true));
    monitor.requestReload();
    await monitor.check();
    expect(reload).not.toHaveBeenCalled();
    expect(seen.at(-1)).toMatchObject({ compositionActive: true, reloadRequested: true });

    page.fire(COMPOSITION_STATE_EVENT, composition(false));
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  });

  it("does not reload a composing page that never asked for one", async () => {
    const page = listeners();
    const reload = vi.fn();
    const fetchMeta = vi.fn(async () => ({ ok: true, json: async () => ({ app_sha: "new" }) }) as unknown as Response);
    const monitor = createReleaseMonitor({
      bootSha: "booted", document: page.target, window: page.target, fetchMeta, reload
    });
    monitor.start();
    await monitor.check();
    page.fire(COMPOSITION_STATE_EVENT, composition(true));
    page.fire(COMPOSITION_STATE_EVENT, composition(false));
    await monitor.check();
    expect(reload).not.toHaveBeenCalled();
  });
});
