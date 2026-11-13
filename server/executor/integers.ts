/** Exact wire integers project to native numbers only when that projection is lossless. */
import { JsonNumber } from '../core/http/numeric_json.ts';
import type { WireInteger } from './wire.generated.ts';

export function integer_value(value: WireInteger): bigint {
  if (value instanceof JsonNumber) {
    if (value.integer === null) throw new TypeError('Expected an integer value');
    return value.integer;
  }
  return BigInt(value);
}

export function normalize_integer(value: number | bigint | JsonNumber): WireInteger {
  const exact = typeof value === 'bigint' ? value : integer_value(value);
  return exact >= BigInt(Number.MIN_SAFE_INTEGER) && exact <= BigInt(Number.MAX_SAFE_INTEGER)
    ? Number(exact)
    : value instanceof JsonNumber
      ? value
      : new JsonNumber(String(exact));
}

/** Opaque JSON preserves unsafe integers and an integral float's otherwise erased kind. */
export function normalize_json_number(value: JsonNumber): number | JsonNumber {
  if (value.kind === 'integer') return normalize_integer(value);
  return Number.isFinite(value.value) && Number.isInteger(value.value) ? value : value.value;
}
