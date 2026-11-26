// The app's own scheme. `panels://app` holds the screens, `panels://setup` the address page.
//
// The screens use relative paths, as they do when the server serves them. This handler
// serves the bundle that ships inside the app and forwards `/api` and `/files` to the one
// server address. The screens never need that address. Requests reach the server from the
// native side with no Origin header.
import { net, protocol, session } from 'electron';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, sep } from 'node:path';
import { serverAddress } from './server_address';
import { desktopIdentityHeaders } from './client_identity';
import type { ScreenPresentation } from './screens/controller';

// Node gives a URL of a custom scheme the origin "null", so pages are told apart by host.
export const SCREENS_HOST = 'app';
export const ADDRESS_PAGE_HOST = 'setup';
export const SCREEN_RECOVERY_HOST = 'recovery';
export const SCREEN_RECOVERY_URL = `panels://${SCREEN_RECOVERY_HOST}/`;
export const SCREENS_URL = `panels://${SCREENS_HOST}/`;
export const ADDRESS_PAGE_URL = `panels://${ADDRESS_PAGE_HOST}/`;

/** The packed bundle: `web/dist`, `assets` and `static`, and the address page. */
export const PACKED = join(__dirname, 'packed');
let presentation: ScreenPresentation = { root: PACKED, appSha: null, serverUpdate: false, screenRecovery: false };
export function selectScreenPresentation(value: ScreenPresentation): void { presentation = value; }

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

// Chromium adds these for the hop to the app scheme, and `net.fetch` sets its own.
const REQUEST_HEADERS_NOT_FORWARDED = ['host', 'origin', 'referer', 'connection', 'content-length'];
// `net.fetch` hands back the body already decoded, so the encoding and length no longer apply.
const RESPONSE_HEADERS_NOT_RETURNED = ['content-encoding', 'content-length'];

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
      if (isForwarded(url)) return forward(request, url);
      if (presentation.serverUpdate) return packedFile(url.pathname, serverUpdateFile);
      return packedFile(url.pathname, screensFile, presentation.root, presentation.appSha);
    }
    if (url.host === SCREEN_RECOVERY_HOST) return packedFile(url.pathname, screenRecoveryFile);
    if (url.host === ADDRESS_PAGE_HOST) return packedFile(url.pathname, screensFile);
    return notFound();
  });
  refuseWritesFromOtherSites();
}

function isForwarded(url: URL): boolean {
  return /^\/(api|files)(\/|$)/.test(url.pathname);
}

/**
 * Only the app's own pages may write through the forward.
 *
 * A page from another site can run inside the app, for example in an artifact frame. Its
 * requests to the app scheme carry no Origin header, so neither the forward nor the
 * server could tell them from the screens' own. Here Electron names the frame that sent
 * each request, so a write from any frame outside `panels://app` is cancelled.
 */
function refuseWritesFromOtherSites(): void {
  session.defaultSession.webRequest.onBeforeRequest({ urls: [`panels://${SCREENS_HOST}/*`] }, (details, callback) => {
    const write = details.method !== 'GET' && details.method !== 'HEAD';
    const fromTheApp = details.frame?.origin === `panels://${SCREENS_HOST}`;
    callback({ cancel: write && !fromTheApp && isForwarded(new URL(details.url)) });
  });
}

function screensFile(pathname: string): string | null {
  if (pathname === '/' || pathname === '/index.html') return 'web/dist/index.html';
  if (pathname.startsWith('/_app/')) return `web/dist/${pathname.slice('/_app/'.length)}`;
  if (pathname.startsWith('/assets/') || pathname.startsWith('/static/')) return pathname.slice(1);
  return null;
}

function screenRecoveryFile(pathname: string): string | null {
  if (pathname === '/' || pathname === '/index.html') return 'screen_recovery_page/index.html';
  if (pathname === '/recovery.js') return 'screen_recovery_page/recovery.js';
  if (pathname === '/recovery.css') return 'screen_recovery_page/recovery.css';
  if (pathname === '/assets/tokens.css' || pathname === '/assets/app.css'
    || pathname === '/assets/fonts/InterVariable.woff2') return pathname.slice(1);
  return null;
}

function serverUpdateFile(pathname: string): string | null {
  if (pathname === '/' || pathname === '/index.html') return 'server_update_page/index.html';
  if (pathname === '/server-update.js') return 'server_update_page/server-update.js';
  if (pathname === '/server-update.css') return 'server_update_page/server-update.css';
  if (pathname === '/assets/tokens.css' || pathname === '/assets/app.css'
    || pathname === '/assets/fonts/InterVariable.woff2' || pathname === '/static/icon-192.png') return pathname.slice(1);
  return null;
}

/** Serve one file of the packed bundle. A path that climbs, even encoded, is refused. */
async function packedFile(pathname: string, choose: (decoded: string) => string | null, root = PACKED, appSha: string | null = null): Promise<Response> {
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return notFound();
  }
  if (decoded.includes('\\') || decoded.split('/').includes('..')) return notFound();
  const relative = choose(decoded);
  if (relative === null) return notFound();
  const file = normalize(join(root, relative));
  if (!file.startsWith(root + sep)) return notFound();
  try {
    let body = await readFile(file);
    if (relative === 'web/dist/index.html' && appSha) {
      const escaped = appSha.replace(/[&<>"']/g, value => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[value]!);
      const address = (serverAddress() ?? "").replace(/[&<>"']/g, value => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[value]!);
      body = Buffer.from(body.toString('utf8').replace('</head>', `<meta name="panels-app-sha" content="${escaped}"><meta name="panels-server-address" content="${address}"></head>`));
    }
    return new Response(body, {
      headers: { 'content-type': MIME[extname(file)] ?? 'application/octet-stream', 'cache-control': 'no-store' },
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
export async function forward(request: Request, url: URL): Promise<Response> {
  const address = serverAddress();
  if (address === null) return Response.error();
  const headers = new Headers(request.headers);
  for (const name of REQUEST_HEADERS_NOT_FORWARDED) headers.delete(name);
  for (const [name, value] of Object.entries(desktopIdentityHeaders())) headers.set(name, value);
  // `net.fetch` follows redirects itself. The server's only redirects under `/api` add or
  // drop a trailing slash on the same server.
  const init: RequestInit & { duplex?: 'half' } = { method: request.method, headers, signal: request.signal, cache: 'no-store' };
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    init.body = request.body;
    init.duplex = 'half';
  }
  let response: Response;
  try {
    response = await net.fetch(`${address}${url.pathname}${url.search}`, init);
  } catch {
    return Response.error();
  }
  // Preserve Electron's fetch metadata and untouched body so its native relay owns
  // cancellation when the page closes a stream. A new Response loses that relay.
  for (const name of RESPONSE_HEADERS_NOT_RETURNED) response.headers.delete(name);
  return response;
}

function notFound(): Response {
  return new Response('Not Found', { status: 404, headers: { 'content-type': 'text/plain', 'cache-control': 'no-store' } });
}
