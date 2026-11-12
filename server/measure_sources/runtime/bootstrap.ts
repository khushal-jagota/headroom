/** Private child entrypoint. No database or credential-key capability crosses this channel. */
import { Socket } from 'node:net';
import { createInterface } from 'node:readline';
import { registerHooks } from 'node:module';
import {
  parse_numeric_json,
  stringify_python_json,
  JsonNumber,
} from '../../core/http/numeric_json.ts';
import { failure } from '../contracts.ts';
import { float_number } from '../../measures/logic/formulas.ts';
import type { RunFailure, RuntimeLimits } from '../contracts.ts';

// Stored collector scripts survive release-directory changes. Resolve this one
// built-in module against the active child runtime, as Python resolves its module.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'panels:attention') {
      return {
        url: new URL('../../attention/collection.ts', import.meta.url).href,
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const output = new Socket({ fd: 3, readable: true, writable: true });
const input = createInterface({ input: output });
let sequence = 0;
const pending = new Map<number, { resolve(value: unknown): void; reject(reason: unknown): void }>();
function signed_zero(value: unknown): unknown {
  if (typeof value === 'number' && Object.is(value, -0)) return float_number(value);
  if (Array.isArray(value)) return value.map(signed_zero);
  if (value !== null && typeof value === 'object' && !(value instanceof JsonNumber))
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, signed_zero(item)]));
  return value;
}
function send(value: unknown): void {
  output.write(stringify_python_json(signed_zero(value), { compact: true }) + '\n');
}
function native(value: unknown): unknown {
  if (value instanceof JsonNumber)
    return value.integer !== null && !Number.isSafeInteger(value.value)
      ? value.integer
      : value.value;
  if (Array.isArray(value)) return value.map(native);
  if (value !== null && typeof value === 'object')
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, native(item)]));
  return value;
}
function freeze(value: unknown): unknown {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}
class ScriptFailure extends Error {
  detail: RunFailure;
  constructor(detail: RunFailure, message: string = detail.code) {
    super(message);
    this.detail = detail;
  }
}
function call(op: string, values: Record<string, unknown>): Promise<unknown> {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    send({ op, id, ...values });
  });
}
let initialized = false;
input.on('line', (line) => {
  const message = native(parse_numeric_json(line)) as Record<string, unknown>;
  if (!initialized) {
    initialized = true;
    void execute(message);
    return;
  }
  const waiter = pending.get(message.id as number);
  if (!waiter) process.exit(1);
  pending.delete(message.id as number);
  if (message.failure) waiter.reject(new ScriptFailure(message.failure as RunFailure));
  else waiter.resolve(message.value);
});
input.on('close', () => process.exit(1));
async function execute(config: Record<string, unknown>): Promise<void> {
  const limits = config.limits as RuntimeLimits;
  let requests = 0;
  let activeRequests = 0;
  let responseBytes = 0;
  const cpu = process.cpuUsage();
  const helpers = {
    context: freeze(config.context),
    latest: (measure_id: string) => call('latest', { measure_id }),
    record: (
      measure_id: string,
      date: string,
      value: number | bigint | null,
      options: Record<string, unknown> = {},
    ) =>
      call('record', {
        measure_id,
        date,
        value,
        unavailable_reason: null,
        query_start_at: null,
        query_end_at: null,
        limitation: null,
        ...options,
      }),
    record_dataset: (
      dataset_id: string,
      date: string,
      rows: unknown[],
      options: Record<string, unknown>,
    ) =>
      call('record_dataset', {
        dataset_id,
        date,
        rows,
        query_start_at: null,
        labels: {},
        unavailable_reason: null,
        limitation: null,
        ...options,
      }),
    async request(
      url: string,
      options: {
        credential_ref?: string | { scheme: string; name: string };
        method?: string;
        json_body?: unknown;
        headers?: Record<string, string>;
      } = {},
    ) {
      activeRequests++;
      try {
        const headers = new Headers(options.headers);
        const public_headers = new Set(['accept', 'content-type']);
        const credential_headers =
          options.credential_ref !== undefined ||
          [...headers.keys()].some((name) => !public_headers.has(name));
        if (options.credential_ref !== undefined) {
          const reference =
            typeof options.credential_ref === 'string'
              ? { scheme: 'local', name: options.credential_ref }
              : options.credential_ref;
          const secret = (await call('credential', { reference })) as {
            kind: string;
            token?: string;
            username?: string;
            password?: string;
            header_name?: string;
            value?: string;
          };
          if (secret.kind === 'bearer') headers.set('Authorization', `Bearer ${secret.token}`);
          else if (secret.kind === 'basic')
            headers.set(
              'Authorization',
              `Basic ${Buffer.from(`${secret.username}:${secret.password}`).toString('base64')}`,
            );
          else headers.set(secret.header_name!, secret.value!);
        }
        requests++;
        let response: Response;
        const controller = new AbortController();
        // HTTPX bounds idle network operations, rather than the entire response.
        const network_read = async <T>(read: () => Promise<T>): Promise<T> => {
          const timer = setTimeout(() => controller.abort(), limits.wall_seconds * 1000);
          try {
            return await read();
          } finally {
            clearTimeout(timer);
          }
        };
        try {
          if (options.json_body !== undefined) headers.set('Content-Type', 'application/json');
          let destination = new URL(url);
          let method = (options.method ?? 'GET').toUpperCase();
          let body =
            options.json_body === undefined ? undefined : stringify_python_json(options.json_body);
          for (let redirects = 0; ; redirects++) {
            response = await network_read(() =>
              fetch(destination, {
                method,
                headers,
                body,
                redirect: 'manual',
                signal: controller.signal,
              }),
            );
            const location = response.headers.get('location');
            if (![301, 302, 303, 307, 308].includes(response.status) || location === null) break;
            await response.body?.cancel();
            if (redirects === 20) throw new Error('Too many redirects');
            const next = new URL(location, destination);
            if (next.origin !== destination.origin && credential_headers) {
              throw new ScriptFailure(
                failure('transport_failure'),
                `Credential redirect refused for origin ${next.origin}`,
              );
            }
            if (
              (response.status === 303 && method !== 'HEAD') ||
              (response.status === 302 && method !== 'HEAD') ||
              (response.status === 301 && method === 'POST')
            ) {
              method = 'GET';
              body = undefined;
              headers.delete('content-length');
              headers.delete('transfer-encoding');
            }
            headers.delete('cookie');
            headers.delete('host');
            destination = next;
          }
        } catch (error) {
          controller.abort();
          if (error instanceof ScriptFailure) throw error;
          throw new ScriptFailure(failure('transport_failure'));
        }
        const chunks: Uint8Array[] = [];
        const reader = response.body?.getReader();
        try {
          if (reader)
            for (;;) {
              const { value: chunk, done } = await network_read(() => reader.read());
              if (done) break;
              responseBytes += chunk.byteLength;
              if (responseBytes > limits.response_bytes) {
                throw new ScriptFailure(failure('budget_exhausted', { budget: 'response_bytes' }));
              }
              chunks.push(chunk);
            }
        } catch (error) {
          if (error instanceof ScriptFailure) throw error;
          throw new ScriptFailure(failure('transport_failure'));
        } finally {
          controller.abort();
          await reader?.cancel().catch(() => {});
          reader?.releaseLock();
        }
        const body = Buffer.concat(chunks).toString('utf8');
        return {
          status: response.status,
          headers: Object.fromEntries(response.headers.entries()),
          raise_for_status() {
            if (response.status < 200 || response.status >= 300)
              throw new ScriptFailure(failure('http_error', { http_status: response.status }));
          },
          async text() {
            return body;
          },
          async json() {
            try {
              return native(parse_numeric_json(body));
            } catch {
              throw new ScriptFailure(failure('invalid_json'));
            }
          },
        };
      } finally {
        activeRequests--;
      }
    },
  };
  let result: RunFailure | null = null;
  try {
    const module = await import(config.script_url as string);
    if (
      Object.keys(module).length !== 1 ||
      typeof module.default !== 'function' ||
      Object.prototype.toString.call(module.default) !== '[object AsyncFunction]'
    )
      throw new TypeError();
    await module.default(helpers);
    // A script must await helper calls; unresolved calls cannot become successful output.
    if (pending.size || activeRequests) throw new ScriptFailure(failure('malformed_protocol'));
  } catch (error) {
    result =
      error instanceof ScriptFailure
        ? error.detail
        : failure('runtime_failure', {
            exception_type:
              error instanceof SyntaxError
                ? 'SyntaxError'
                : error instanceof TypeError
                  ? 'TypeError'
                  : error instanceof ReferenceError
                    ? 'ReferenceError'
                    : error instanceof RangeError
                      ? 'RangeError'
                      : null,
          });
    const sourceLine =
      error instanceof Error ? /source\.mjs:(\d+)/.exec(error.stack ?? '')?.[1] : undefined;
    if (result && sourceLine) result.source_line = Number(sourceLine);
  }
  const used = process.cpuUsage(cpu);
  send({
    op: 'done',
    failure: result,
    request_count: requests,
    response_bytes: responseBytes,
    cpu_milliseconds: Math.trunc((used.user + used.system) / 1000),
  });
}
