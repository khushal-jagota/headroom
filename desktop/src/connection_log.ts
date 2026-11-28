// A small log of why this app lost its connection, sent to the server in the background.
//
// The page records what it saw. This side records what only the native side can see: the
// Chromium error of a failed relay (for example `ERR_NETWORK_CHANGED`) and the Mac's sleep,
// wake and screen lock. Events wait in a file in the app's data folder, so a quit or a crash
// does not lose them. The server takes the batch at `POST /api/client-events`.
import { app, netLog, powerMonitor, session } from 'electron';
import { mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { readJson, writeJson } from './stored_json';
import { serverAddress } from './server_address';
import { desktopIdentityHeaders } from './client_identity';

const EVENTS_FILE = 'connection-events.json';
const MAX_EVENTS = 200;
const MAX_BATCH = 50;
const SEND_DELAY_MS = 5_000;
const RETRY_MS = 30_000;
/** After a 429 the server asks for a minute. */
const TOO_MANY_BATCHES_MS = 60_000;
const SEND_LIMIT_MS = 10_000;

// The event shape is version 1 of `POST /api/client-events`, owned by the server's design for
// the stream-close record. The server names the client by the identity header.
type NativeEvent = {
  /** Epoch milliseconds. */
  at: number;
  kind: 'stream_ended' | 'power';
  stream?: string;
  reason?: string;
  power?: 'suspend' | 'resume' | 'lock' | 'unlock';
};

let queue: NativeEvent[] | null = null;
let sendTimer: NodeJS.Timeout | null = null;

function events(): NativeEvent[] {
  return queue ??= readJson<NativeEvent[]>(EVENTS_FILE) ?? [];
}

function record(event: Omit<NativeEvent, 'at'>): void {
  const list = events();
  list.push({ at: Date.now(), ...event });
  if (list.length > MAX_EVENTS) list.splice(0, list.length - MAX_EVENTS);
  writeJson(EVENTS_FILE, list);
  scheduleSend(SEND_DELAY_MS);
}

function scheduleSend(delay: number): void {
  if (sendTimer) return;
  sendTimer = setTimeout(() => { sendTimer = null; void send(); }, delay);
  sendTimer.unref();
}

/** Node's own fetch, as the notification stream uses, so a failed send is never a relay failure. */
async function send(): Promise<void> {
  const address = serverAddress();
  const batch = events().slice(0, MAX_BATCH);
  if (!address || batch.length === 0) return;
  let status: number;
  try {
    const response = await fetch(`${address}/api/client-events`, {
      method: 'POST',
      headers: { ...desktopIdentityHeaders(), 'content-type': 'application/json' },
      body: JSON.stringify({ version: 1, events: batch }),
      signal: AbortSignal.timeout(SEND_LIMIT_MS),
    });
    status = response.status;
  } catch {
    scheduleSend(RETRY_MS);
    return;
  }
  // Any 2xx means stored. A refused shape (400, 413, 422) can never succeed, so that batch is
  // dropped like a stored one. A 429 or 503 keeps the batch.
  const stored = status >= 200 && status < 300;
  if (!stored && ![400, 413, 422].includes(status)) { scheduleSend(status === 429 ? TOO_MANY_BATCHES_MS : RETRY_MS); return; }
  // Newer events may have joined the queue during the send. The sent ones are the oldest.
  queue = events().slice(batch.length);
  writeJson(EVENTS_FILE, queue);
  if (queue.length > 0) scheduleSend(SEND_DELAY_MS);
}

/** The live streams the app relays, named as the server names their routes. */
function streamOf(path: string): string | undefined {
  if (path === '/api/changes' || path === '/api/navigation/view/events') return path;
  const tail = /^\/api\/conversation\/conversations\/[^/]+(\/view)?\/tail$/.exec(path);
  if (tail) return `/api/conversation/conversations/:conversation_id${tail[1] ?? ''}/tail`;
  return undefined;
}

/** `net::ERR_NETWORK_CHANGED` becomes `ERR_NETWORK_CHANGED`. */
function chromiumCode(message: string): string {
  return /net::(ERR_[A-Z0-9_]+)/.exec(message)?.[1] ?? message.slice(0, 120);
}

/** `forward()` calls this when the server could not be reached at all. */
export function recordRelayFailure(path: string, error: unknown): void {
  const stream = streamOf(path);
  if (stream) record({ kind: 'stream_ended', stream, reason: chromiumCode(error instanceof Error ? error.message : String(error)) });
}

/**
 * Record what Chromium reports for a screen request that failed after the server answered,
 * which is how a live stream dies when a network change closes its HTTP/2 connection.
 * `forward()` never sees that error, because the response body belongs to Chromium's relay.
 * `ERR_ABORTED` is the page closing a stream itself. `ERR_FAILED` is `forward()` returning
 * its own network error, which `recordRelayFailure` already recorded with the real code.
 */
export function startRelayErrorLog(screensHost: string): void {
  session.defaultSession.webRequest.onErrorOccurred({ urls: [`panels://${screensHost}/*`] }, (details) => {
    const code = chromiumCode(details.error);
    if (code === 'ERR_ABORTED' || code === 'ERR_FAILED') return;
    const stream = streamOf(new URL(details.url).pathname);
    if (stream) record({ kind: 'stream_ended', stream, reason: code });
  });
}

export function startPowerLog(): void {
  powerMonitor.on('suspend', () => record({ kind: 'power', power: 'suspend' }));
  powerMonitor.on('resume', () => record({ kind: 'power', power: 'resume' }));
  powerMonitor.on('lock-screen', () => record({ kind: 'power', power: 'lock' }));
  powerMonitor.on('unlock-screen', () => record({ kind: 'power', power: 'unlock' }));
}

// --- Chromium net-log -----------------------------------------------------------------
// A switch for a day of detailed network recording, written to the app's logs folder.

const NET_LOG_STATE_FILE = 'net-log.json';
const NET_LOG_DURATION_MS = 24 * 60 * 60 * 1000;
const NET_LOG_MAX_BYTES = 100 * 1024 * 1024;
let netLogTimer: NodeJS.Timeout | null = null;

function netLogPath(): string {
  return join(app.getPath('logs'), 'net-log.json');
}

export function netLogIsOn(): boolean {
  return netLog.currentlyLogging;
}

/** Start logging until the stored end time. A restart inside that time carries on. */
export async function resumeNetLog(onEnd: () => void): Promise<void> {
  const until = readJson<{ until?: number }>(NET_LOG_STATE_FILE)?.until;
  if (typeof until === 'number' && until > Date.now()) await beginNetLog(until, onEnd);
}

async function beginNetLog(until: number, onEnd: () => void): Promise<void> {
  mkdirSync(app.getPath('logs'), { recursive: true });
  await netLog.startLogging(netLogPath(), { captureMode: 'default', maxFileSize: NET_LOG_MAX_BYTES });
  netLogTimer = setTimeout(() => { void endNetLog().then(onEnd); }, Math.max(0, until - Date.now()));
  netLogTimer.unref();
}

export async function startNetLogForADay(onEnd: () => void): Promise<void> {
  if (netLogIsOn()) return;
  const until = Date.now() + NET_LOG_DURATION_MS;
  writeJson(NET_LOG_STATE_FILE, { until });
  rmSync(netLogPath(), { force: true });
  await beginNetLog(until, onEnd);
}

export async function endNetLog(): Promise<void> {
  if (netLogTimer) clearTimeout(netLogTimer);
  netLogTimer = null;
  writeJson(NET_LOG_STATE_FILE, {});
  if (netLog.currentlyLogging) await netLog.stopLogging();
}
