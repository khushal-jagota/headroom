// Notifications through macOS.
//
// The native side holds one live stream to the server, `/api/notifications/desktop`, while
// the app runs, also with its window closed. Each event on it is one notification that the
// server's policy allowed. Nothing is replayed: what happens while the app is not
// connected reaches only the browser and iPhone.
import { Notification } from 'electron';
import { serverAddress } from './server_address';
import { desktopIdentityHeaders } from './client_identity';

type ShownNotification = { title: string; body: string; route: string; tag: string };

const RECONNECT_MS = 5_000;
// The server sends a keep-alive every 15 seconds. A stream silent for longer is dead, as a
// connection can be after the Mac sleeps, so it is dropped and opened again.
const SILENT_STREAM_MS = 45_000;

let connection: AbortController | null = null;
// A newer notification with the same tag replaces the older one. Holding each shown one
// here also keeps its click handler alive.
const shown = new Map<string, Notification>();

/** Hold the stream to the current server address. Call again after the address changes. */
export function holdNotificationStream(openRoute: (route: string) => void): void {
  connection?.abort();
  const address = serverAddress();
  connection = address ? new AbortController() : null;
  if (address && connection) void listen(address, connection.signal, openRoute);
}

async function listen(address: string, signal: AbortSignal, openRoute: (route: string) => void): Promise<void> {
  while (!signal.aborted) {
    const attempt = new AbortController();
    const stop = () => attempt.abort();
    signal.addEventListener('abort', stop);
    let silence = setTimeout(stop, SILENT_STREAM_MS);
    try {
      // Node's own fetch, not Electron's `net`. The screens' requests go through Chromium,
      // which holds at most six connections to one HTTP/1.1 server. A stream held there for
      // good would take one of them from the screens.
      const response = await fetch(`${address}/api/notifications/desktop`, {
        headers: { accept: 'text/event-stream', ...desktopIdentityHeaders() },
        signal: attempt.signal,
      });
      if (response.ok && response.body)
        await readEvents(
          response.body,
          () => {
            clearTimeout(silence);
            silence = setTimeout(stop, SILENT_STREAM_MS);
          },
          (event) => show(event, openRoute),
        );
    } catch {
      // The server is gone, the stream went silent, or the address changed. Try again below.
    } finally {
      clearTimeout(silence);
      signal.removeEventListener('abort', stop);
      attempt.abort();
    }
    if (!signal.aborted) await new Promise((done) => setTimeout(done, RECONNECT_MS));
  }
}

/** Read server-sent events. Each `data:` event is one notification as JSON. */
async function readEvents(
  body: ReadableStream<Uint8Array>,
  onBytes: () => void,
  onEvent: (event: ShownNotification) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffered = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    onBytes();
    buffered += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffered.indexOf('\n\n')) !== -1) {
      const block = buffered.slice(0, end);
      buffered = buffered.slice(end + 2);
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice('data:'.length).trimStart())
        .join('\n');
      const event = data ? parseNotification(data) : null;
      if (event) onEvent(event);
    }
  }
}

function parseNotification(data: string): ShownNotification | null {
  try {
    const value = JSON.parse(data) as Record<string, unknown>;
    const { title, body, route, tag } = value;
    if ([title, body, route, tag].every((field) => typeof field === 'string'))
      return { title, body, route, tag } as ShownNotification;
  } catch {
    // Not a notification. Skip it.
  }
  return null;
}

function show(event: ShownNotification, openRoute: (route: string) => void): void {
  shown.get(event.tag)?.close();
  const notification = new Notification({ title: event.title, body: event.body });
  shown.set(event.tag, notification);
  notification.on('click', () => openRoute(event.route));
  notification.on('close', () => {
    if (shown.get(event.tag) === notification) shown.delete(event.tag);
  });
  notification.show();
}
