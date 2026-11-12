import { parse_numeric_json, stringify_python_json } from '../core/http/numeric_json.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
export function encode_record(value: unknown, compact = true): string {
  return stringify_python_json(value, { ensure_ascii: true, compact });
}
export function decode_record<T>(raw: string): NumericJSON<T> {
  return parse_numeric_json(raw) as NumericJSON<T>;
}
