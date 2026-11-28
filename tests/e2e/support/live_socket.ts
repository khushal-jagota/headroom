import type { BrowserContext, Page } from '@playwright/test';

/**
 * While `refusing()` is true, answer each subscribe frame of one kind with a refusal, as a
 * failed request used to fail the old stream. The server never hears the subscribe. The frames that
 * follow are renumbered, so the page still sees one whole sequence.
 */
export async function refuseLiveSubscriptions(
  target: Page | BrowserContext,
  kind: 'navigation' | 'conversation_view' | 'conversation_events',
  refusing: () => boolean,
): Promise<void> {
  await target.routeWebSocket(/\/api\/live$/, (socket) => {
    const server = socket.connectToServer();
    let injected = 0;
    let last = 0;
    server.onMessage((message) => {
      const frame = JSON.parse(String(message));
      frame.seq += injected;
      last = frame.seq;
      socket.send(JSON.stringify(frame));
    });
    socket.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (refusing() && frame.op === 'subscribe' && frame.kind === kind) {
        injected += 1;
        socket.send(JSON.stringify({ seq: ++last, sub: frame.id, kind: 'ended', data: { status: 503, detail: 'Refused by the test.' } }));
        return;
      }
      server.send(message);
    });
  });
}
