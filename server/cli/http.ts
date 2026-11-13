import { readFileSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { delimiter, join } from 'node:path';
import { checkServerIdentity } from 'node:tls';
import { isIP, type Socket } from 'node:net';
import { request as http_request, type ClientRequest } from 'node:http';
import { request as https_request, type RequestOptions } from 'node:https';
import { gunzipSync, inflateSync, brotliDecompressSync } from 'node:zlib';
import { HttpsProxyAgent } from 'https-proxy-agent';
import { rates_proxy_for } from '../conversation/usage/proxy.ts';
import { parse_numeric_json } from '../core/http/numeric_json.ts';
import type { CommandResult, Invocation } from './contracts.ts';
import { CliFailure, human_error } from './errors.ts';

export interface TransportLimits {
  connect_ms: number;
  write_ms: number;
  read_ms: number;
}
export const CLI_TRANSPORT_LIMITS: Readonly<TransportLimits> = Object.freeze({
  connect_ms: 30000,
  write_ms: 30000,
  read_ms: 30000,
});

export const json = (
  value: unknown,
  options: { ensure_ascii?: boolean; compact?: boolean; indent?: 2 } = {},
): string =>
  JSON.stringify(
    value,
    (_key, item) =>
      typeof item === 'bigint'
        ? (JSON as unknown as { rawJSON(text: string): unknown }).rawJSON(String(item))
        : item,
    options.indent,
  );
export function emit(data: unknown, as_json: boolean, human: string): CommandResult {
  return { exit_code: 0, stdout: (as_json ? json(data) : human) + '\n', stderr: '' };
}
export function fail_validation(
  invocation: Invocation,
  message: string,
  options: { recovery?: string; no_route?: string; detail?: unknown } = {},
): never {
  const payload = { error: { code: 'validation', message, detail: options.detail ?? {} } };
  throw new CliFailure(
    invocation.values.as_json
      ? { exit_code: 1, stdout: '', stderr: json(payload) + '\n' }
      : human_error(invocation.command, message, 1, options),
  );
}
function fail_response(inv: Invocation, status: number, data: any, text: string): never {
  const payload =
    data && typeof data === 'object' && 'error' in data
      ? data
      : { error: { code: 'http_error', message: `HTTP ${status}`, detail: data ?? text } };
  if (inv.values.as_json)
    throw new CliFailure({ exit_code: 1, stdout: '', stderr: json(payload) + '\n' });
  const error = payload.error;
  if (error.code === 'agent_forbidden')
    throw new CliFailure(
      human_error(inv.command, 'This actor cannot perform this operation.', 1, {
        no_route: 'No panels call can perform it as this actor.',
      }),
    );
  const no_route =
    error.code === 'gateway_offline'
      ? 'No panels call can work until the gateway is available.'
      : error.code === 'http_error'
        ? 'No panels call can recover from this server response.'
        : undefined;
  throw new CliFailure(human_error(inv.command, String(error.message), 1, { no_route }));
}
/** Keep the native cause so a connection failure describes the actual runtime. */
export async function connection_message(error: unknown): Promise<string> {
  const cause = error instanceof Error && error.cause instanceof Error ? error.cause : error;
  return cause instanceof Error ? cause.message : String(cause);
}

function no_proxy(url: URL, env: NodeJS.ProcessEnv): boolean {
  for (const entry of (env.no_proxy ?? env.NO_PROXY ?? '')
    .split(',')
    .map((value) => value.trim())) {
    if (entry === '*') return true;
    if (!entry) continue;
    const scheme = entry.includes('://') ? entry.slice(0, entry.indexOf('://') + 1) : 'all:';
    if (scheme !== 'all:' && scheme !== url.protocol) continue;
    let authority = (entry.includes('://') ? entry.slice(entry.indexOf('://') + 3) : entry).split(
      '/',
    )[0];
    let port = '';
    const match = authority.match(/^(.*):([0-9]+)$/);
    if (match && (authority.startsWith('[') || authority.split(':').length === 2)) {
      authority = match[1];
      port = match[2];
    }
    if (port && port !== (url.port || (url.protocol === 'https:' ? '443' : '80'))) continue;
    const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, '');
    const name = authority.toLowerCase().replace(/^\[|\]$/g, '');
    if (entry.includes('://')) {
      if (
        name === host ||
        (name.startsWith('*.') && host.endsWith(name.slice(1))) ||
        (name.startsWith('*') && (host === name.slice(1) || host.endsWith('.' + name.slice(1))))
      )
        return true;
    } else if (isIP(name) || name === 'localhost') {
      if (host === name) return true;
    } else if (name.startsWith('.')) {
      if (host.endsWith(name) && host !== name.slice(1)) return true;
    } else if (host === name || host.endsWith('.' + name)) return true;
  }
  return false;
}

