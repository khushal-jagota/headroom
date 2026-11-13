/** Preserve SQLite counters and Python integers without a native-number round trip. */
import { JsonNumber } from '../../core/http/numeric_json.ts';
import type { MailboxCounter } from '../contracts.ts';
export function integer(value: number | bigint | JsonNumber): bigint {
  if (value instanceof JsonNumber) {
    if (value.integer === null) throw new TypeError('Expected an integer counter');
    return value.integer;
  }
  return BigInt(value);
}
export function counter(value: bigint | string): MailboxCounter {
  return new JsonNumber(String(value));
}
