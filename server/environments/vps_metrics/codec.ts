import { inflateSync } from 'node:zlib';
import { deflate } from 'pako';
import {
  JsonNumber,
  numeric_value,
  parse_numeric_json,
  stringify_python_json,
} from '../../core/http/numeric_json.ts';
import type { MetricNumber } from './contracts.ts';
import { MetricsValueError } from './contracts.ts';
export function number(value: MetricNumber): number {
  return numeric_value(value)!;
}
function nonfinite_error(value: number): MetricsValueError {
  return new MetricsValueError(`Metrics JSON requires a finite number: ${value}`);
}
export function float(value: number): JsonNumber {
  if (!Number.isFinite(value)) throw nonfinite_error(value);
  const source = String(value);
  return new JsonNumber(
    Object.is(value, -0) ? '-0.0' : /[.eE]/.test(source) ? source : source + '.0',
  );
}
function finite(value: unknown): void {
  if (typeof value === 'number' || value instanceof JsonNumber) {
    if (!Number.isFinite(typeof value === 'number' ? value : value.value))
      throw nonfinite_error(typeof value === 'number' ? value : value.value);
  } else if (Array.isArray(value)) {
    for (const item of value) finite(item);
  } else if (value !== null && typeof value === 'object') {
    for (const item of Object.values(value)) finite(item);
  }
}
export function encode(value: unknown): string {
  finite(value);
  return stringify_python_json(value, { compact: true, ensure_ascii: true });
}
export function decode<T>(value: string): T {
  return parse_numeric_json(value) as T;
}
export function pack(value: unknown): Buffer {
  return Buffer.from(deflate(Buffer.from(encode(value)), { level: 1, legacyHash: true }));
}
export function unpack<T>(value: Buffer): T {
  return decode<T>(inflateSync(value).toString('utf8'));
}
