/** Pure HTTP policy for prefix-aware trusted applications. */
import { HEADER_BYTES } from './config.ts';
import { PreviewError, type Header } from './contracts.ts';
const HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);
export function header_size(headers: Header[]): void {
  if (
    headers.reduce((n, [k, v]) => n + Buffer.byteLength(k) + Buffer.byteLength(v) + 4, 0) >
    HEADER_BYTES
  )
    throw new PreviewError('Preview headers exceed the supported limit.', 431);
}
export function within_prefix(path: string, prefix: string): boolean {
  let decoded: string;
  try {
    decoded = decodeURIComponent(path);
  } catch {
    decoded = path.replace(/%([0-9a-f]{2})/gi, (_m, h: string) =>
      String.fromCharCode(parseInt(h, 16)),
    );
  }
  return (
    decoded.startsWith(prefix) &&
    !decoded.includes('\\') &&
    !decoded.split('/').some((p) => p === '.' || p === '..')
  );
}
function connection_tokens(headers: Header[]): Set<string> {
  return new Set(
    headers
      .filter(([k]) => k.toLowerCase() === 'connection')
      .flatMap(([, v]) => v.split(',').map((s) => s.trim().toLowerCase())),
  );
}
interface Cookie {
  name: string;
  coded_value: string;
  attributes: Map<string, string>;
}
/** Preserve quoted cookie values, as SimpleCookie does. */
function unquote(value: string): string {
  if (!value.startsWith('"') || !value.endsWith('"')) return value;
  return value
    .slice(1, -1)
    .replace(/\\([0-3][0-7]{2}|.)/gs, (_match, escaped: string) =>
      /^[0-3][0-7]{2}$/.test(escaped) ? String.fromCharCode(parseInt(escaped, 8)) : escaped,
    );
}
function cookies(raw: string): Cookie[] {
  const pattern =
    /\s*([\w!#%&'~_`><@,:/$*+\-.^|)(?}{=]+?)(?:\s*=\s*("(?:[^\\"]|\\.)*"|(?:\w{3,6}day|\w{3}),\s[\w\d\s-]{9,11}\s[\d:]{8}\sGMT|[\w!#%&'~_`><@,:/$*+\-.^|)(?}{=\[\]]*))?\s*(?:\s+|;|$)/gy;
  const attrs = new Set([
    'expires',
    'path',
    'comment',
    'domain',
    'max-age',
    'secure',
    'httponly',
    'version',
    'samesite',
    'partitioned',
  ]);
  const flags = new Set(['secure', 'httponly', 'partitioned']);
  const result = new Map<string, Cookie>();
  let current: Cookie | undefined;
  let position = 0;
  while (position < raw.length) {
    pattern.lastIndex = position;
    const match = pattern.exec(raw);
    if (!match) break;
    position = pattern.lastIndex;
    const name = match[1]!;
    const value = match[2];
    if (name.startsWith('$')) {
      if (!current) continue;
      const attribute = name.slice(1).toLowerCase();
      if (!attrs.has(attribute)) return [];
      current.attributes.set(attribute, value ?? '');
    } else if (attrs.has(name.toLowerCase())) {
      if (!current || (value === undefined && !flags.has(name.toLowerCase()))) return [];
      current.attributes.set(name.toLowerCase(), value === undefined ? 'True' : unquote(value));
    } else if (value !== undefined) {
      if (!/^[-!#$%&'*+.\^_`|~0-9A-Za-z:]+$/.test(name)) return [];
      current = result.get(name) ?? { name, coded_value: value, attributes: new Map() };
      current.coded_value = value;
      result.set(name, current);
    } else return [];
  }
  return [...result.values()];
}

export function request_headers(
  headers: Header[],
  options: { port: number; origin: string; cookie_names: Set<string> },
): Header[] {
  header_size(headers);
  const connection = connection_tokens(headers);
  const clean: Header[] = [];
  for (const [key, raw] of headers) {
    const name = key.toLowerCase();
    if (
      HOP.has(name) ||
      connection.has(name) ||
      ['host', 'forwarded', 'origin'].includes(name) ||
      ['tailscale-', 'x-plan-', 'x-forwarded-', 'proxy-'].some((p) => name.startsWith(p))
    )
      continue;
    let value = raw;
    if (name === 'cookie') {
      value = cookies(value)
        .filter((c) => options.cookie_names.has(c.name))
        .map((c) => `${c.name}=${c.coded_value}`)
        .join('; ');
      if (!value) continue;
    }
    clean.push([key, value]);
  }
  clean.push(
    ['Host', `127.0.0.1:${options.port}`],
    ['Origin', options.origin],
    ['X-Forwarded-Host', new URL(options.origin).host],
    ['X-Forwarded-Proto', 'https'],
  );
  return clean;
}
export function response_headers(
  headers: Header[],
  options: { request_url: string; origin: string; prefix: string },
): [Header[], Set<string>] {
  header_size(headers);
  const connection = connection_tokens(headers);
  const clean: Header[] = [];
  const names = new Set<string>();
  for (const [key, value] of headers) {
    const name = key.toLowerCase();
    if (HOP.has(name) || connection.has(name) || name === 'service-worker-allowed') continue;
    if (name === 'location') {
      // Preserve urllib's exact netloc policy before WHATWG case/port normalization.
      const location = value.replace(/[\t\r\n]/g, '').replace(/^[\u0000-\u0020]+/, '');
      const authority = /^(?:([a-z][a-z\d+.-]*):)?\/\/([^/?#]*)([^?#]*)/i.exec(location);
      const canonical = /^([a-z][a-z\d+.-]*):\/\/([^/?#]*)/i.exec(options.origin)!;
      const raw_path = authority ? authority[3]! : location.split(/[?#]/)[0]!;
      const unsafe =
        raw_path.includes('\\') ||
        raw_path
          .split('/')
          .some((part) => part.includes('%') && ['.', '..'].includes(part.replace(/%2e/gi, '.')));
      let resolved: URL;
      try {
        resolved = new URL(location, options.request_url);
      } catch {
        throw new PreviewError('Preview redirect must stay under its prepared base path.', 502);
      }
      const exact_authority =
        !authority ||
        ((authority[1]?.toLowerCase() ?? canonical[1]!.toLowerCase()) ===
          canonical[1]!.toLowerCase() &&
          authority[2] === canonical[2]);
      if (
        !exact_authority ||
        resolved.origin !== new URL(options.origin).origin ||
        !within_prefix(resolved.pathname, options.prefix) ||
        unsafe
      )
        throw new PreviewError('Preview redirect must stay under its prepared base path.', 502);
    }
    if (name === 'set-cookie') {
      const parsed = cookies(value);
      if (!parsed.length) throw new PreviewError('Preview returned an incompatible cookie.', 502);
      for (const cookie of parsed) {
        if (
          cookie.name.startsWith('__Host-') ||
          cookie.attributes.get('domain') ||
          !within_prefix(cookie.attributes.get('path') ?? '', options.prefix)
        )
          throw new PreviewError(
            'Preview cookies require an explicit generation Path and no Domain.',
            502,
          );
        names.add(cookie.name);
      }
    }
    clean.push([key, value]);
  }
  return [clean, names];
}
