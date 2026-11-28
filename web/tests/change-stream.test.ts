import { QueryObserver } from "@tanstack/svelte-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { startChangeStream, stopChangeStream } from "../src/lib/changeStream";
import { queryClient } from "../src/lib/queryClient";
import { get } from "svelte/store";
import { connectionStatus } from "../src/lib/changeStream";
import { STREAM_SILENCE_MS } from "../src/lib/updates/config";
import { FakeWebSocket, stubPage, stubPageDocument } from "./support/fakeLiveSocket";

// The page's own window events. `window` is the test's global object, so these stand in for it.
function stubWindowEvents() {
  const events = new EventTarget();
  vi.stubGlobal("addEventListener", (name: string, listener: () => void) => events.addEventListener(name, listener));
  vi.stubGlobal("removeEventListener", (name: string, listener: () => void) => events.removeEventListener(name, listener));
  return events;
}

afterEach(() => {
  stopChangeStream();
  queryClient.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Start the change stream and let the socket owner open its socket. */
async function startOpen() {
  startChangeStream();
  await vi.advanceTimersByTimeAsync(0);
  return FakeWebSocket.current;
}

describe("contentless change stream refresh", () => {
  it("reconciles observer data on ready and after a reopen, defers hidden commits, and ignores a stopped socket", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    stubWindowEvents();
    stubPage();
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
    let visibilityState = "visible";
    const listeners = new Set<() => void>();
    vi.stubGlobal("document", stubPageDocument({
      get visibilityState() { return visibilityState; },
      addEventListener: (_name: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_name: string, listener: () => void) => listeners.delete(listener)
    }));
    let revision = 1;
    const revisions: number[] = [];
    const observer = new QueryObserver(queryClient, {
      queryKey: ["visible-ticket"], queryFn: async () => ({ revision })
    });
    const unsubscribe = observer.subscribe((result) => {
      if (result.data) revisions.push(result.data.revision);
    });
    await observer.refetch();
    const old = await startOpen();
    revision = 2;
    old.ready();
    await vi.advanceTimersByTimeAsync(0);
    expect(revisions.at(-1)).toBe(2);
    expect(get(connectionStatus)).toBe("connected");
    old.drop();
    expect(get(connectionStatus)).toBe("unreachable");
    revision = 3;
    await vi.advanceTimersByTimeAsync(1_000);
    const reopened = FakeWebSocket.current;
    expect(reopened).not.toBe(old);
    reopened.ready();
    await vi.advanceTimersByTimeAsync(0);
    expect(revisions.at(-1)).toBe(3);
    visibilityState = "hidden";
    revision = 4;
    reopened.change();
    reopened.change();
    await vi.advanceTimersByTimeAsync(3000);
    expect(revisions.at(-1)).toBe(3);
    visibilityState = "visible";
    for (const listener of listeners) listener();
    await vi.advanceTimersByTimeAsync(0);
    expect(revisions.at(-1)).toBe(4);
    // A queued visible flush and all old callbacks lose authority on stop.
    reopened.change();
    stopChangeStream();
    expect(reopened.closed).toBe(true);
    expect(listeners.size).toBe(0);
    revision = 5;
    reopened.ready();
    reopened.change();
    reopened.drop();
    await vi.advanceTimersByTimeAsync(3000);
    expect(revisions.at(-1)).toBe(4);
    const replacement = await startOpen();
    expect(replacement).not.toBe(reopened);
    replacement.ready();
    await vi.advanceTimersByTimeAsync(0);
    expect(revisions.at(-1)).toBe(5);
    expect(get(connectionStatus)).toBe("connected");
    reopened.drop();
    expect(get(connectionStatus)).toBe("connected");
    unsubscribe();
  });

  it("coalesces bursts at the first frame deadline for subscribed queries", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    stubWindowEvents();
    stubPage();
    vi.stubGlobal("document", stubPageDocument());
    const mountedRead = vi.fn(async () => ({ revision: 1 }));
    const observer = new QueryObserver(queryClient, { queryKey: ["mounted"], queryFn: mountedRead });
    const unsubscribe = observer.subscribe(() => {});
    await observer.refetch();
    mountedRead.mockClear();
    const socket = await startOpen();
    expect(socket.url).toBe("wss://panels.test/api/live");
    socket.change();
    await vi.advanceTimersByTimeAsync(1_000);
    socket.change();
    await vi.advanceTimersByTimeAsync(499);
    expect(mountedRead).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(mountedRead).toHaveBeenCalledTimes(1);
    // A later burst gets its own fixed window, rather than an immediate refetch.
    socket.change();
    await vi.advanceTimersByTimeAsync(1_499);
    expect(mountedRead).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(mountedRead).toHaveBeenCalledTimes(2);
    unsubscribe();
  });

  // Distinct risk: a socket drop must show the card at once, with no waiting time, and the
  // ready frame must end it. The operating system's offline word must still win. No other test
  // drives the page's status through these states.
  it("shows no card at start, then the card at once on every drop, and ends it on the next ready", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    const windowEvents = stubWindowEvents();
    stubPage();
    vi.stubGlobal("document", stubPageDocument());
    const probe = vi.fn(async () => { throw new Error("no network path"); });
    vi.stubGlobal("fetch", probe);
    const first = await startOpen();
    // Before the first ready is a start, not a drop: no card.
    expect(get(connectionStatus)).toBe("connecting");
    first.ready();
    expect(get(connectionStatus)).toBe("connected");
    // The drop shows the card in the same tick, and the probe for the record starts at once.
    first.drop();
    expect(get(connectionStatus)).toBe("unreachable");
    expect(probe).toHaveBeenCalledWith("/api/meta", expect.objectContaining({ cache: "no-store" }));
    await vi.advanceTimersByTimeAsync(1_000);
    FakeWebSocket.current.ready();
    expect(get(connectionStatus)).toBe("connected");
    // The operating system's word that there is no network wins, and the return reopens the socket.
    vi.stubGlobal("navigator", { onLine: false });
    FakeWebSocket.current.drop();
    expect(get(connectionStatus)).toBe("offline");
    const beforeReturn = FakeWebSocket.current;
    vi.stubGlobal("navigator", { onLine: true });
    windowEvents.dispatchEvent(new Event("online"));
    expect(get(connectionStatus)).toBe("unreachable");
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeWebSocket.current).not.toBe(beforeReturn);
  });

  it("treats a socket with no frame for 37.5 s as lost, and each frame moves the deadline", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    stubWindowEvents();
    stubPage();
    vi.stubGlobal("document", stubPageDocument());
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
    const older = await startOpen();
    older.ready();
    await vi.advanceTimersByTimeAsync(STREAM_SILENCE_MS - 1);
    expect(get(connectionStatus)).toBe("connected");
    older.heartbeat();
    await vi.advanceTimersByTimeAsync(STREAM_SILENCE_MS - 1);
    expect(older.closed).toBe(false);
    expect(get(connectionStatus)).toBe("connected");
    await vi.advanceTimersByTimeAsync(1);
    expect(older.closed).toBe(true);
    expect(get(connectionStatus)).toBe("unreachable");
    await vi.advanceTimersByTimeAsync(1_000);
    expect(FakeWebSocket.current).not.toBe(older);
    FakeWebSocket.current.ready();
    expect(get(connectionStatus)).toBe("connected");
  });

  it("answers an update-needed close with the update status read, no card, and no reopen", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    stubWindowEvents();
    stubPage();
    vi.stubGlobal("document", stubPageDocument());
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
    const updates = vi.fn(async () => ({}));
    const observer = new QueryObserver(queryClient, { queryKey: ["updates"], queryFn: updates });
    const unsubscribe = observer.subscribe(() => {});
    await observer.refetch();
    updates.mockClear();
    const socket = await startOpen();
    socket.open();
    socket.drop(4001);
    await vi.advanceTimersByTimeAsync(0);
    expect(updates).toHaveBeenCalledTimes(1);
    expect(get(connectionStatus)).toBe("connected");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(FakeWebSocket.all).toHaveLength(1);
    unsubscribe();
  });
});
