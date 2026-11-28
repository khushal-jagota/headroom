/** A minimal live-socket client for tests and the smoke run. It opens one subscription. */
import WebSocket from 'ws';
import type { LiveEnvelope, LiveSubscriptionKind } from '../contracts.ts';
import { LIVE_SOCKET_PATH } from '../contracts.ts';

export interface LiveSubscription {
  kind: LiveSubscriptionKind;
  conversation_id?: string;
  params?: Record<string, string>;
}

/**
 * Connects to `origin`, says hello, subscribes, and yields the frames of that subscription. It
 * ends when the subscription ends, the socket closes, or `signal` aborts.
 */
export async function* live_frames(
  origin: string,
  subscription: LiveSubscription,
  signal: AbortSignal,
): AsyncGenerator<LiveEnvelope> {
  const socket = new WebSocket(origin.replace(/^http/, 'ws') + LIVE_SOCKET_PATH);
  const queue: LiveEnvelope[] = [];
  let wake: (() => void) | null = null;
  let ended: Error | null = null;
  const finish = (error: Error | null) => {
    ended ??= error ?? new Error('live socket closed');
    wake?.();
  };
  socket.on('message', (data) => {
    queue.push(JSON.parse(String(data)) as LiveEnvelope);
    wake?.();
  });
  socket.on('close', () => finish(null));
  socket.on('error', (error) => finish(error));
  signal.addEventListener('abort', () => finish(new Error('aborted')), { once: true });
  socket.on('open', () => {
    socket.send(
      JSON.stringify({
        op: 'hello',
        client: { kind: 'browser', device_id: 'live-test-client', version: '1' },
      }),
    );
  });
  let subscribed = false;
  try {
    for (;;) {
      const frame = queue.shift();
      if (!frame) {
        if (ended) return;
        await new Promise<void>((resolve) => (wake = resolve));
        wake = null;
        continue;
      }
      if (frame.kind === 'ready' && !subscribed) {
        subscribed = true;
        socket.send(JSON.stringify({ op: 'subscribe', id: 'one', ...subscription }));
      } else if (frame.sub === 'one') {
        yield frame;
        if (frame.kind === 'ended') return;
      }
    }
  } finally {
    socket.terminate();
  }
}
