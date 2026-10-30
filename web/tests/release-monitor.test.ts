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

// The fetch helper must fence writes when a tab misses the change stream.
// The existing monitor fixture proves this without a connected browser host.
describe("the stale page mutation fence", () => {
  it("checks boot SHA on wake and before writes, attaches the SHA, and refuses stale or unverifiable writes", async () => {
    const { fetchJson } = await import("../src/lib/api");
    const { startReleaseMonitor, stopReleaseMonitor } = await import("../src/lib/releaseMonitor");
    const page = listeners();
    let serverSha = "booted";
    let available = true;
    const reload = vi.fn();
    const request = vi.fn<typeof globalThis.fetch>(async (path) => {
      if (path === "/api/meta") return new Response(JSON.stringify({ app_sha: serverSha }), { status: available ? 200 : 503 });
      return new Response("{}");
    });
    vi.stubGlobal("fetch", request);
    vi.stubGlobal("document", { ...page.target, querySelector: () => ({ content: "booted" }) });
    vi.stubGlobal("window", { ...page.target, location: { reload } });
    try {
      startReleaseMonitor();
      await fetchJson("/api/example", { method: "POST", body: {} });
      const mutation = request.mock.calls.find(([path]) => path === "/api/example");
      expect(mutation?.[1]?.headers).toMatchObject({ "X-Panels-App-SHA": "booted" });
      const checks = request.mock.calls.filter(([path]) => path === "/api/meta").length;
      serverSha = "new";
      page.fire("pageshow", { type: "pageshow" } as Event);
      await vi.waitFor(() => expect(request.mock.calls.filter(([path]) => path === "/api/meta").length).toBeGreaterThan(checks));
      await expect(fetchJson("/api/example", { method: "PATCH", body: {} })).rejects.toThrow("Refresh page");
      const { interruptConversation } = await import("../src/lib/conversation/wire");
      const { postVoiceTranscription } = await import("../src/lib/conversation/voiceCapture");
      const { videoUploadAdapter } = await import("../src/lib/conversation/videoUploads/adapter");
      await expect(interruptConversation("conversation")).rejects.toThrow("Refresh page");
      await expect(postVoiceTranscription({ audio: "audio", media_type: "audio/webm" })).rejects.toThrow("Refresh page");
      const file = new File(["video"], "clip.mp4", { type: "video/mp4" });
      await expect(videoUploadAdapter.cancel("upload")).rejects.toThrow("Refresh page");
      await expect(videoUploadAdapter.transfer("upload", file, new AbortController().signal, () => {})).rejects.toThrow("Refresh page");
      serverSha = "booted";
      available = false;
      await expect(fetchJson("/api/example", { method: "DELETE" })).rejects.toThrow("Cannot confirm");
      expect(request.mock.calls.filter(([path]) => path === "/api/example")).toHaveLength(1);
      expect(reload).not.toHaveBeenCalled();
    } finally { stopReleaseMonitor(); }
  });
});