/** Reuse the shared environment selector, with HTTPX's all:// fallback and bypass. */
export function cli_proxy_for(url: URL, env: NodeJS.ProcessEnv): string | null {
  if (no_proxy(url, env)) return null;
  const effective = { ...env, no_proxy: '' };
  return (
    rates_proxy_for(url.href, effective) ??
    rates_proxy_for('all:' + url.href.slice(url.protocol.length), effective)
  );
}

class ClientConfigurationError extends Error {}
function certificate_options(env: NodeJS.ProcessEnv): Pick<RequestOptions, 'ca'> {
  if (env.SSL_CERT_FILE) {
    try {
      return { ca: readFileSync(env.SSL_CERT_FILE) };
    } catch {
      throw new ClientConfigurationError(`Cannot read SSL_CERT_FILE: ${env.SSL_CERT_FILE}`);
    }
  }
  if (!env.SSL_CERT_DIR) return {};
  const ca: Buffer[] = [];
  for (const directory of env.SSL_CERT_DIR.split(delimiter)) {
    let entries: string[];
    try {
      entries = readdirSync(directory);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') continue;
      throw error;
    }
    // OpenSSL looks up lowercase subject hashes and stops at the first absent
    // collision suffix. Merely looking like a hash must not grant a CA trust.
    const names = new Set(entries);
    for (const hash of new Set(
      entries.filter((name) => /^[0-9a-f]{8}\.0$/.test(name)).map((name) => name.slice(0, 8)),
    )) {
      for (let index = 0; names.has(`${hash}.${index}`); index++) {
        const certificate = readFileSync(join(directory, `${hash}.${index}`));
        const result = spawnSync('openssl', ['x509', '-subject_hash', '-noout'], {
          input: certificate,
          env,
          encoding: 'utf8',
        });
        if (result.error) throw result.error;
        if (result.status === 0 && result.stdout.trim() === hash) ca.push(certificate);
      }
    }
  }
  return { ca };
}

