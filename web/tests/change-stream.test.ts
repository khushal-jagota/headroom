import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { INVALIDATE_DEBOUNCE_MS } from "../src/lib/changeStream";

const fakes = vi.hoisted(() => ({
  invalidateQueries: vi.fn<() => Promise<void>>()
}));

vi.mock("../src/lib/queryClient", () => ({
  queryClient: {
    invalidateQueries: fakes.invalidateQueries
  }
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }
}

class FakeDocument {
  visibilityState: "visible" | "hidden" = "visible";
  private readonly listeners = new Map<string, Set<() => void>>();

  addEventListener(type: string, handler: () => void): void {
    const held = this.listeners.get(type) ?? new Set<() => void>();
    held.add(handler);
    this.listeners.set(type, held);
  }

  removeEventListener(type: string, handler: () => void): void {
    this.listeners.get(type)?.delete(handler);
  }

  /** Leave the tab, or come back to it, exactly as a browser reports it. */
  becomes(visibility: "visible" | "hidden"): void {
    this.visibilityState = visibility;
    for (const handler of this.listeners.get("visibilitychange") ?? []) handler();
  }
}

type ChangeStreamModule = typeof import("../src/lib/changeStream");

let loadedModule: ChangeStreamModule | null = null;
let unsubscribe: (() => void) | null = null;
let statuses: string[] = [];
let debug: PlannerDebug;
let fakeDocument: FakeDocument;

async function loadChangeStream(): Promise<ChangeStreamModule> {
  const module = await import("../src/lib/changeStream");
  loadedModule = module;
  unsubscribe = module.connectionStatus.subscribe((status) => statuses.push(status));
  return module;
}

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  fakes.invalidateQueries.mockReset();
  fakes.invalidateQueries.mockResolvedValue();
  FakeEventSource.instances = [];
  statuses = [];
  debug = { sseOpens: 0, sseReconciliations: 0, flushes: 0 };
  fakeDocument = new FakeDocument();
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("document", fakeDocument);
  vi.stubGlobal("window", {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    __plannerDebug: debug
  });
});

afterEach(() => {
  loadedModule?.stopChangeStream();
  loadedModule = null;
  unsubscribe?.();
  unsubscribe = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("change stream", () => {
  it("opens one changes stream and a second start reuses it", async () => {
    const { startChangeStream } = await loadChangeStream();

    startChangeStream();
    startChangeStream();

    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe("/api/changes");
    expect(statuses.at(-1)).toBe("reconnecting");
    expect(fakes.invalidateQueries).not.toHaveBeenCalled();
  });

  it("collapses a finite burst into one invalidation at the first frame's deadline", async () => {
    const { startChangeStream } = await loadChangeStream();
    startChangeStream();
    const source = FakeEventSource.instances[0];
    source.onopen?.();

    source.onmessage?.();
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(INVALIDATE_DEBOUNCE_MS - 1);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);

    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(1);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(2);
    expect(debug.flushes).toBe(2);

    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(INVALIDATE_DEBOUNCE_MS);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(3);
  });

  it("invalidates at each fixed deadline while frames stay sustained beyond two windows", async () => {
    const { startChangeStream } = await loadChangeStream();
    startChangeStream();
    const source = FakeEventSource.instances[0];
    source.onopen?.();

    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(500);
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(500);
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(500);
    source.onmessage?.();
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(500);
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(500);
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(500);
    source.onmessage?.();
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(3);

    await vi.advanceTimersByTimeAsync(500);
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(500);
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(500);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(4);
    expect(debug.flushes).toBe(4);
  });

  it("reports reconnecting on error and catches up when the same stream reopens", async () => {
    const { startChangeStream } = await loadChangeStream();
    startChangeStream();
    const source = FakeEventSource.instances[0];
    source.onopen?.();

    source.onerror?.();
    expect(statuses.at(-1)).toBe("reconnecting");
    expect(FakeEventSource.instances).toHaveLength(1);

    source.onopen?.();
    await vi.waitFor(() => expect(debug.sseReconciliations).toBe(2));
    expect(statuses.at(-1)).toBe("connected");
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(2);
    expect(debug).toEqual({
      sseOpens: 2,
      sseReconciliations: 2,
      flushes: 2
    });
  });

  it("holds every change that lands while the tab is hidden, and answers them all at once on the way back", async () => {
    const { startChangeStream } = await loadChangeStream();
    startChangeStream();
    const source = FakeEventSource.instances[0];
    source.onopen?.();
    await vi.waitFor(() => expect(debug.sseReconciliations).toBe(1));

    fakeDocument.becomes("hidden");
    source.onmessage?.();
    source.onmessage?.();
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(INVALIDATE_DEBOUNCE_MS);
    // Nothing on screen to bring up to date, so nothing was fetched for it.
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);

    fakeDocument.becomes("visible");
    await vi.waitFor(() => expect(fakes.invalidateQueries).toHaveBeenCalledTimes(2));

    // Coming back to a tab that missed nothing asks for nothing.
    fakeDocument.becomes("hidden");
    fakeDocument.becomes("visible");
    await vi.advanceTimersByTimeAsync(INVALIDATE_DEBOUNCE_MS);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(2);
  });

  it("stops cleanly, cancels a pending flush, and ignores stale callbacks", async () => {
    const { startChangeStream, stopChangeStream } = await loadChangeStream();
    startChangeStream();
    const source = FakeEventSource.instances[0];
    source.onopen?.();
    source.onmessage?.();

    stopChangeStream();

    expect(source.closed).toBe(true);
    expect(statuses.at(-1)).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(INVALIDATE_DEBOUNCE_MS);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);

    source.onopen?.();
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(INVALIDATE_DEBOUNCE_MS);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);
    expect(debug).toEqual({
      sseOpens: 1,
      sseReconciliations: 0,
      flushes: 1
    });
  });
});
