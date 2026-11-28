import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startLiveSocket, stopLiveSocket, subscribeLive } from "../src/lib/liveSocket";
import { FakeWebSocket, stubPage, stubPageDocument } from "./support/fakeLiveSocket";

// Distinct risks that no other test reaches: the socket owner's frame routing, its sequence
// check, what a subscriber is told on a drop or a refusal, and the desktop connection. The
// consumers are tested against a fake of this module, and the change stream against its states.
const handlers = () => ({ ready: vi.fn(), change: vi.fn(), lost: vi.fn(), updateNeeded: vi.fn() });
const subscriber = () => ({ frame: vi.fn(), lost: vi.fn() });

async function open(handler = handlers()) {
  startLiveSocket(handler);
  await vi.advanceTimersByTimeAsync(0);
  return { handler, socket: FakeWebSocket.current };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("window", globalThis);
  stubPage();
  vi.stubGlobal("document", stubPageDocument());
});
afterEach(() => {
  stopLiveSocket();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("live socket", () => {
  it("says hello first, subscribes after ready, and routes frames by subscription", async () => {
    const { handler, socket } = await open();
    const early = subscriber();
    subscribeLive({ kind: "conversation_events", conversationId: "c1", params: { after: "7" } }, early);
    socket.open();
    expect(socket.sent).toEqual([expect.objectContaining({ op: "hello", client: expect.objectContaining({ kind: "browser" }) })]);
    socket.serve(null, "ready", { heartbeat_ms: 15_000 });
    expect(handler.ready).toHaveBeenCalledTimes(1);
    expect(socket.subscribes()).toEqual([
      { op: "subscribe", id: expect.any(String), kind: "conversation_events", conversation_id: "c1", params: { after: "7" } },
    ]);
    const id = (socket.subscribes()[0] as { id: string }).id;
    const other = subscriber();
    const stopOther = subscribeLive({ kind: "navigation", params: { view: "{}" } }, other);
    socket.serve(id, "conversation-event", { sequence: 8 });
    socket.change();
    expect(early.frame).toHaveBeenCalledWith("conversation-event", { sequence: 8 });
    expect(other.frame).not.toHaveBeenCalled();
    expect(handler.change).toHaveBeenCalledTimes(1);
    stopOther();
    stopOther();
    expect(socket.sent.filter((frame) => frame.op === "unsubscribe")).toHaveLength(1);
  });

  it("ends one subscription with the server's status and leaves the socket open", async () => {
    const { handler, socket } = await open();
    socket.ready();
    const refused = subscriber();
    const kept = subscriber();
    subscribeLive({ kind: "conversation_view", conversationId: "c" }, refused);
    subscribeLive({ kind: "navigation" }, kept);
    const [first, second] = socket.subscribes() as Array<{ id: string }>;
    socket.serve(first.id, "ended", { status: 409, detail: "Conversation view is upgrading" });
    expect(refused.lost).toHaveBeenCalledWith({ status: 409, detail: "Conversation view is upgrading" });
    socket.serve(second.id, "snapshot", { n: 1 });
    expect(kept.frame).toHaveBeenCalledWith("snapshot", { n: 1 });
    expect(handler.lost).not.toHaveBeenCalled();
    expect(socket.closed).toBe(false);
  });

  it("tells every subscriber on a drop, reopens, and serves a new subscription on the new socket", async () => {
    const { handler, socket } = await open();
    socket.ready();
    const one = subscriber();
    const two = subscriber();
    subscribeLive({ kind: "navigation" }, one);
    subscribeLive({ kind: "navigation" }, two);
    socket.drop(4002);
    expect(one.lost).toHaveBeenCalledWith(null);
    expect(two.lost).toHaveBeenCalledWith(null);
    expect(handler.lost).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    const next = FakeWebSocket.current;
    expect(next).not.toBe(socket);
    next.ready();
    const again = subscriber();
    subscribeLive({ kind: "navigation" }, again);
    expect(next.subscribes()).toHaveLength(1);
    expect(handler.ready).toHaveBeenCalledTimes(2);
  });

  it("treats a skipped sequence number as a drop", async () => {
    const { handler, socket } = await open();
    socket.ready();
    const watcher = subscriber();
    subscribeLive({ kind: "navigation" }, watcher);
    socket.sequence += 1;
    socket.change();
    expect(socket.closed).toBe(true);
    expect(watcher.lost).toHaveBeenCalledWith(null);
    expect(handler.change).not.toHaveBeenCalled();
    expect(handler.lost).toHaveBeenCalledTimes(1);
  });

  it("opens the desktop socket at the address and with the identity that the bridge gives", async () => {
    const identity = { kind: "desktop", device_id: "d1", version: "1.2.3", bridge_version: 3 };
    vi.stubGlobal("panels", { liveConnection: async () => ({ address: "https://panels-vps.example.ts.net", identity }) });
    vi.stubGlobal("location", { href: "panels://app/index.html" });
    const { socket } = await open();
    expect(socket.url).toBe("wss://panels-vps.example.ts.net/api/live");
    socket.open();
    expect(socket.sent[0]).toEqual({ op: "hello", client: identity });
  });
});