async function read_request(
  url: URL,
  method: string,
  headers: Record<string, string>,
  body: string | undefined,
  env: NodeJS.ProcessEnv,
  limits: Readonly<TransportLimits>,
): Promise<{ response: { status: number; ok: boolean }; text: string }> {
  const selected = cli_proxy_for(url, env);
  const proxy =
    selected === null ? null : new URL(selected.includes('://') ? selected : 'http://' + selected);
  const tls = certificate_options(env);
  const tunnel =
    proxy !== null && url.protocol === 'https:' ? new HttpsProxyAgent(proxy, tls) : null;
  const endpoint = proxy !== null && tunnel === null ? new URL(proxy.href) : new URL(url.href);
  const outgoing: Record<string, string> = {
    Accept: '*/*',
    'Accept-Encoding': 'gzip, deflate',
    ...headers,
  };
  if (body !== undefined) outgoing['Content-Length'] = String(Buffer.byteLength(body));
  const target_host = url.hostname.replace(/^\[|\]$/g, '');
  const options: RequestOptions = {
    method,
    headers: outgoing,
    ...tls,
    agent: tunnel ?? false,
    // A CONNECT agent removes host before TLS upgrades. Bind verification to the
    // actual destination even for IP targets, whose SNI must remain empty.
    servername: isIP(target_host) ? '' : target_host,
    checkServerIdentity: (_hostname, certificate) => checkServerIdentity(target_host, certificate),
  };
  if (proxy !== null && tunnel === null) {
    endpoint.username = '';
    endpoint.password = '';
    options.path = url.protocol + '//' + url.host + url.pathname + url.search;
    outgoing['Host'] = url.host;
    if (proxy.username || proxy.password)
      outgoing['Proxy-Authorization'] =
        'Basic ' +
        Buffer.from(
          decodeURIComponent(proxy.username) + ':' + decodeURIComponent(proxy.password),
        ).toString('base64');
    if (url.username || url.password)
      outgoing['Authorization'] =
        'Basic ' +
        Buffer.from(
          decodeURIComponent(url.username) + ':' + decodeURIComponent(url.password),
        ).toString('base64');
  }
  try {
    return await new Promise((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      let request: ClientRequest;
      let settled = false;
      let header_socket: Socket | undefined;
      const header_progress = () => wait_for_phase();
      const stop_header_progress = () => header_socket?.off('data', header_progress);
      const fail = (error: Error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        stop_header_progress();
        reject(error);
      };
      const wait_for_phase = (milliseconds = limits.read_ms) => {
        if (settled) return;
        clearTimeout(timer);
        timer = setTimeout(() => {
          const error = Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' });
          request.destroy(error);
          fail(error);
        }, milliseconds);
      };
      request = (endpoint.protocol === 'https:' ? https_request : http_request)(
        endpoint,
        options,
        (response) => {
          stop_header_progress();
          const chunks: Buffer[] = [];
          let received = 0;
          const expected = Number(response.headers['content-length']);
          response.on('aborted', () => {
            if (Number.isSafeInteger(expected) && received < expected)
              fail(
                new Error(
                  `peer closed connection without sending complete message body (received ${received} bytes, expected ${expected})`,
                ),
              );
          });
          wait_for_phase();
          response.on('data', (chunk: Buffer) => {
            chunks.push(Buffer.from(chunk));
            received += chunk.length;
            wait_for_phase();
          });
          response.on('error', fail);
          response.on('end', () => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            try {
              let bytes: Buffer = Buffer.concat(chunks);
              for (const encoding of String(response.headers['content-encoding'] ?? '')
                .split(',')
                .reverse()) {
                if (encoding.trim() === 'gzip') bytes = gunzipSync(bytes);
                else if (encoding.trim() === 'deflate') bytes = inflateSync(bytes);
                else if (encoding.trim() === 'br') bytes = brotliDecompressSync(bytes);
              }
              resolve({
                response: {
                  status: response.statusCode ?? 0,
                  ok: (response.statusCode ?? 0) >= 200 && (response.statusCode ?? 0) < 300,
                },
                text: new TextDecoder('utf-8', { ignoreBOM: true }).decode(bytes),
              });
            } catch (error) {
              reject(error);
            }
          });
        },
      );
      request.on('error', fail);
      request.on('socket', (socket) => {
        // HTTPX times each socket read, including incomplete response headers.
        // Native HTTP emits response only after all headers have arrived.
        header_socket = socket;
        socket.on('data', header_progress);
        socket.once('connect', () =>
          wait_for_phase(endpoint.protocol === 'https:' ? limits.connect_ms : limits.write_ms),
        );
        socket.once('secureConnect', () => wait_for_phase(limits.write_ms));
      });
      wait_for_phase(limits.connect_ms);
      request.end(body, () => wait_for_phase(limits.read_ms));
    });
  } finally {
    tunnel?.destroy();
  }
}

