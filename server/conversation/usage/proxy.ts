/** Linux urllib.request environment proxy selection, without any I/O. */
export function rates_proxy_for(
  target: string,
  environment: Readonly<Record<string, string | undefined>> = process.env,
): string | null {
  const proxies: Record<string, string> = {};
  for (const [name, value] of Object.entries(environment)) {
    if (name.toLowerCase().endsWith('_proxy') && value)
      proxies[name.slice(0, -6).toLowerCase()] = value;
  }
  // urllib ignores uppercase HTTP_PROXY in CGI environments.
  if (environment.REQUEST_METHOD !== undefined) delete proxies.http;
  for (const [name, value] of Object.entries(environment)) {
    if (!name.endsWith('_proxy')) continue;
    const key = name.slice(0, -6).toLowerCase();
    if (value) proxies[key] = value;
    else delete proxies[key];
  }
  const url = new URL(target);
  const host = url.host.toLowerCase();
  const hostname = url.hostname.toLowerCase();
  const bypass = proxies.no;
  if (bypass === '*') return null;
  if (bypass !== undefined) {
    for (const entry of bypass.split(',')) {
      const name = entry.trim().replace(/^\.+/, '').toLowerCase();
      if (name && (hostname === name || host === name || hostname.endsWith('.' + name) || host.endsWith('.' + name)))
        return null;
    }
  }
  return proxies[url.protocol.slice(0, -1)] ?? null;
}
