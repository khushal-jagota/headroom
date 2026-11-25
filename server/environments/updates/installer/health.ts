import * as http from 'node:http';
import * as https from 'node:https';
import { setTimeout as delay } from 'node:timers/promises';
import { is_object } from '../codec.ts';
import { parse_numeric_json } from '../../../core/http/numeric_json.ts';
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
  identity: string | undefined,
  failed: (error: unknown) => void,
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
        {
          agent: false,
          headers: {
            Connection: 'close',
            ...(identity === undefined ? {} : { 'x-plan-actor': identity }),
          },
        },
        (response) => {
          const chunks: Buffer[] = [];
          response.on('data', (chunk: Buffer) => chunks.push(chunk));
          response.on('error', (error) => {
            failed(error);
            resolve(null);
          });
          response.once('aborted', () => {
            failed(new Error('health response aborted'));
            resolve(null);
          });
          response.once('end', () => {
            try {
              const status = response.statusCode ?? 0;
              const location = response.headers.location ?? response.headers.uri;
              resolve({
                status,
                body: REDIRECT_STATUSES.includes(status)
                  ? null
                  : parse_numeric_json(Buffer.concat(chunks).toString('utf8')),
                location: typeof location === 'string' ? location : undefined,
              });
            } catch (error) {
              failed(error);
              resolve(null);
            }
          });
        },
      );
      request.once('close', () => {
        release();
        resolve(null);
      });
      request.on('error', (error) => {
        failed(error);
        resolve(null);
      });
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
  identity: string | undefined,
  failed: (error: unknown) => void,
): Promise<HealthResponse | null> {
  const visited = new Map<string, number>();
  for (;;) {
    assert_installer_health_url(url, environment);
    const result = await request_observation(url, signal, identity, failed);
    if (
      result === null ||
      !REDIRECT_STATUSES.includes(result.status) ||
      result.location === undefined
    )
      return result;
    const next = new URL(result.location, url).href;
    const repeats = visited.get(next) ?? 0;
    if (repeats >= HEALTH_MAX_REDIRECT_REPEATS || visited.size >= HEALTH_MAX_REDIRECT_URLS) {
      failed(new Error(`health redirects exceeded the limit at ${next}`));
      return null;
    }
    visited.set(next, repeats + 1);
    url = next;
  }
}
/** A zero timeout requests one bounded observation. Positive timeouts bound the whole trial. */
export async function healthy(
  health_url: string,
  sha: string,
  timeout_seconds: number,
  signal: AbortSignal,
  environment: NodeJS.ProcessEnv = process.env,
  require_ready = false,
  identity?: string,
  prepared_attempt_id?: string,
): Promise<boolean> {
  const timeout = AbortSignal.timeout(
    Math.ceil((timeout_seconds > 0 ? timeout_seconds : HEALTH_INACTIVITY_SECONDS) * 1000),
  );
  const lifetime = AbortSignal.any([signal, timeout]);
  // Failed polls are expected while the server restarts, so only the last failure is logged at the deadline.
  let last_failure: unknown;
  const failed = (error: unknown) => {
    last_failure = error;
  };
  for (;;) {
    signal.throwIfAborted();
    let result: Awaited<ReturnType<typeof observation>> = null;
    try {
      result = await observation(
        `${health_url}?expected_sha=${sha}`,
        lifetime,
        environment,
        identity,
        failed,
      );
    } catch (error) {
      signal.throwIfAborted();
      failed(error);
    }
    if (
      result?.status === 200 &&
      is_object(result.body) &&
      result.body.app_sha === sha &&
      (prepared_attempt_id === undefined ||
        result.body.prepared_attempt_id === prepared_attempt_id) &&
      (!require_ready || result.body.ready === true)
    )
      return true;
    if (timeout.aborted || timeout_seconds <= 0) {
      console.warn('installer health check did not pass before its deadline', {
        health_url,
        sha,
        last_status: result?.status,
        last_failure,
      });
      return false;
    }
    try {
      await delay(HEALTH_POLL_SECONDS * 1000, undefined, { signal: lifetime });
    } catch (error) {
      signal.throwIfAborted();
      if (!timeout.aborted) throw error;
    }
  }
}
