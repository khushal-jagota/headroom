import { describe, expect, it, vi } from "vitest";
import {
  COMPOSITION_STATE_EVENT,
  createReleaseMonitor,
  type ReleaseSnapshot
} from "../src/lib/releaseMonitor";

const BOOT_SHA = "0123456789abcdef0123456789abcdef01234567";
const NEXT_SHA = "89abcdef0123456789abcdef0123456789abcdef";

class FakeDocument extends EventTarget {
  visibilityState: DocumentVisibilityState = "visible";
}

function compositionEvent(active: boolean): Event {
  const event = new Event(COMPOSITION_STATE_EVENT);
  Object.defineProperty(event, "detail", { value: { active } });
  return event;
}

function response(appSha: string): Response {
  return new Response(JSON.stringify({ app_sha: appSha }), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
}

function setup(serverSha: string) {
  const document = new FakeDocument();
  const window = new EventTarget();
  const fetchMeta = vi.fn<typeof fetch>().mockResolvedValue(response(serverSha));
  const reload = vi.fn();
  const states: ReleaseSnapshot[] = [];
  const monitor = createReleaseMonitor({
    bootSha: BOOT_SHA,
    document: document as unknown as Document,
    window: window as unknown as Window,
    fetchMeta,
    reload
  });
  monitor.subscribe((state) => states.push({ ...state }));
  return { document, window, fetchMeta, reload, states, monitor };
}

describe("installed app release monitor", () => {
  it("defers an explicit reload while composition is active", async () => {
    const { document, reload, states, monitor } = setup(NEXT_SHA);
    monitor.start();
    await monitor.check();
    document.dispatchEvent(compositionEvent(true));

    monitor.requestReload();
    await vi.waitFor(() => expect(states.at(-1)?.reloadRequested).toBe(true));

    expect(states.at(-1)?.compositionActive).toBe(true);
    expect(reload).not.toHaveBeenCalled();
  });

  it("rechecks and completes a requested reload after composition becomes inactive", async () => {
    const { document, fetchMeta, reload, monitor } = setup(NEXT_SHA);
    monitor.start();
    await monitor.check();
    document.dispatchEvent(compositionEvent(true));
    monitor.requestReload();
    await vi.waitFor(() => expect(fetchMeta.mock.calls.length).toBeGreaterThanOrEqual(2));

    document.dispatchEvent(compositionEvent(false));

    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(fetchMeta.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

});
