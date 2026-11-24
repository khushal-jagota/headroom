import type { UpdateBarrier } from '../../environments/update_barrier.ts';
import { gzipSync, createGzip } from 'node:zlib';
import { Transform, Readable } from 'node:stream';
import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import type { Config } from '../config.ts';
import { PlannerError } from '../contracts.ts';
import { MAX_CONVERSATION_SEND_REQUEST_BYTES } from '../config.ts';
import {
  CLIENT_COMPATIBILITY,
  supported_ios_build,
  supported_desktop_bridge,
} from '../../client_versions/policy.ts';
import { parse_client_identity } from '../../client_versions/identity.ts';
import { remember_client_version } from '../../client_versions/data.ts';
import type { ClientIdentity } from '../../client_versions/contracts.ts';
import type { Database } from '../database/contracts.ts';
import type { Clock } from '../clock.ts';
import { TRUSTED_INGRESS_CONFIGURED } from '../authority/identity.ts';
import type { HttpRequestScope } from './lifetime.ts';
import { is_executor_link_connection } from './executor_link.ts';
import { SERVER_LOCATION_PATH } from '../../server_link/contracts.ts';
export interface WorkspaceAdmission {
  operation(): {
    admitted: boolean;
    release(): void;
  };
}
const SEND_PATHS = [
  /^\/api\/conversation\/conversations\/[^/]+\/send$/,
  /^\/api\/conversation\/conversations\/[^/]+\/user-input-answers$/,
  /^\/api\/chief\/conversation\/send$/,
  /^\/api\/tickets\/[^/]+\/conversation\/send$/,
  /^\/api\/managers\/[^/]+\/conversation\/send$/,
  /^\/api\/messages\/send$/,
];
/** The desktop app serves its screens from this origin. Change it here only. */
export const DESKTOP_APP_ORIGIN = 'panels://app';
const securityHeaders = new Set(['tailscale-user-login', 'origin']);
const safe = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);
function rawHeaders(request: FastifyRequest): Map<string, string[]> {
  const result = new Map<string, string[]>();
  for (let i = 0; i < request.raw.rawHeaders.length; i += 2) {
    const key = request.raw.rawHeaders[i]!.toLowerCase();
    result.set(key, [...(result.get(key) ?? []), request.raw.rawHeaders[i + 1]!.trim()]);
  }
  return result;
}
function reject(reply: FastifyReply, message: string): void {
  reply.code(403).send({ error: { code: 'agent_forbidden', message, detail: {} } });
}
/** HTTP and raw WebSocket upgrades share the same hosted ingress authority. */
export function trusted_ingress_login(
  config: Config,
  raw: ReadonlyMap<string, readonly string[]>,
  method: string,
): string | null {
  if (!config.trusted_ingress_provider) return null;
  const refuse = (message: string): never => {
    throw new PlannerError('agent_forbidden', message);
  };
  if ([...securityHeaders].some((name) => (raw.get(name)?.length ?? 0) > 1))
    refuse('duplicate security header is not allowed');
  const origin = raw.get('origin')?.[0]?.trim();
  if (
    origin !== undefined &&
    !safe.has(method) &&
    origin !== config.trusted_ingress_canonical_origin &&
    origin !== DESKTOP_APP_ORIGIN
  )
    refuse('request origin is not allowed');
  const login = raw.get('tailscale-user-login')?.[0]?.trim();
  if (login !== undefined && login !== config.trusted_ingress_allowed_login)
    refuse('Tailscale user is not allowed');
  return login ?? null;
}
export function installMiddleware(
  app: FastifyInstance,
  config: Config,
  workspace?: WorkspaceAdmission,
  lifetime?: HttpRequestScope,
  update_barrier?: UpdateBarrier,
  client_versions?: { database: Database; clock: Clock },
): void {
  const identities = new WeakMap<FastifyRequest, ClientIdentity>();
  app.addHook('onRequest', async (request, reply) => {
    const operation = async () => {
      const path = new URL(request.url, 'http://localhost').pathname;
      const executor_link = is_executor_link_connection(request.raw.socket);
      if (executor_link) {
        // The machine credential, checked by the executor routes, says which machine this is.
        // The matched route decides, because the router does not resolve `..` in a raw path.
        const route = request.routeOptions.url;
        if (
          route === undefined ||
          !(route.startsWith('/api/executor/') || route === SERVER_LOCATION_PATH)
        )
          return reject(reply, 'the local executor link serves executor routes only');
        if (request.headers['tailscale-user-login'] !== undefined)
          return reject(reply, 'the local executor link does not accept a Tailscale login');
      }
      // Raw headers also reach the streams that hijack or flush their own headers.
      if (request.headers.origin === DESKTOP_APP_ORIGIN) {
        reply.raw.setHeader('access-control-allow-origin', DESKTOP_APP_ORIGIN);
        const preflight = request.headers['access-control-request-method'];
        if (request.method === 'OPTIONS' && preflight !== undefined) {
          reply.raw.setHeader('access-control-allow-methods', preflight);
          const headers = request.headers['access-control-request-headers'];
          if (headers !== undefined) reply.raw.setHeader('access-control-allow-headers', headers);
          reply.raw.setHeader('access-control-max-age', '600');
          reply.code(204).send();
          return;
        }
      }
      Object.assign(request, { [TRUSTED_INGRESS_CONFIGURED]: !!config.trusted_ingress_provider });
      const raw = rawHeaders(request);
      let login: string | null = null;
      try {
        if (!executor_link) login = trusted_ingress_login(config, raw, request.method);
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        reject(reply, error.message);
        return;
      }
      if (login !== null) {
        Object.assign(request, { authenticatedOwner: true, trustedLogin: login });
      }
      let client: ClientIdentity | null;
      try {
        client = parse_client_identity(raw.get('x-panels-client'));
      } catch (error) {
        reply
          .code(400)
          .send({ error: { code: 'validation', message: (error as Error).message, detail: {} } });
        return;
      }
      if (client) identities.set(request, client);
      if (
        client?.kind === 'desktop' &&
        path.startsWith('/api/') &&
        !(path === '/api/meta' && ['GET', 'HEAD'].includes(request.method)) &&
        request.method !== 'OPTIONS' &&
        !supported_desktop_bridge(client)
      ) {
        reply
          .code(426)
          .header('cache-control', 'no-store')
          .send({
            error: {
              code: 'desktop_update_required',
              message: 'Update the Panels desktop app.',
              details: CLIENT_COMPATIBILITY.desktop_shell,
            },
          });
        return;
      }
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
        const sha = request.headers['x-panels-app-sha'];
        if (sha !== undefined && sha !== config.app_sha) {
          reply.code(409).send({
            error: {
              code: 'validation',
              message: 'Refresh page before making changes.',
              detail: {},
            },
          });
          return;
        }
        if (
          update_barrier?.frozen &&
          !path.startsWith('/api/updates') &&
          !path.startsWith('/api/executor/')
        ) {
          reply.code(503).send({ detail: 'Owner update in progress.' });
          return;
        }
        if (workspace && !path.startsWith('/api/updates') && !path.startsWith('/api/executor/')) {
          const lease = workspace.operation();
          if (!lease.admitted) {
            lease.release();
            reply.code(503).send({ detail: 'Owner update in progress.' });
            return;
          }
          let released = false;
          const release = (): void => {
            if (!released) {
              released = true;
              lease.release();
            }
          };
          // Headers end the admission lease, not the response body.
          (
            request as FastifyRequest & {
              releaseWorkspace?: () => void;
            }
          ).releaseWorkspace = release;
          request.raw.once('aborted', release);
          reply.raw.once('close', release);
        }
      }
      const builds = raw.get('x-panels-ios-build');
      if (
        (builds &&
          (builds.length !== 1 ||
            builds[0]!.length > 10 ||
            !/^\d+$/.test(builds[0]!) ||
            !supported_ios_build(Number(builds[0])))) ||
        (client?.kind === 'ios' && !supported_ios_build(client.build))
      ) {
        reply
          .code(426)
          .header('cache-control', 'no-store')
          .send({
            error: {
              code: 'ios_update_required',
              message: 'Update Panels from TestFlight',
              details: { minimum_build: CLIENT_COMPATIBILITY.ios.minimum_build },
            },
          });
        return;
      }
      if (request.method === 'POST' && SEND_PATHS.some((pattern) => pattern.test(path))) {
        const length = request.headers['content-length'];
        if (typeof length === 'string' && Number(length) > MAX_CONVERSATION_SEND_REQUEST_BYTES)
          reply.code(413).send({ detail: 'a conversation send request is too large' });
      }
    };
    if (!lifetime) return operation();
    return request.request_lifetime === lifetime
      ? lifetime.track(operation)
      : lifetime.run(operation);
  });
  app.addHook('preParsing', async (request, _reply, payload) => {
    const path = new URL(request.url, 'http://localhost').pathname;
    if (request.method !== 'POST' || !SEND_PATHS.some((pattern) => pattern.test(path)))
      return payload;
    let size = 0;
    const limited = new Transform({
      transform(chunk: Buffer, _encoding, done) {
        size += chunk.length;
        if (size > MAX_CONVERSATION_SEND_REQUEST_BYTES) {
          const error = Object.assign(new Error('a conversation send request is too large'), {
            code: 'PANELS_SEND_TOO_LARGE',
          });
          done(error);
        } else done(null, chunk);
      },
    });
    payload.on('error', (error) => limited.destroy(error));
    return payload.pipe(limited);
  });
  app.addHook('onSend', async (request, reply, payload) => {
    const client = identities.get(request);
    identities.delete(request);
    if (client && client_versions && reply.statusCode !== 401 && reply.statusCode !== 403)
      remember_client_version(client_versions.database, client, client_versions.clock.nowUnix());
    (
      request as FastifyRequest & {
        releaseWorkspace?: () => void;
      }
    ).releaseWorkspace?.();
    if (String(reply.getHeader('content-type') ?? '').startsWith('application/json'))
      reply.header('content-type', 'application/json');
    if (reply.statusCode === 307 || reply.statusCode === 304) reply.removeHeader('content-type');
    const marked = (
      request.routeOptions.config as {
        uncompressed?: boolean;
      }
    ).uncompressed;
    if (
      marked ||
      reply.hasHeader('content-encoding') ||
      String(reply.getHeader('content-type') ?? '').startsWith('text/event-stream')
    )
      return payload;
    if (payload instanceof Readable) {
      const vary = reply.getHeader('vary');
      reply.header('vary', vary ? `${vary}, Accept-Encoding` : 'Accept-Encoding');
      if (!String(request.headers['accept-encoding'] ?? '').includes('gzip')) return payload;
      reply.header('content-encoding', 'gzip').removeHeader('content-length');
      const gzip = createGzip({ level: 4 });
      payload.on('error', (error) => gzip.destroy(error));
      return payload.pipe(gzip);
    }
    if (typeof payload !== 'string' && !Buffer.isBuffer(payload)) return payload;
    const bytes = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
    if (bytes.length < 1024) return payload;
    const vary = reply.getHeader('vary');
    reply.header('vary', vary ? `${vary}, Accept-Encoding` : 'Accept-Encoding');
    if (!String(request.headers['accept-encoding'] ?? '').includes('gzip')) return payload;
    const compressed = gzipSync(bytes, { level: 4 });
    reply.header('content-encoding', 'gzip').header('content-length', compressed.length);
    return compressed;
  });
}
