import * as http from 'node:http';
import * as https from 'node:https';
import { setTimeout as delay } from 'node:timers/promises';
import { is_object, parse_attempt_json } from '../codec.ts';
import {
  assert_installer_health_url,
  HEALTH_INACTIVITY_SECONDS,
  HEALTH_POLL_SECONDS,
  HEALTH_MAX_REDIRECT_REPEATS,
  HEALTH_MAX_REDIRECT_URLS,
} from './configuration.ts';

interface HealthResponse {
  status: number;
  body: unknown;
  location: string | undefined;
}
const REDIRECT_STATUSES = [301, 302, 303, 307, 308];

async function request_observation(
  url: string,
  signal: AbortSignal,
): Promise<HealthResponse | null> {
  signal.throwIfAborted();
  const target = new URL(url);
  let request: http.ClientRequest | undefined, release!: () => void;
  const closed = new Promise<void>((resolve) => {
    release = resolve;
  });
  const abort = () =>
    request?.destroy(
      signal.reason instanceof Error ? signal.reason : new Error('Installer interrupted'),
    );
  let connecting: ReturnType<typeof setTimeout> | undefined;
  signal.addEventListener('abort', abort);
  try {
    const result = new Promise<HealthResponse | null>((resolve) => {
      request = (target.protocol === 'https:' ? https : http).get(
        target,
        { agent: false, headers: { Connection: 'close' } },
        (response) => {
          const chunks: Buffer[] = [];
          response.on('data', (chunk: Buffer) => chunks.push(chunk));
          response.on('error', () => resolve(null));
          response.once('aborted', () => resolve(null));
          response.once('end', () => {
            try {
              const status = response.statusCode ?? 0;
              const location = response.headers.location ?? response.headers.uri;
              resolve({
                status,
                body: REDIRECT_STATUSES.includes(status)
                  ? null
                  : parse_attempt_json(Buffer.concat(chunks).toString('utf8')),
                location: typeof location === 'string' ? location : undefined,
              });
            } catch {
              resolve(null);
            }
          });
        },
      );
      request.once('close', () => {
        release();
        resolve(null);
      });
      request.on('error', () => resolve(null));
      request.setTimeout(HEALTH_INACTIVITY_SECONDS * 1000, () =>
        request?.destroy(new Error('health read timed out')),
      );
      connecting = setTimeout(
        () => request?.destroy(new Error('health connection timed out')),
        HEALTH_INACTIVITY_SECONDS * 1000,
      );
      request.once('socket', (socket) => {
        const connected = () => clearTimeout(connecting);
        if (!socket.connecting) connected();
        else socket.once(target.protocol === 'https:' ? 'secureConnect' : 'connect', connected);
      });
      if (signal.aborted) abort();
    });
    const value = await result;
    signal.throwIfAborted();
    return value;
  } finally {
    clearTimeout(connecting);
    if (request) {
      request.destroy();
      await closed;
    }
    signal.removeEventListener('abort', abort);
  }
}
async function observation(
  url: string,
  signal: AbortSignal,
  environment: NodeJS.ProcessEnv,
): Promise<HealthResponse | null> {
  const visited = new Map<string, number>();
  for (;;) {
    assert_installer_health_url(url, environment);
    const result = await request_observation(url, signal);
    if (
      result === null ||
      !REDIRECT_STATUSES.includes(result.status) ||
      result.location === undefined
    )
      return result;
    const next = new URL(result.location, url).href;
    const repeats = visited.get(next) ?? 0;
    if (repeats >= HEALTH_MAX_REDIRECT_REPEATS || visited.size >= HEALTH_MAX_REDIRECT_URLS)
      return null;
    visited.set(next, repeats + 1);
    url = next;
  }
}
/** Python checks its overall anchor only after a failed/nonmatching observation. */
export async function healthy(
  health_url: string,
  sha: string,
  timeout_seconds: number,
  signal: AbortSignal,
  environment: NodeJS.ProcessEnv = process.env,
): Promise<boolean> {
  const deadline = performance.now() / 1000 + timeout_seconds;
  for (;;) {
    signal.throwIfAborted();
    let result: Awaited<ReturnType<typeof observation>> = null;
    try {
      result = await observation(`${health_url}?expected_sha=${sha}`, signal, environment);
    } catch {
      signal.throwIfAborted();
    }
    if (result?.status === 200 && is_object(result.body) && result.body.app_sha === sha)
      return true;
    if (performance.now() / 1000 >= deadline) return false;
    await delay(HEALTH_POLL_SECONDS * 1000, undefined, { signal });
  }
}
