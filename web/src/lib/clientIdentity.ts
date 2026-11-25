import type { ClientIdentity } from '../../../server/client_versions/contracts';
import { REQUEST_LIMIT_MS } from './requestLimit/config';
import { signalWithLimit } from './requestLimit/limit';
import { get } from 'svelte/store';
import { noteUpdateRefusal, updatePause, updateRefusal, waitForUpdateWrites } from './updates/writePause';
import { isUpdateScreenReplacement, retainRefusedWrite, settleRefusedWrite } from './updates/handoff';

const DEVICE_KEY = 'panels.client-device-id';
let transientDeviceId: string | null = null;

export function randomClientId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, '0')).join('');
}

/** Storage can be disabled. Keep one identity for that tab when it is. */
function deviceId(): string {
  try {
    const stored = localStorage.getItem(DEVICE_KEY);
    if (stored) return stored;
    const id = transientDeviceId ??= randomClientId();
    localStorage.setItem(DEVICE_KEY, id);
    return id;
  } catch {
    return transientDeviceId ??= randomClientId();
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
export async function clientFetch(input: string, init: RequestInit = {}, limitMs = REQUEST_LIMIT_MS): Promise<Response> {
  const relative = /^\/(api|files)(\/|$)/.test(input);
  const sameServer = typeof location !== 'undefined' && (() => {
    try {
      const url = new URL(input, location.href);
      return url.origin === location.origin && /^\/(api|files)(\/|$)/.test(url.pathname);
    } catch { return false; }
  })();
  const local = relative || sameServer;
  return withUpdateWritePause(input, init, () => fetch(input, {
    ...init,
    signal: signalWithLimit(limitMs, init.signal),
    ...(local ? { headers: clientIdentityHeaders(init.headers) } : {})
  }));
}

/** Fetch and progress uploads share one admission boundary. Transport failures never retry. */
export async function withUpdateWritePause(input: string, init: RequestInit, dispatch: () => Promise<Response>): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  let path = input;
  let local = /^\/(api|files)(\/|$)/.test(input);
  if (!local && typeof location !== "undefined") {
    try {
      const url = new URL(input, location.href);
      path = url.pathname;
      local = url.origin === location.origin && /^\/(api|files)(\/|$)/.test(path);
    } catch { /* External requests do not wait on Panels admission. */ }
  }
  const updateWrite = local && !["GET", "HEAD", "OPTIONS"].includes(method)
    && !path.startsWith("/api/updates") && !path.startsWith("/api/executor/");
  let retained: string | null = null;
  try {
    for (;;) {
      if (updateWrite && (get(updatePause).blocked || isUpdateScreenReplacement())) {
        retained ??= await retainRefusedWrite(input, init);
        await waitForUpdateWrites(init.signal);
      }
      // Remove before dispatch: a network failure after this point has an uncertain outcome.
      if (retained !== null) { await settleRefusedWrite(retained); retained = null; }
      const response = await dispatch();
      const refusal = updateWrite ? await updateRefusal(response) : null;
      if (refusal === null) return response;
      noteUpdateRefusal(refusal);
      retained = await retainRefusedWrite(input, init);
      await waitForUpdateWrites(init.signal);
    }
  } catch (error) {
    if (retained !== null && !isUpdateScreenReplacement()) await settleRefusedWrite(retained);
    throw error;
  }
}
