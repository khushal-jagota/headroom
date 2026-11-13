/** A record-shaped value, without a domain schema or prototype requirement. */
export function is_record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
