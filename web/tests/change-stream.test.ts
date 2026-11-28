import { QueryObserver } from "@tanstack/svelte-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { startChangeStream, stopChangeStream } from "../src/lib/changeStream";
import { queryClient } from "../src/lib/queryClient";
import { get } from "svelte/store";
import { connectionStatus } from "../src/lib/changeStream";
import { STREAM_SILENCE_MS } from "../src/lib/updates/config";

class ControlledEventSource {
  static current: ControlledEventSource;
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  readyState = 1;
  private named = new Map<string, () => void>();
  constructor(readonly url: string) { ControlledEventSource.current = this; }
  addEventListener(name: string, listener: () => void) { this.named.set(name, listener); }
  emit(name: string) { this.named.get(name)?.(); }
  close() { this.closed = true; }
}

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

describe("contentless change stream refresh", () => {
  it("reconciles observer data on open and reopen, defers hidden commits, and ignores stopped source callbacks", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    stubWindowEvents();
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
    let visibilityState = "visible";
    const listeners = new Set<() => void>();
    vi.stubGlobal("document", {
      get visibilityState() { return visibilityState; },
      addEventListener: (_name: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_name: string, listener: () => void) => listeners.delete(listener)
    });
    vi.stubGlobal("EventSource", ControlledEventSource);
    let revision = 1;
    const revisions: number[] = [];
    const observer = new QueryObserver(queryClient, {
      queryKey: ["visible-ticket"], queryFn: async () => ({ revision })
    });
    const unsubscribe = observer.subscribe((result) => {
      if (result.data) revisions.push(result.data.revision);
    });
    await observer.refetch();
    startChangeStream();
    const old = ControlledEventSource.current;
    revision = 2;
    old.onopen?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(revisions.at(-1)).toBe(2);
    expect(get(connectionStatus)).toBe("connected");
    old.onerror?.();
    expect(get(connectionStatus)).toBe("unreachable");
    revision = 3;
    old.onopen?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(revisions.at(-1)).toBe(3);
    visibilityState = "hidden";
    revision = 4;
    old.onmessage?.();
    old.onmessage?.();
    await vi.advanceTimersByTimeAsync(3000);
    expect(revisions.at(-1)).toBe(3);
    visibilityState = "visible";
    for (const listener of listeners) listener();
    await vi.advanceTimersByTimeAsync(0);
    expect(revisions.at(-1)).toBe(4);
    // A queued visible flush and all old callbacks lose authority on stop.
    old.onmessage?.();
    stopChangeStream();
    expect(old.closed).toBe(true);
    expect(listeners.size).toBe(0);
    revision = 5;
    old.onopen?.();
    old.onmessage?.();
    old.onerror?.();
    await vi.advanceTimersByTimeAsync(3000);
    expect(revisions.at(-1)).toBe(4);
    startChangeStream();
    const replacement = ControlledEventSource.current;
    expect(replacement).not.toBe(old);
    replacement.onopen?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(revisions.at(-1)).toBe(5);
    expect(get(connectionStatus)).toBe("connected");
    old.onerror?.();
    expect(get(connectionStatus)).toBe("connected");
    unsubscribe();
  });

  it("reopens a permanently CLOSED stream and cancels recovery on stop", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    stubWindowEvents();
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
    vi.stubGlobal("document", { visibilityState: "visible", addEventListener: vi.fn(), removeEventListener: vi.fn() });
    vi.stubGlobal("EventSource", ControlledEventSource);
    startChangeStream();
    const first = ControlledEventSource.current;
    first.readyState = 2;
    first.onerror?.();
    await vi.advanceTimersByTimeAsync(3000);
    const second = ControlledEventSource.current;
    expect(second).not.toBe(first);
    expect(first.closed).toBe(true);
    second.onopen?.();
    expect(get(connectionStatus)).toBe("connected");
    first.onerror?.();
    expect(get(connectionStatus)).toBe("connected");
    second.readyState = 2;
    second.onerror?.();
    stopChangeStream();
    await vi.advanceTimersByTimeAsync(3000);
    expect(ControlledEventSource.current).toBe(second);
  });

  it("coalesces bursts at the first frame deadline for subscribed queries", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    stubWindowEvents();
    vi.stubGlobal("document", {
      visibilityState: "visible", addEventListener: vi.fn(), removeEventListener: vi.fn()
    });
    vi.stubGlobal("EventSource", ControlledEventSource);
    const mountedRead = vi.fn(async () => ({ revision: 1 }));
    const observer = new QueryObserver(queryClient, { queryKey: ["mounted"], queryFn: mountedRead });
    const unsubscribe = observer.subscribe(() => {});
    await observer.refetch();
    mountedRead.mockClear();
    startChangeStream();
    const source = ControlledEventSource.current;
    expect(source.url).toBe("/api/changes");
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(1_000);
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(499);
    expect(mountedRead).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(mountedRead).toHaveBeenCalledTimes(1);
    // A later burst gets its own fixed window, rather than an immediate refetch.
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(1_499);
    expect(mountedRead).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(mountedRead).toHaveBeenCalledTimes(2);
    unsubscribe();
  });

  // Distinct risk: a stream error must show the card at once, with no waiting time, and the
  // open must end it. The operating system's offline word must still win. No other test drives
  // the stream through these states.
  it("shows the card at once on a stream error and ends it on the next open", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    const windowEvents = stubWindowEvents();
    vi.stubGlobal("document", { visibilityState: "visible", addEventListener: vi.fn(), removeEventListener: vi.fn() });
    vi.stubGlobal("EventSource", ControlledEventSource);
    const probe = vi.fn(async () => { throw new Error("no network path"); });
    vi.stubGlobal("fetch", probe);
    startChangeStream();
    ControlledEventSource.current.onopen?.();
    expect(get(connectionStatus)).toBe("connected");
    // The error shows the card in the same tick, and the probe for the record starts at once.
    ControlledEventSource.current.onerror?.();
    expect(get(connectionStatus)).toBe("unreachable");
    expect(probe).toHaveBeenCalledWith("/api/meta", expect.objectContaining({ cache: "no-store" }));
    ControlledEventSource.current.onopen?.();
    expect(get(connectionStatus)).toBe("connected");
    // The operating system's word that there is no network wins, and the return reopens the stream.
    vi.stubGlobal("navigator", { onLine: false });
    ControlledEventSource.current.onerror?.();
    expect(get(connectionStatus)).toBe("offline");
    const beforeReturn = ControlledEventSource.current;
    vi.stubGlobal("navigator", { onLine: true });
    windowEvents.dispatchEvent(new Event("online"));
    expect(get(connectionStatus)).toBe("unreachable");
    expect(ControlledEventSource.current).not.toBe(beforeReturn);
  });

  it("treats a stream with no heartbeat for 37.5 s as lost, but only once the server has sent one", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    stubWindowEvents();
    vi.stubGlobal("document", { visibilityState: "visible", addEventListener: vi.fn(), removeEventListener: vi.fn() });
    vi.stubGlobal("EventSource", ControlledEventSource);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
    startChangeStream();
    const older = ControlledEventSource.current;
    older.onopen?.();
    // An older server sends only comment lines the page cannot see. Silence proves nothing there.
    await vi.advanceTimersByTimeAsync(STREAM_SILENCE_MS * 2);
    expect(ControlledEventSource.current).toBe(older);
    expect(get(connectionStatus)).toBe("connected");
    older.emit("heartbeat");
    await vi.advanceTimersByTimeAsync(STREAM_SILENCE_MS - 1);
    expect(ControlledEventSource.current).toBe(older);
    older.emit("heartbeat");
    await vi.advanceTimersByTimeAsync(STREAM_SILENCE_MS - 1);
    expect(ControlledEventSource.current).toBe(older);
    await vi.advanceTimersByTimeAsync(1);
    expect(older.closed).toBe(true);
    expect(ControlledEventSource.current).not.toBe(older);
    expect(get(connectionStatus)).toBe("unreachable");
    ControlledEventSource.current.onopen?.();
    expect(get(connectionStatus)).toBe("connected");
  });
});
