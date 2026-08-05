import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

type ChangeStreamModule = typeof import("../src/lib/changeStream");

let loadedModule: ChangeStreamModule | null = null;
let unsubscribe: (() => void) | null = null;
let statuses: string[] = [];
let debug: PlannerDebug;

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
  vi.stubGlobal("EventSource", FakeEventSource);
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

  it("marks an open stream connected and reconciles missed changes", async () => {
    const { startChangeStream } = await loadChangeStream();
    startChangeStream();

    FakeEventSource.instances[0].onopen?.();
    await vi.waitFor(() => expect(debug.sseReconciliations).toBe(1));

    expect(statuses.at(-1)).toBe("connected");
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);
    expect(debug).toEqual({
      sseOpens: 1,
      sseReconciliations: 1,
      flushes: 1
    });
  });

  it("reports initial reconciliation only after its invalidation resolves", async () => {
    let resolveInvalidation!: () => void;
    fakes.invalidateQueries.mockReturnValueOnce(
      new Promise<void>((resolve) => {
        resolveInvalidation = resolve;
      })
    );
    const { startChangeStream } = await loadChangeStream();
    startChangeStream();

    FakeEventSource.instances[0].onopen?.();

    expect(debug.sseOpens).toBe(1);
    expect(debug.flushes).toBe(1);
    expect(debug.sseReconciliations).toBe(0);

    resolveInvalidation();
    await vi.waitFor(() => expect(debug.sseReconciliations).toBe(1));
  });

  it("collapses a burst into one trailing invalidation and later changes into another", async () => {
    const { startChangeStream } = await loadChangeStream();
    startChangeStream();
    const source = FakeEventSource.instances[0];
    source.onopen?.();

    source.onmessage?.();
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(249);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);

    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(249);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(2);
    expect(debug.flushes).toBe(2);

    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(250);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(3);
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

  it("stops cleanly, cancels a pending flush, and ignores stale callbacks", async () => {
    const { startChangeStream, stopChangeStream } = await loadChangeStream();
    startChangeStream();
    const source = FakeEventSource.instances[0];
    source.onopen?.();
    source.onmessage?.();

    stopChangeStream();

    expect(source.closed).toBe(true);
    expect(statuses.at(-1)).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(250);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);

    source.onopen?.();
    source.onmessage?.();
    await vi.advanceTimersByTimeAsync(250);
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);
    expect(debug).toEqual({
      sseOpens: 1,
      sseReconciliations: 0,
      flushes: 1
    });
  });
});
