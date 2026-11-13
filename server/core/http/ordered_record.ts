/** Preserve public field order and make new record fields visible instead of dropping them. */
import { testModeFromEnvironment } from '../config.ts';
export function ordered_record<T extends object>(
  record: T,
  fields: readonly string[],
  label: string,
  internal: readonly string[] = [],
): T {
  const known = new Set([...fields, ...internal]);
  const unknown = Object.keys(record).filter((field) => !known.has(field));
  if (unknown.length && testModeFromEnvironment())
    throw new Error(`Unknown ${label} response fields: ${unknown.join(', ')}`);
  return Object.fromEntries(
    [...fields.filter((field) => Object.hasOwn(record, field)), ...unknown].map((field) => [
      field,
      Reflect.get(record, field),
    ]),
  ) as T;
}
