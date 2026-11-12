import type { FastifyInstance, FastifyRequest } from 'fastify';
import { JsonNumber, parse_numeric_json } from './numeric_json.ts';
/** Area-scoped intake retains numeric token kinds before validation and fingerprints. */
export function install_numeric_json_parser(app: FastifyInstance): void {
  app.setReplySerializer((payload) =>
    JSON.stringify(payload, (_key, value: unknown) =>
      typeof value === 'number' && Number.isInteger(value) && !Number.isSafeInteger(value)
        ? new JsonNumber(value.toExponential()).toJSON()
        : value,
    ),
  );
  app.removeAllContentTypeParsers();
  const parser = (
    _request: FastifyRequest,
    body: string | Buffer,
    done: (error: Error | null, value?: unknown) => void,
  ): void => {
    try {
      done(null, body.length ? parse_numeric_json(String(body)) : undefined);
    } catch (error) {
      done(
        Object.assign(error as Error, {
          code: 'FST_ERR_CTP_INVALID_JSON_BODY',
          jsonSource: String(body),
        }),
      );
    }
  };
  app.addContentTypeParser(
    /^application\/(?:[^;]+\+)?json(?:;.*)?$/,
    { parseAs: 'string' },
    parser,
  );
  app.addContentTypeParser('*', { parseAs: 'string' }, (request, body, done) => {
    if (!request.headers['content-type']) parser(request, body, done);
    else done(null, body);
  });
}
