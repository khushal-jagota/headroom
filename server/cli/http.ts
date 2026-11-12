import { parse_numeric_json, stringify_python_json } from '../core/http/numeric_json.ts';
import type { CommandResult, Invocation } from './contracts.ts';
import { CliFailure, human_error } from './errors.ts';

export const json = (
  value: unknown,
  options: Parameters<typeof stringify_python_json>[1] = {},
): string => stringify_python_json(value, { ensure_ascii: true, ...options });
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
function connection_message(error: unknown): string {
  const cause = (error as { cause?: { code?: string; message?: string } })?.cause;
  switch (cause?.code) {
    case 'ECONNREFUSED':
      return '[Errno 111] Connection refused';
    case 'ENOTFOUND':
      return '[Errno -2] Name or service not known';
    case 'ECONNRESET':
      return '[Errno 104] Connection reset by peer';
    case 'ETIMEDOUT':
      return 'timed out';
  }
  if ((error as Error)?.name === 'TimeoutError') return 'timed out';
  return cause?.message ?? String(error);
}
async function request(
  inv: Invocation,
  method: string,
  path: string,
  options: { json_body?: unknown; params?: Record<string, any> },
): Promise<{ response: Response; text: string }> {
  const env = inv.environment;
  if (env.PLAN_EXECUTOR_ROOT)
    fail_validation(inv, 'Executor transport remains with the executor unit.');
  const base = env.PLAN_SERVER_URL?.trim() || 'http://127.0.0.1:8767';
  const url = new URL(base.replace(/\/+$/, '') + path);
  for (const [key, value] of Object.entries(options.params ?? {})) {
    if (Array.isArray(value)) for (const item of value) url.searchParams.append(key, String(item));
    else
      url.searchParams.set(
        key,
        value === null || value === undefined
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
  try {
    const response = await fetch(url, {
      method,
      headers,
      body:
        options.json_body === undefined ? undefined : json(options.json_body, { compact: true }),
      redirect: 'manual',
      signal: AbortSignal.timeout(30000),
    });
    return {
      response,
      text: new TextDecoder('utf-8', { ignoreBOM: true }).decode(await response.arrayBuffer()),
    };
  } catch (error) {
    if (inv.values.as_json)
      throw new CliFailure({
        exit_code: 2,
        stdout: '',
        stderr:
          json({ error: { code: 'connection', message: connection_message(error), detail: {} } }) +
          '\n',
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
  options: { json_body?: unknown; params?: Record<string, any> } = {},
): Promise<any> {
  const { response, text } = await request(inv, method, path, options);
  let data: any = null;
  try {
    data = parse_numeric_json(text.startsWith('\uFEFF') ? text.slice(1) : text);
  } catch {
    /* Python retains null for non-JSON responses. */
  }
  if (response.ok && !(data && typeof data === 'object' && 'error' in data)) return data;
  return fail_response(inv, response.status, data, text);
}
export async function send_text(
  inv: Invocation,
  method: string,
  path: string,
  options: { params?: Record<string, any> } = {},
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
