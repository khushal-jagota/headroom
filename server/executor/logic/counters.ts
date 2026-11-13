/** Project a SQLite decimal read through the shared wire integer policy. */
import type { WireInteger } from '../wire.generated.ts';
import { normalize_integer } from '../integers.ts';
export function counter(decimal: string): WireInteger {
  return normalize_integer(BigInt(decimal));
}
