import type { ClientIdentity } from '../../../server/client_versions/contracts';
import { REQUEST_LIMIT_MS } from './requestLimit/config';
import { signalWithLimit } from './requestLimit/limit';

const DEVICE_KEY = 'panels.client-device-id';
let transientDeviceId: string | null = null;

function newDeviceId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, '0')).join('');
}

/** Storage can be disabled. Keep one identity for that tab when it is. */
function deviceId(): string {
  try {
    const stored = localStorage.getItem(DEVICE_KEY);
    if (stored) return stored;
    const id = transientDeviceId ??= newDeviceId();
    localStorage.setItem(DEVICE_KEY, id);
    return id;
  } catch {
    return transientDeviceId ??= newDeviceId();
  }
}

/** These facts describe the loaded screens, not a later server release. */
export function clientIdentityHeaders(headers?: HeadersInit): Record<string, string> {
  const result: Record<string, string> = headers instanceof Headers
    ? Object.fromEntries(headers.entries())
    : Array.isArray(headers) ? Object.fromEntries(headers) : { ...headers };
  const version = typeof document === 'undefined'
    ? 'development'
    : document.querySelector<HTMLMetaElement>('meta[name="panels-app-sha"]')?.content.trim() || 'development';
  const identity: ClientIdentity = { kind: 'browser', device_id: deviceId(), version };
  for (const name of Object.keys(result)) if (name.toLowerCase() === 'x-panels-client') delete result[name];
  result['X-Panels-Client'] = JSON.stringify(identity);
  return result;
}

/**
 * Explicit Panels fetch boundary. External file links receive no device identity.
 *
 * Every request has a time limit. A request that names no limit gets the ordinary one.
 * At the limit the request ends with a `RequestLimitError`.
 */
export function clientFetch(input: string, init: RequestInit = {}, limitMs = REQUEST_LIMIT_MS): Promise<Response> {
  init = { ...init, signal: signalWithLimit(limitMs, init.signal) };
  const relative = /^\/(api|files)(\/|$)/.test(input);
  const sameServer = typeof location !== 'undefined' && (() => {
    try {
      const url = new URL(input, location.href);
      return url.origin === location.origin && /^\/(api|files)(\/|$)/.test(url.pathname);
    } catch { return false; }
  })();
  return fetch(input, relative || sameServer ? { ...init, headers: clientIdentityHeaders(init.headers) } : init);
}
