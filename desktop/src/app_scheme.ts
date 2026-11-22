// The app's own scheme. `panels://app` holds the screens, `panels://setup` the address page.
//
// The screens use relative paths, as they do when the server serves them. This handler
// serves the bundle that ships inside the app and forwards `/api` and `/files` to the one
// server address. The screens never need that address. Requests reach the server from the
// native side with no Origin header.
import { net, protocol } from 'electron';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, sep } from 'node:path';
import { serverAddress } from './server_address';

// Node gives a URL of a custom scheme the origin "null", so pages are told apart by host.
export const SCREENS_HOST = 'app';
export const ADDRESS_PAGE_HOST = 'setup';
export const SCREENS_URL = `panels://${SCREENS_HOST}/`;
export const ADDRESS_PAGE_URL = `panels://${ADDRESS_PAGE_HOST}/`;

/** The packed bundle: `web/dist`, `assets` and `static`, and the address page. */
const PACKED = join(__dirname, 'packed');

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/vnd.microsoft.icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.wasm': 'application/wasm',
  '.map': 'application/json',
};

// Chromium adds these for the hop to the app scheme. `net.fetch` sets its own, and it
// decodes compressed bodies, so the server must not compress for the screens.
const HEADERS_NOT_FORWARDED = ['host', 'origin', 'referer', 'connection', 'content-length', 'accept-encoding'];

/** Call before the app is ready. */
export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: 'panels',
      privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
    },
  ]);
}

/** Call once the app is ready. */
export function handleAppScheme(): void {
  protocol.handle('panels', (request) => {
    const url = new URL(request.url);
    if (url.host === SCREENS_HOST) {
      if (/^\/(api|files)(\/|$)/.test(url.pathname)) return forward(request, url);
      return packedFile(screensFile(url.pathname));
    }
    if (url.host === ADDRESS_PAGE_HOST) return packedFile(addressPageFile(url.pathname));
    return notFound();
  });
}

function screensFile(pathname: string): string | null {
  if (pathname === '/' || pathname === '/index.html') return 'web/dist/index.html';
  if (pathname.startsWith('/_app/')) return `web/dist/${pathname.slice('/_app/'.length)}`;
  if (pathname.startsWith('/assets/') || pathname.startsWith('/static/')) return pathname.slice(1);
  return null;
}

function addressPageFile(pathname: string): string | null {
  return `address_page/${pathname === '/' ? 'index.html' : pathname.slice(1)}`;
}

async function packedFile(relative: string | null): Promise<Response> {
  if (relative === null) return notFound();
  const file = normalize(join(PACKED, decodeURIComponent(relative)));
  if (!file.startsWith(PACKED + sep)) return notFound();
  try {
    const body = await readFile(file);
    return new Response(body, {
      headers: { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' },
    });
  } catch {
    return notFound();
  }
}

/**
 * Send one screen request to the server and hand back its response as it streams.
 *
 * If the server cannot be reached, answer with a network error, not an HTTP error. A live
 * stream (`EventSource`) closes for good on an HTTP error, but on a network error it
 * reconnects by itself when the server is back.
 */
async function forward(request: Request, url: URL): Promise<Response> {
  const address = serverAddress();
  if (address === null) return Response.error();
  const headers = new Headers(request.headers);
  for (const name of HEADERS_NOT_FORWARDED) headers.delete(name);
  const init: RequestInit & { duplex?: 'half' } = { method: request.method, headers };
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    init.body = request.body;
    init.duplex = 'half';
  }
  try {
    return await net.fetch(`${address}${url.pathname}${url.search}`, init);
  } catch {
    return Response.error();
  }
}

function notFound(): Response {
  return new Response('Not Found', { status: 404, headers: { 'content-type': 'text/plain' } });
}
