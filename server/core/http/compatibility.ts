import type { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import { HttpError } from './errors.ts';
import { PlannerError } from '../contracts.ts';
import { jsonFailure } from './json.ts';
interface Match {
    pattern: RegExp;
    methods: string[];
    path: string;
}
function regex(path: string): RegExp {
    return new RegExp('^' + path.split('/').map(part => part === '*' ? '.*' : part.startsWith(':') ? '[^/]+' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('/') + '$');
}
export function installCompatibility(app: FastifyInstance): void {
    const routes: Match[] = [];
    app.addHook('onRoute', route => { routes.push({ pattern: regex(route.url), methods: Array.isArray(route.method) ? route.method : [route.method], path: route.url }); });
    const rejectUnmatched = (request: FastifyRequest, reply: FastifyReply): boolean => {
        const url = new URL(request.url, `${request.protocol}://${request.host}`);
        const matches = routes.filter(route => route.pattern.test(url.pathname));
        if (matches.some(route => route.methods.includes(request.method)))
            return false;
        const match = matches[0];
        if (match) {
            reply.code(405);
            if (!match.path.endsWith('/*'))
                reply.header('allow', match.methods.join(', '));
            reply.send({ detail: 'Method Not Allowed' });
            return true;
        }
        if (url.pathname !== '/') {
            const alternate = url.pathname.endsWith('/') ? url.pathname.slice(0, -1) : url.pathname + '/';
            if (routes.some(route => route.pattern.test(alternate))) {
                url.pathname = alternate;
                reply.code(307).header('location', url.href).send('');
                return true;
            }
        }
        reply.code(404).send({ detail: 'Not Found' });
        return true;
    };
    app.addHook('preParsing', async (request, reply, payload) => {
        if (!rejectUnmatched(request, reply))
            return payload;
    });
    app.setNotFoundHandler(async (request, reply) => {
        if (!rejectUnmatched(request, reply))
            reply.code(404).send({ detail: 'Not Found' });
    });
    app.setErrorHandler((caught, request, reply) => {
        const error = caught as Error & {
            code?: string;
            jsonSource?: string;
        };
        if (error instanceof HttpError) {
            reply.code(error.statusCode).send({detail:error.detail});
            return;
        }
        if (error instanceof PlannerError) {
            reply.code(error.statusCode).send(error.toPayload());
            return;
        }
        if (error.code === 'PANELS_SEND_TOO_LARGE') {
            reply.code(413).send({ detail: 'a conversation send request is too large' });
            return;
        }
        if (error.code === 'FST_ERR_CTP_INVALID_JSON_BODY' || error.code === 'FST_ERR_CTP_EMPTY_JSON_BODY') {
            const failure = jsonFailure(error.jsonSource ?? '');
            reply.code(422).send({ detail: [{ type: 'json_invalid', loc: ['body', failure.position], msg: 'JSON decode error', input: {}, ctx: { error: failure.message } }] });
            return;
        }
        if (error.code === 'FST_ERR_CTP_INVALID_MEDIA_TYPE') {
            reply.code(422).send({ detail: [{ type: 'dict_type', loc: ['body'], msg: 'Input should be a valid dictionary', input: request.body }] });
            return;
        }
        request.log.error(error);
        reply.code(500).header('content-type', 'text/plain; charset=utf-8').send('Internal Server Error');
    });
    // FastAPI accepts absent Content-Type and application/*+json as JSON.
    app.removeContentTypeParser('application/json');
    const jsonParser = (_request: FastifyRequest, body: string | Buffer, done: (error: Error | null, value?: unknown) => void): void => {
        try {
            done(null, body.length ? JSON.parse(String(body)) : undefined);
        }
        catch (error) {
            done(Object.assign(error as Error, { code: 'FST_ERR_CTP_INVALID_JSON_BODY', jsonSource: String(body) }));
        }
    };
    app.addContentTypeParser(/^application\/(?:[^;]+\+)?json(?:;.*)?$/, { parseAs: 'string' }, jsonParser);
    app.addContentTypeParser('*', { parseAs: 'string' }, (request, body, done) => {
        if (!request.headers['content-type'])
            jsonParser(request, body, done);
        else
            done(null, body);
    });
}
