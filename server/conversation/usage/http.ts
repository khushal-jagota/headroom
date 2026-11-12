/** The sole production rates door. Tests supply a recording fetcher. */
import { backend_io_is_test } from '../snapshot/environment.ts';
import { LITELLM_RATES_URL, RATES_TIMEOUT_MS } from './configuration.ts';
import { request as http_request } from 'node:http';
import { request as https_request } from 'node:https';
import { HttpsProxyAgent } from 'https-proxy-agent';
import { rates_proxy_for } from './proxy.ts';
export { rates_proxy_for } from './proxy.ts';

/** urlopen follows GET redirects and consults the proxy environment for each URL. */
async function proxied_rates(url: string, signal: AbortSignal, remaining = 10): Promise<unknown> {
  const target = new URL(url);
  const selected = rates_proxy_for(url);
  if (selected === null) {
    const response = await fetch(url, { signal, redirect: 'manual' });
    const location = response.headers.get('location');
    if ([301, 302, 303, 307, 308].includes(response.status) && location !== null) {
      await response.body?.cancel();
      if (remaining === 0) throw new Error('The usage rates source redirected too many times.');
      return await proxied_rates(new URL(location, target).href, signal, remaining - 1);
    }
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error('The usage rates source did not answer successfully.');
    }
    return response.json();
  }
  const proxy = new URL(selected.includes('://') ? selected : 'http://' + selected);
  const tunnel = target.protocol === 'https:' ? new HttpsProxyAgent(proxy) : null;
  try {
    const result = await new Promise<{ status: number; location?: string; body: string }>((resolve, reject) => {
      const endpoint = new URL((tunnel === null ? proxy : target).href);
      // Proxy credentials belong only in Proxy-Authorization, never origin auth.
      if (tunnel === null) { endpoint.username = ''; endpoint.password = ''; }
      const request = (endpoint.protocol === 'https:' ? https_request : http_request)(endpoint, {
        method: 'GET', signal,
        ...(tunnel === null ? {
          path: target.href,
          headers: { host: target.host,
            ...(proxy.username || proxy.password ? { 'proxy-authorization': 'Basic ' + Buffer.from(decodeURIComponent(proxy.username) + ':' + decodeURIComponent(proxy.password)).toString('base64') } : {}),
          },
        } : { agent: tunnel }),
      }, (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => chunks.push(chunk));
        response.on('error', reject);
        response.on('end', () => resolve({ status: response.statusCode ?? 0,
          location: response.headers.location, body: Buffer.concat(chunks).toString('utf8') }));
      });
      request.on('error', reject);
      request.end();
    });
    if ([301, 302, 303, 307, 308].includes(result.status) && result.location !== undefined) {
      if (remaining === 0) throw new Error('The usage rates source redirected too many times.');
      return await proxied_rates(new URL(result.location, target).href, signal, remaining - 1);
    }
    if (result.status < 200 || result.status >= 300)
      throw new Error('The usage rates source did not answer successfully.');
    return JSON.parse(result.body) as unknown;
  } finally {
    tunnel?.destroy();
  }
}
export async function fetchRates(options: { signal?: AbortSignal } = {}): Promise<unknown> {
  if (backend_io_is_test())
    throw new Error(
      'Production usage rates I/O is unavailable in tests. Inject a recording fetcher.',
    );
  const timeout = AbortSignal.timeout(RATES_TIMEOUT_MS);
  const signal =
    options.signal === undefined ? timeout : AbortSignal.any([options.signal, timeout]);
  return await proxied_rates(LITELLM_RATES_URL, signal);
}
