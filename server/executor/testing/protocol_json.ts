/** One pure comparison exchange. No server, provider, or socket is constructed. */
import { readFileSync } from 'node:fs';
import { JsonNumber } from '../../core/http/numeric_json.ts';
import {
  ExecutorJSONSyntaxError,
  ordered_json_entries,
  parse_executor_json,
} from '../protocol_json.ts';

function float_snapshot(value: number): unknown {
  if (!Number.isFinite(value))
    return ['float', Number.isNaN(value) ? 'nan' : value > 0 ? 'inf' : '-inf'];
  const bytes = Buffer.alloc(8);
  bytes.writeDoubleBE(value);
  return ['float', bytes.toString('hex')];
}
function snapshot(value: unknown): unknown {
  if (value === null) return ['null'];
  if (typeof value === 'boolean') return ['bool', value];
  if (typeof value === 'string') return ['str', value];
  if (value instanceof JsonNumber) {
    if (value.kind === 'integer') return ['int', String(value.integer)];
    return float_snapshot(value.value);
  }
  if (typeof value === 'number') {
    if (Number.isFinite(value)) throw new Error('Finite number lost its token kind');
    return float_snapshot(value);
  }
  if (Array.isArray(value)) return ['array', value.map(snapshot)];
  if (typeof value !== 'object') throw new Error('Unexpected parser value');
  return [
    'object',
    ordered_json_entries(value as Record<string, unknown>).map(([key, item]) => [
      key,
      snapshot(item),
    ]),
  ];
}
const cases = JSON.parse(readFileSync(0, 'utf8')) as { name: string; body: string }[];
const results = cases.map(({ name, body }) => {
  try {
    return { name, ok: true, value: snapshot(parse_executor_json(Buffer.from(body, 'base64'))) };
  } catch (error) {
    if (!(error instanceof ExecutorJSONSyntaxError)) throw error;
    return { name, ok: false, error: error.message };
  }
});
process.stdout.write(JSON.stringify(results));
