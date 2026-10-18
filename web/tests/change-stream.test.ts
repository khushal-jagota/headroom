import { QueryObserver } from "@tanstack/svelte-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { startChangeStream, stopChangeStream } from "../src/lib/changeStream";
import { queryClient } from "../src/lib/queryClient";
import { get } from "svelte/store";
import { connectionStatus } from "../src/lib/changeStream";

class ControlledEventSource {
  static current: ControlledEventSource;
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) { ControlledEventSource.current = this; }
  close() { this.closed = true; }
}

afterEach(() => {
  stopChangeStream();
  queryClient.clear();
  vi.useRealTimers();
});

describe("contentless change stream refresh", () => {
  it("reconciles observer data on open and reopen, defers hidden commits, and ignores stopped source callbacks", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
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
    expect(get(connectionStatus)).toBe("reconnecting");
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

  it("coalesces bursts at the first frame deadline for subscribed queries", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
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
});
