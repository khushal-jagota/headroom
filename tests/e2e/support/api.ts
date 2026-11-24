/** The public HTTP API and the TypeScript CLI, as a browser test reaches them. */
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { expect } from '@playwright/test';
import { REPO_ROOT, type JsonObject, type ServerHandle } from './server.ts';
import { agentEnvironment } from './agents.ts';

async function send(
  server: ServerHandle,
  method: string,
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<JsonObject> {
  // No X-Plan-* headers: the server treats the request as the unattributed owner.
  const response = await fetch(server.base + path, {
    method,
    headers: body === undefined ? headers : { 'content-type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  expect(response.status, `${method} ${path} -> ${response.status}: ${text}`).toBeLessThan(300);
  return text ? JSON.parse(text) : {};
}

export const api = {
  get: (server: ServerHandle, path: string) => send(server, 'GET', path),
  post: (server: ServerHandle, path: string, body: unknown, headers?: Record<string, string>) =>
    send(server, 'POST', path, body, headers),
  patch: (server: ServerHandle, path: string, body: unknown, headers?: Record<string, string>) =>
    send(server, 'PATCH', path, body, headers),
  put: (server: ServerHandle, path: string, body: unknown, headers?: Record<string, string>) =>
    send(server, 'PUT', path, body, headers),
};

export interface CliOptions {
  /** Run as this Ticket's Worker. */
  ticketId?: string;
  actor?: string;
  stdin?: string;
  /** After `ticket create`, accept the supplied Brief with no ceiling. */
  settleBrief?: boolean;
}

function runCli(server: ServerHandle, args: string[], env: Record<string, string>, stdin?: string) {
  const result = spawnSync(process.execPath, [join(REPO_ROOT, 'server/cli/main.ts'), ...args, '--json'], {
    cwd: REPO_ROOT,
    env,
    input: stdin,
    encoding: 'utf8',
    timeout: 30_000,
  });
  expect(result.status, `panels ${args.join(' ')} rc=${result.status}\nstderr: ${result.stderr}`).toBe(0);
  return JSON.parse(result.stdout) as JsonObject;
}

/** `panels <args> --json` through the TypeScript CLI, against this test's server only. */
export async function cli(server: ServerHandle, args: string[], options: CliOptions = {}): Promise<JsonObject> {
  const env: Record<string, string> = { ...server.environment, PLAN_SERVER_URL: server.base };
  if (options.ticketId !== undefined) {
    // A launched Worker's identity is both variables together.
    env.PLAN_TICKET_ID = options.ticketId;
    env.PLAN_ACTOR = 'worker';
  }
  if (options.actor !== undefined) env.PLAN_ACTOR = options.actor;
  if (options.actor !== undefined || options.ticketId !== undefined)
    Object.assign(env, await agentEnvironment(server, options.actor ?? 'worker', options.ticketId));
  const data = runCli(server, args, env, options.stdin);
  if (!options.settleBrief) return data;
  expect(args.slice(0, 2)).toEqual(['ticket', 'create']);
  return runCli(server, ['ticket', 'approve', data.id, '--ceiling', 'none'], env);
}
