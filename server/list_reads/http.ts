import type { FastifyReply } from 'fastify';

export function dictionaryBody(raw: unknown, reply: FastifyReply): raw is Record<string, unknown> {
  if (raw !== undefined && raw !== null && typeof raw === 'object' && !Array.isArray(raw))
    return true;
  const missing = raw == null;
  reply.code(422).send({
    detail: [
      {
        type: missing ? 'missing' : 'dict_type',
        loc: ['body'],
        msg: missing ? 'Field required' : 'Input should be a valid dictionary',
        input: missing ? null : raw,
      },
    ],
  });
  return false;
}
export function requiredQuery(raw: unknown, key: string, reply: FastifyReply): raw is string {
  if (raw !== undefined) return true;
  reply.code(422).send({
    detail: [{ type: 'missing', loc: ['query', key], msg: 'Field required', input: null }],
  });
  return false;
}
export function integerQuery(raw: string | undefined, key: string): number | undefined {
  if (raw === undefined) return undefined;
  const clean = raw.trim();
  // Pydantic permits decimal notation only when the fractional digits are zero.
  if (!/^[+-]?[0-9]+(?:\.0+)?$/.test(clean)) throw new QueryIntegerError(key, raw);
  const value = Number(clean);
  if (!Number.isFinite(value)) throw new QueryIntegerError(key, raw);
  return value;
}
export class QueryIntegerError extends Error {
  readonly key: string;
  readonly input: string;
  constructor(key: string, input: string) {
    super('integer query');
    this.key = key;
    this.input = input;
  }
  payload() {
    return {
      type: 'int_parsing',
      loc: ['query', this.key],
      msg: 'Input should be a valid integer, unable to parse string as an integer',
      input: this.input,
    };
  }
}

/** FastAPI scalar query parameters use the final repeated value. */
export function scalarQueries<T extends Record<string, unknown>>(raw: T): T {
  return Object.fromEntries(
    Object.entries(raw).map(([key, value]) => [key, Array.isArray(value) ? value.at(-1) : value]),
  ) as T;
}
