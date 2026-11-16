/** Fail clearly when the server omits a required field. */
import type { Invocation } from './contracts.ts';
import { fail_validation } from './http.ts';
export function response_field(inv: Invocation, record: unknown, key: string, label = key): any {
  if (
    record === null ||
    typeof record !== 'object' ||
    !Object.hasOwn(record, key) ||
    Reflect.get(record, key) === undefined
  )
    fail_validation(inv, `response is missing required field: ${label}`);
  return Reflect.get(record, key);
}
