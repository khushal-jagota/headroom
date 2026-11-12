/** Bounded public request metadata and the existing canonical snapshot decoder. */
import { isIP } from 'node:net';
import { invalid, text } from '../../logic/validation.ts';
import { numeric_kind, numeric_value, stringify_python_json } from '../../../core/http/numeric_json.ts';
import { conversation_event_payload_from_canonical_json } from '../../../conversation/record/codec.ts';
import { CredentialRequestEventPayload } from '../../../conversation/events.ts';
import type { CreateCredentialRequestBody, CredentialRequestRowBody, RequestReplacement } from '../contracts.ts';
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
function exact_keys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).sort().join(',') === [...keys].sort().join(',');
}
export function strip(value: string): string {
  return value.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '');
}
function uuid(value: string): string {
  const hex = value.replaceAll('urn:', '').replaceAll('uuid:', '')
    .replace(/^[{}]+|[{}]+$/g, '').replaceAll('-', '');
  if (hex.length !== 32 || !/^[a-fA-F0-9]{32}$/.test(hex)) throw invalid('request_id must be a UUID');
  const lower = hex.toLowerCase();
  return [lower.slice(0, 8), lower.slice(8, 12), lower.slice(12, 16), lower.slice(16, 20), lower.slice(20)].join('-');
}
/** urlsplit's hostname check does not require a valid numeric port or normalize the saved URL. */
function require_https_url(value: string): void {
  const cleaned = value.replace(/^[\x00-\x20]+/, '').replace(/[\t\n\r]/g, '');
  const scheme = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(cleaned);
  const remaining = scheme === null ? cleaned : cleaned.slice(scheme[0].length);
  const authority = remaining.startsWith('//') ? remaining.slice(2).split(/[/?#]/, 1)[0]! : '';
  if (authority !== authority.normalize('NFKC')) {
    const normalized = authority.replace(/[@:#?]/g, '').normalize('NFKC');
    if (/[/?#@:]/.test(normalized)) throw new Error('netloc contains invalid characters under NFKC normalization');
  }
  const at = authority.lastIndexOf('@');
  const userinfo = at < 0 ? '' : authority.slice(0, at);
  const hostport = at < 0 ? authority : authority.slice(at + 1);
  let hostname: string;
  if (hostport.includes('[') || hostport.includes(']')) {
    const bracketed = /^\[([^\]]*)\]/.exec(hostport);
    if (bracketed === null) throw new Error('Invalid IPv6 URL');
    const suffix = hostport.slice(bracketed[0].length);
    if (suffix && !suffix.startsWith(':')) throw new Error('Invalid IPv6 URL');
    hostname = bracketed[1]!;
    if (hostname.startsWith('v') ? !/^v[a-fA-F0-9]+\..+$/.test(hostname) : isIP(hostname) !== 6)
      throw new Error('Invalid IPv6 URL');
  } else hostname = hostport.split(':', 1)[0]!;
  if (scheme?.[1]?.toLowerCase() !== 'https' || !hostname || userinfo.replace(':', ''))
    throw invalid('credential help links require an HTTPS URL');
}
export function decode_snapshot(value: unknown): CredentialRequestRowBody {
  try {
    const payload = conversation_event_payload_from_canonical_json(
      'credential_request', stringify_python_json({ request: value }),
    );
    if (!(payload instanceof CredentialRequestEventPayload)) throw new Error('snapshot');
    return payload.request;
  } catch { throw new Error('invalid credential request snapshot'); }
}
export function validate_request(value: unknown): CreateCredentialRequestBody {
  if (!object(value) || !exact_keys(value, [
    'request_id', 'name', 'provider', 'use', 'auth_kind', 'header_name', 'help', 'help_url', 'replacement',
  ])) throw invalid('invalid credential request metadata');
  const request_id = uuid(text(value.request_id, 36));
  const kind = value.auth_kind;
  if (kind !== 'bearer' && kind !== 'header')
    throw invalid('API key requests support bearer and header credentials');
  const header = value.header_name;
  if (kind === 'header') {
    if (typeof header !== 'string' || header.length < 1 || header.length > 200 || /[^!#$%&'*+.^_`|~0-9A-Za-z-]/.test(header))
      throw invalid('a header request needs its header name');
  } else if (header !== null) throw invalid('bearer requests do not name a header');
  const hint = value.help === null ? null : strip(text(value.help, 1000));
  const url = value.help_url === null ? null : strip(text(value.help_url, 2000));
  if (url !== null) require_https_url(url);
  const raw = value.replacement;
  let replacement: RequestReplacement | null = null;
  if (raw !== null) {
    if (!object(raw) || !exact_keys(raw, ['credential_id', 'name', 'revision']))
      throw invalid('invalid replacement metadata');
    const credential_id = text(raw.credential_id, 200);
    const name = text(raw.name, 200);
    const revision = numeric_value(raw.revision);
    if (numeric_kind(raw.revision) !== 'integer' || revision === undefined || revision < 1)
      throw invalid('invalid credential revision');
    replacement = { credential_id, name, revision };
  }
  return {
    request_id,
    name: strip(text(value.name, 200)),
    provider: strip(text(value.provider, 200)),
    use: strip(text(value.use, 2000)),
    auth_kind: kind,
    header_name: header as string | null,
    help: hint,
    help_url: url,
    replacement,
  };
}
