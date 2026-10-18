import { QueryObserver } from "@tanstack/svelte-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { startChangeStream, stopChangeStream } from "../src/lib/changeStream";
import { queryClient } from "../src/lib/queryClient";

class ControlledEventSource {
  static current: ControlledEventSource;
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) { ControlledEventSource.current = this; }
  close() {}
}

afterEach(() => {
  stopChangeStream();
  queryClient.clear();
  vi.useRealTimers();
});

describe("contentless change stream refresh", () => {
  it("coalesces bursts at the first frame deadline and refetches only mounted queries", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    vi.stubGlobal("document", {
      visibilityState: "visible", addEventListener: vi.fn(), removeEventListener: vi.fn()
    });
    vi.stubGlobal("EventSource", ControlledEventSource);
    const mountedRead = vi.fn(async () => ({ revision: 1 }));
    const inactiveRead = vi.fn(async () => ({ revision: 1 }));
    await queryClient.fetchQuery({ queryKey: ["inactive"], queryFn: inactiveRead });
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
    expect(inactiveRead).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryState(["inactive"])?.isInvalidated).toBe(true);
    // A later burst gets its own fixed window, rather than an immediate refetch.
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(1_499);
    expect(mountedRead).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(mountedRead).toHaveBeenCalledTimes(2);
    unsubscribe();
  });
});
