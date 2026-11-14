import type { FastifyInstance, FastifyRequest } from 'fastify';
import { JsonNumber, parse_numeric_json, stringify_numeric_json } from './numeric_json.ts';
import { MAX_PYTHON_JSONDICT_DEPTH } from './configuration.ts';
import { ResponseDepthError } from './errors.ts';
/** Pydantic's dict[str, Any] response serializer admits descendants through depth 255. */
export function stringify_numeric_response(
  payload: unknown,
  checkDepth = true,
  maxDepth = MAX_PYTHON_JSONDICT_DEPTH,
): string {
  const pending: { value: unknown; depth: number }[] = [{ value: payload, depth: 0 }];
  while (checkDepth && pending.length) {
    const { value, depth } = pending.pop()!;
    if (depth > maxDepth) throw new ResponseDepthError('response exceeds the supported JSON depth');
    // Retained numeric metadata represents a scalar on Python's response boundary.
    if (value !== null && typeof value === 'object' && !(value instanceof JsonNumber)) {
      for (const child of Object.values(value)) pending.push({ value: child, depth: depth + 1 });
    }
  }
  return stringify_numeric_json(payload);
}
/** Area-scoped intake retains numeric token kinds before validation and fingerprints. */
export function install_numeric_json_parser(
  app: FastifyInstance,
  maxDepth = MAX_PYTHON_JSONDICT_DEPTH,
): void {
  app.setReplySerializer((payload, statusCode) =>
    stringify_numeric_response(payload, statusCode >= 200 && statusCode < 300, maxDepth),
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
