// A small log of why this app lost its connection, sent to the server in the background.
//
// The page records what it saw. This side records what only the native side can see: the
// Chromium error of a failed relay (for example `ERR_NETWORK_CHANGED`) and the Mac's sleep,
// wake and screen lock. Events wait in a file in the app's data folder, so a quit or a crash
// does not lose them. The server takes the batch at `POST /api/client-events`.
import { app, netLog, powerMonitor, session } from 'electron';
import { randomUUID } from 'node:crypto';
import { mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { readJson, writeJson } from './stored_json';
import { serverAddress } from './server_address';
import { desktopIdentityHeaders } from './client_identity';

const EVENTS_FILE = 'connection-events.json';
const MAX_EVENTS = 200;
const MAX_BATCH = 100;
const SEND_DELAY_MS = 5_000;
const RETRY_MS = 30_000;
const SEND_LIMIT_MS = 10_000;

type Stream = 'changes' | 'navigation_events' | 'conversation_tail';
type NativeEvent = {
  id: string;
  at: string;
  source: 'desktop_native';
  kind: 'relay_failed' | 'power';
  stream?: Stream;
  error_code?: string;
  route?: string;
  power?: 'suspend' | 'resume' | 'lock' | 'unlock';
};

let queue: NativeEvent[] | null = null;
let sendTimer: NodeJS.Timeout | null = null;

function events(): NativeEvent[] {
  return queue ??= readJson<NativeEvent[]>(EVENTS_FILE) ?? [];
}

function record(event: Omit<NativeEvent, 'id' | 'at' | 'source'>): void {
  const list = events();
  list.push({ id: randomUUID(), at: new Date().toISOString(), source: 'desktop_native', ...event });
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
  // A 400 can never succeed, so that batch is dropped like a stored one.
  if (status !== 204 && status !== 400) { scheduleSend(RETRY_MS); return; }
  const sent = new Set(batch.map(event => event.id));
  queue = events().filter(event => !sent.has(event.id));
  writeJson(EVENTS_FILE, queue);
  if (queue.length > 0) scheduleSend(SEND_DELAY_MS);
}

function streamOf(path: string): Stream | undefined {
  if (path === '/api/changes') return 'changes';
  if (path === '/api/navigation/view/events') return 'navigation_events';
  if (/^\/api\/conversation\/conversations\/[^/]+\/tail$/.test(path)) return 'conversation_tail';
  return undefined;
}

/** `net::ERR_NETWORK_CHANGED` becomes `ERR_NETWORK_CHANGED`. */
function chromiumCode(message: string): string {
  return /net::(ERR_[A-Z0-9_]+)/.exec(message)?.[1] ?? message.slice(0, 120);
}

/** `forward()` calls this when the server could not be reached at all. */
export function recordRelayFailure(path: string, error: unknown): void {
  record({ kind: 'relay_failed', route: path, stream: streamOf(path),
    error_code: chromiumCode(error instanceof Error ? error.message : String(error)) });
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
    const url = new URL(details.url);
    if (!/^\/(api|files)(\/|$)/.test(url.pathname)) return;
    record({ kind: 'relay_failed', route: url.pathname, stream: streamOf(url.pathname), error_code: code });
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
