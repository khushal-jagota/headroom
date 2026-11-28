import { vi } from "vitest";
import type { LiveClientFrame } from "../../../server/live_updates/contracts";

/**
 * A stand-in for the browser's WebSocket that speaks the server's envelope. A test drives it:
 * `ready()` opens it and sends the ready frame, `serve` sends one frame with the next sequence
 * number, and `drop` closes it as the network would.
 */
export class FakeWebSocket {
  static readonly OPEN = 1;
  static all: FakeWebSocket[] = [];
  static get current(): FakeWebSocket { return FakeWebSocket.all.at(-1)!; }
  readyState = 0;
  closed = false;
  sent: LiveClientFrame[] = [];
  sequence = 0;
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) { FakeWebSocket.all.push(this); }
  send(text: string) { this.sent.push(JSON.parse(text)); }
  close() { this.readyState = 3; this.closed = true; }
  open() { this.readyState = 1; this.onopen?.(); }
  serve(sub: string | null, kind: string, data?: unknown) {
    this.onmessage?.({ data: JSON.stringify({ seq: ++this.sequence, sub, kind, data }) });
  }
  ready() { this.open(); this.serve(null, "ready", { heartbeat_ms: 15_000 }); }
  change() { this.serve(null, "change"); }
  heartbeat() { this.serve(null, "heartbeat", "now"); }
  drop(code = 1006) { this.readyState = 3; this.onclose?.({ code }); }
  /** The subscribe frames that this socket has received, in order. */
  subscribes() { return this.sent.filter((frame) => frame.op === "subscribe"); }
}

/** The browser globals that the socket owner reads, as a plain page and not the desktop app. */
export function stubPage(): void {
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("location", { href: "https://panels.test/board" });
  FakeWebSocket.all = [];
}

export function stubPageDocument(extra: object = {}) {
  const page = {
    visibilityState: "visible",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    querySelector: () => null,
    documentElement: { hasAttribute: () => false },
  };
  // Descriptors keep a getter live, which a spread would read once and freeze.
  return Object.defineProperties(page, Object.getOwnPropertyDescriptors(extra));
}
