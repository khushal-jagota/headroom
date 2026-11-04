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

// Reuse the monitor fixture to prove writes retain the page header without a preflight.
describe("page version headers", () => {
  it("sends writes directly despite an unavailable update check and preserves server refusals", async () => {
    const { fetchJson } = await import("../src/lib/api");
    const { startReleaseMonitor, stopReleaseMonitor, mutationHeaders } = await import("../src/lib/releaseMonitor");
    const page = listeners();
    const reload = vi.fn();
    const request = vi.fn<typeof globalThis.fetch>(async (path) => {
      if (path === "/api/meta") return new Response("{}", { status: 503 });
      if (path === "/api/refused") return new Response(JSON.stringify({error:{code:"validation",message:"Refresh page before making changes."}}), {status:409});
      return new Response("{}");
    });
    vi.stubGlobal("fetch", request);
    vi.stubGlobal("document", { ...page.target, querySelector: () => ({ content: "booted" }) });
    vi.stubGlobal("window", { ...page.target, location: { reload } });
    try {
      startReleaseMonitor();
      await fetchJson("/api/example", { method: "POST", body: {} });
      await fetchJson("/api/example", { method: "PATCH", body: {} });
      await fetchJson("/api/example", { method: "DELETE" });
      const mutations = request.mock.calls.filter(([path]) => path === "/api/example");
      expect(mutations).toHaveLength(3);
      for (const [,options] of mutations) expect(options?.headers).toMatchObject({"X-Panels-App-SHA":"booted"});
      expect(await mutationHeaders({"Content-Type":"application/octet-stream"})).toEqual({"content-type":"application/octet-stream","X-Panels-App-SHA":"booted"});
      await expect(fetchJson("/api/refused", {method:"POST",body:{}})).rejects.toMatchObject({code:"validation",message:"Refresh page before making changes."});
      expect(request.mock.calls.filter(([path]) => path === "/api/meta")).toHaveLength(1);
      expect(reload).not.toHaveBeenCalled();
    } finally { stopReleaseMonitor(); vi.unstubAllGlobals(); }
  });
});
