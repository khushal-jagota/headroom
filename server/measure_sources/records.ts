import { parse_numeric_json, stringify_numeric_json } from '../core/http/numeric_json.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
export function encode_record(value: unknown): string {
  return stringify_numeric_json(value);
}
export function decode_record<T>(raw: string): NumericJSON<T> {
  return parse_numeric_json(raw) as NumericJSON<T>;
}
