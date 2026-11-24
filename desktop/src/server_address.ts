// The one server address the app holds, and the check that a Panels server answers there.
import { net } from 'electron';
import { desktopIdentityHeaders } from './client_identity';
import { readJson, writeJson } from './stored_json';
import type { ServerAddressResult } from './bridge';

/** A Panels server on this Mac listens here unless its owner chose another port. */
const DEFAULT_LOCAL_SERVER_ADDRESS = 'http://127.0.0.1:8767';
const CHECK_TIMEOUT_MS = 5_000;

type StoredServer = { address: string };

let current: string | null = null;

/** The address that the forward sends requests to, or null before the first one. */
export function serverAddress(): string | null {
  return current;
}

export function loadStoredServerAddress(): string | null {
  current = readJson<StoredServer>('server.json')?.address ?? null;
  return current;
}

export function storeServerAddress(address: string): void {
  writeJson('server.json', { address } satisfies StoredServer);
  current = address;
}

/** The default address setup offers after the person selects this Mac. */
export function localServerAddress(): string {
  return process.env.PANELS_LOCAL_SERVER_ADDRESS ?? DEFAULT_LOCAL_SERVER_ADDRESS;
}

/** Normalize what a person typed to a scheme and host. A bare host means HTTPS, as a Tailscale address is. */
function normalize(typed: string): string | null {
  const text = typed.trim();
  if (!text) return null;
  try {
    const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`);
    const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) return null;
    if (url.username || url.password || url.pathname !== '/' || url.search || url.hash) return null;
    return url.origin;
  } catch {
    return null;
  }
}

/** Ask the address for `/api/meta`. A Panels server answers with its release in `app_sha`. */
async function checkServer(address: string): Promise<ServerAddressResult> {
  try {
    const response = await net.fetch(`${address}/api/meta`, {
      signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
      headers: desktopIdentityHeaders(),
    });
    if (!response.ok) return { ok: false, problem: `The address answered ${response.status}.` };
    const body: unknown = await response.json().catch(() => null);
    if (typeof body !== 'object' || body === null || !('app_sha' in body))
      return { ok: false, problem: 'The address answered, but not as a Panels server.' };
    return { ok: true, address };
  } catch (error) {
    return { ok: false, problem: `No Panels server answered: ${(error as Error).message}` };
  }
}

/** Check that a Panels server answers at a typed address. The caller stores it. */
export async function checkServerAddress(typed: string): Promise<ServerAddressResult> {
  const address = normalize(typed);
  if (!address) return { ok: false, problem: 'Enter an address such as https://panels.example.ts.net.' };
  return checkServer(address);
}