async function request(
  inv: Invocation,
  method: string,
  path: string,
  options: {
    json_body?: unknown;
    params?: Record<string, any>;
    transport_limits?: Readonly<TransportLimits>;
  },
): Promise<{ response: { status: number; ok: boolean }; text: string }> {
  const env = inv.environment;
  if (env.PLAN_EXECUTOR_ROOT)
    fail_validation(inv, 'Executor transport remains with the executor unit.');
  try {
    const base = env.PLAN_SERVER_URL?.trim() || 'http://127.0.0.1:8767';
    if (!base.includes('://'))
      throw new Error("Request URL is missing an 'http://' or 'https://' protocol.");
    let url: URL;
    try {
      url = new URL(base.replace(/\/+$/, '') + path);
    } catch {
      throw new ClientConfigurationError(`Invalid Panels server URL: ${base}`);
    }
    if (!['http:', 'https:'].includes(url.protocol))
      throw new Error(`Request URL has an unsupported protocol '${url.protocol.slice(0, -1)}://'.`);
    for (const [key, value] of Object.entries(options.params ?? {})) {
      if (Array.isArray(value))
        for (const item of value) url.searchParams.append(key, String(item));
      else
        url.searchParams.set(
          key,
          value == null
            ? ''
            : typeof value === 'boolean'
              ? String(value).toLowerCase()
              : String(value),
        );
    }
    const headers: Record<string, string> = {};
    const actor = env.PLAN_ACTOR?.trim();
    if (actor) {
      headers['X-Plan-Actor'] = actor;
      if (actor === 'worker' && env.PLAN_TICKET_ID?.trim())
        headers['X-Plan-Ticket-ID'] = env.PLAN_TICKET_ID.trim();
      if (actor === 'manager' && env.PLAN_MANAGER_ID?.trim())
        headers['X-Plan-Manager-ID'] = env.PLAN_MANAGER_ID.trim();
      if (env.PLAN_CONVERSATION_ID?.trim())
        headers['X-Plan-Conversation-ID'] = env.PLAN_CONVERSATION_ID.trim();
    }
    if (options.json_body !== undefined) headers['Content-Type'] = 'application/json';
    return await read_request(
      url,
      method,
      headers,
      options.json_body === undefined
        ? undefined
        : json(options.json_body, { compact: true, ensure_ascii: false }),
      env,
      options.transport_limits ?? CLI_TRANSPORT_LIMITS,
    );
  } catch (error) {
    if (error instanceof ClientConfigurationError) fail_validation(inv, error.message);
    if (inv.values.as_json)
      throw new CliFailure({
        exit_code: 2,
        stdout: '',
        stderr:
          json({
            error: { code: 'connection', message: await connection_message(error), detail: {} },
          }) + '\n',
      });
    throw new CliFailure(
      human_error(inv.command, 'The Panels server is unavailable.', 2, {
        no_route: 'No panels call can work until the server is available.',
      }),
    );
  }
}
export async function send(
  inv: Invocation,
  method: string,
  path: string,
  options: {
    json_body?: unknown;
    params?: Record<string, any>;
    transport_limits?: Readonly<TransportLimits>;
  } = {},
): Promise<any> {
  const { response, text } = await request(inv, method, path, options);
  let data: any = null;
  try {
    data = parse_numeric_json(text.startsWith('\uFEFF') ? text.slice(1) : text);
  } catch {
    /* A non-JSON response has no decoded data. */
  }
  if (response.ok && !(data && typeof data === 'object' && 'error' in data)) return data;
  return fail_response(inv, response.status, data, text);
}
export async function send_text(
  inv: Invocation,
  method: string,
  path: string,
  options: { params?: Record<string, any>; transport_limits?: Readonly<TransportLimits> } = {},
): Promise<string> {
  const { response, text } = await request(inv, method, path, options);
  if (response.ok) return text;
  let data: any = null;
  try {
    data = parse_numeric_json(text.startsWith('\uFEFF') ? text.slice(1) : text);
  } catch {
    /* Preserve text envelope fallback. */
  }
  return fail_response(inv, response.status, data, text);
}
