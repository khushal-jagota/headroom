import { createHash } from 'node:crypto';
export function compact(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(compact).join(',') + ']';
  if (value !== null && typeof value === 'object') {
    const object = value as Record<string, unknown>;
    return (
      '{' +
      Object.keys(object)
        .sort()
        .map((key) => JSON.stringify(key) + ':' + compact(object[key]))
        .join(',') +
      '}'
    );
  }
  return JSON.stringify(value);
}
export function digest(value: unknown): string {
  return createHash('sha256').update(compact(value)).digest('hex');
}
export const ORDERING_GENERATION = `intl-${process.versions.icu}-base-v1-review-v4`;
/** Later sources use the same comparator for membership order, including id tie breaks. */
export function compareTitles(
  locale: string,
  left: { title: string; id: string },
  right: { title: string; id: string },
): number {
  return (
    new Intl.Collator(locale, { sensitivity: 'base' }).compare(left.title, right.title) ||
    new Intl.Collator(locale).compare(left.id, right.id)
  );
}
