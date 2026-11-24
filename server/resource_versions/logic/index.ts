import { PlannerError } from '../../core/contracts.ts';
import { MAX_RESOURCE_KEYS, MAX_RESOURCE_KEY_LENGTH } from '../configuration.ts';
export function checkedKey(key: string): string {
  if (!key.startsWith('/api/') || Array.from(key).length > MAX_RESOURCE_KEY_LENGTH)
    throw new PlannerError('validation', 'invalid resource key');
  return key;
}
export function parseKeys(raw: string): string[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new PlannerError('validation', 'invalid resource keys');
  }
  if (
    !Array.isArray(parsed) ||
    parsed.length > MAX_RESOURCE_KEYS ||
    parsed.some((key) => typeof key !== 'string')
  )
    throw new PlannerError('validation', 'invalid resource keys');
  return [...new Set(parsed as string[])];
}
/**
 * One key's revision. A key that its provider refuses, such as a deleted Ticket, is
 * unknown. It does not fail the other keys of the same probe.
 */
export function revisionFor(
  key: string,
  capture: (checked: string) => { revision: string } | null,
): string | null {
  try {
    return capture(checkedKey(key))?.revision ?? null;
  } catch (error) {
    if (!(error instanceof PlannerError)) throw error;
    return null;
  }
}
