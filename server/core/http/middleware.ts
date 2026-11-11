import { gzipSync, createGzip } from 'node:zlib';
import { Transform, Readable } from 'node:stream';
import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import type { Config } from '../config.ts';
import { MAX_CONVERSATION_SEND_REQUEST_BYTES } from '../config.ts';
export interface WorkspaceAdmission {
    operation(): {
        admitted: boolean;
        release(): void;
    };
}
const SEND_PATHS = [/^\/api\/conversation\/conversations\/[^/]+\/send$/, /^\/api\/chief\/conversation\/send$/, /^\/api\/tickets\/[^/]+\/conversation\/send$/, /^\/api\/managers\/[^/]+\/conversation\/send$/, /^\/api\/messages\/send$/];
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
function reject(reply: FastifyReply, message: string): void { reply.code(403).send({ error: { code: 'agent_forbidden', message, detail: {} } }); }
export function installMiddleware(app: FastifyInstance, config: Config, workspace?: WorkspaceAdmission): void {
    app.addHook('onRequest', async (request, reply) => {
        const path = new URL(request.url, 'http://localhost').pathname;
        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
            const sha = request.headers['x-panels-app-sha'];
            if (sha !== undefined && sha !== config.app_sha) {
                reply.code(409).send({ error: { code: 'validation', message: 'Refresh page before making changes.', detail: {} } });
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
                const release = (): void => { if (!released) {
                    released = true;
                    lease.release();
                } };
                // Headers end the admission lease, not the response body.
                (request as FastifyRequest & {
                    releaseWorkspace?: () => void;
                }).releaseWorkspace = release;
                request.raw.once('aborted', release);
                reply.raw.once('close', release);
            }
        }
        const raw = rawHeaders(request);
        const builds = raw.get('x-panels-ios-build');
        if (builds && (builds.length !== 1 || builds[0]!.length > 10 || !/^\d+$/.test(builds[0]!) || Number(builds[0]) < 1)) {
            reply.code(426).header('cache-control', 'no-store').send({ error: { code: 'ios_update_required', message: 'Update Panels from TestFlight', details: { minimum_build: 1 } } });
            return;
        }
        if (config.trusted_ingress_provider) {
            if ([...securityHeaders].some(name => (raw.get(name)?.length ?? 0) > 1)) {
                reject(reply, 'duplicate security header is not allowed');
                return;
            }
            const origin = raw.get('origin')?.[0];
            if (origin !== undefined && !safe.has(request.method) && origin !== config.trusted_ingress_canonical_origin) {
                reject(reply, 'request origin is not allowed');
                return;
            }
            const login = raw.get('tailscale-user-login')?.[0];
            if (login !== undefined) {
                if (login !== config.trusted_ingress_allowed_login) {
                    reject(reply, 'Tailscale user is not allowed');
                    return;
                }
                Object.assign(request, { authenticatedOwner: true, trustedLogin: login });
                if (!path.startsWith('/api/updates')) {
                    for (const name of ['x-plan-conversation-id', 'x-plan-actor', 'x-plan-ticket-id', 'x-plan-manager-id'])
                        delete request.headers[name];
                }
            }
        }
        if (request.method === 'POST' && SEND_PATHS.some(pattern => pattern.test(path))) {
            const length = request.headers['content-length'];
            if (typeof length === 'string' && Number(length) > MAX_CONVERSATION_SEND_REQUEST_BYTES)
                reply.code(413).send({ detail: 'a conversation send request is too large' });
        }
    });
    app.addHook('preParsing', async (request, _reply, payload) => {
        const path = new URL(request.url, 'http://localhost').pathname;
        if (request.method !== 'POST' || !SEND_PATHS.some(pattern => pattern.test(path)))
            return payload;
        let size = 0;
        const limited = new Transform({ transform(chunk: Buffer, _encoding, done) {
                size += chunk.length;
                if (size > MAX_CONVERSATION_SEND_REQUEST_BYTES) {
                    const error = Object.assign(new Error('a conversation send request is too large'), { code: 'PANELS_SEND_TOO_LARGE' });
                    done(error);
                }
                else
                    done(null, chunk);
            } });
        payload.on('error', error => limited.destroy(error));
        return payload.pipe(limited);
    });
    app.addHook('onSend', async (request, reply, payload) => {
        (request as FastifyRequest & {
            releaseWorkspace?: () => void;
        }).releaseWorkspace?.();
        if (String(reply.getHeader('content-type') ?? '').startsWith('application/json'))
            reply.header('content-type', 'application/json');
        if (reply.statusCode === 307 || reply.statusCode === 304)
            reply.removeHeader('content-type');
        const marked = (request.routeOptions.config as {
            uncompressed?: boolean;
        }).uncompressed;
        if (marked || reply.hasHeader('content-encoding') || String(reply.getHeader('content-type') ?? '').startsWith('text/event-stream'))
            return payload;
        if (payload instanceof Readable) {
            const vary = reply.getHeader('vary');
            reply.header('vary', vary ? `${vary}, Accept-Encoding` : 'Accept-Encoding');
            if (!String(request.headers['accept-encoding'] ?? '').includes('gzip'))
                return payload;
            reply.header('content-encoding', 'gzip').removeHeader('content-length');
            const gzip = createGzip({ level: 4 });
            payload.on('error', error => gzip.destroy(error));
            return payload.pipe(gzip);
        }
        if (typeof payload !== 'string' && !Buffer.isBuffer(payload))
            return payload;
        const bytes = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
        if (bytes.length < 1024)
            return payload;
        const vary = reply.getHeader('vary');
        reply.header('vary', vary ? `${vary}, Accept-Encoding` : 'Accept-Encoding');
        if (!String(request.headers['accept-encoding'] ?? '').includes('gzip'))
            return payload;
        const compressed = gzipSync(bytes, { level: 4 });
        reply.header('content-encoding', 'gzip').header('content-length', compressed.length);
        return compressed;
    });
}
