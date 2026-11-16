import { JsonNumber } from '../core/http/numeric_json.ts';
import { json } from './http.ts';
import { MissingResponseValue } from './errors.ts';

export function scalar_text(value: unknown): string {
  if (value === undefined) throw new MissingResponseValue();
  if (value instanceof JsonNumber)
    return value.kind === 'integer' ? String(value.integer) : String(value.value);
  return value !== null && typeof value === 'object' ? json(value) : String(value);
}
