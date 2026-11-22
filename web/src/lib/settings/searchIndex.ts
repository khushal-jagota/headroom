/** One row that Settings search can find. */
export type SettingsSearchEntry = {
  /** The `rowId` of the row to scroll to and flash, or null for a whole page. */
  rowId: string | null;
  href: string;
  label: string;
  /** The section path and the current value, shown under the label. */
  where: string;
  /** Extra words that find the row but are not shown. */
  keywords: string;
};

export type SettingsSearchHit = Omit<SettingsSearchEntry, "keywords">;

/** The entries that match every word of `query`, labels that match first.
 *
 * A word matches the label, the section path and value, or the keywords. Entries that
 * would show the same line twice are kept once.
 */
export function searchSettings(entries: readonly SettingsSearchEntry[], query: string): SettingsSearchHit[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const words = needle.split(/\s+/);
  const seen = new Set<string>();
  const matches: { hit: SettingsSearchHit; labelMatch: boolean; order: number }[] = [];
  entries.forEach((entry, order) => {
    const haystack = `${entry.label} ${entry.where} ${entry.keywords}`.toLowerCase();
    if (!words.every(word => haystack.includes(word))) return;
    const key = `${entry.href}|${entry.label}|${entry.where}`;
    if (seen.has(key)) return;
    seen.add(key);
    const { keywords: _keywords, ...hit } = entry;
    matches.push({ hit, labelMatch: entry.label.toLowerCase().includes(needle), order });
  });
  matches.sort((left, right) => Number(right.labelMatch) - Number(left.labelMatch) || left.order - right.order);
  return matches.map(match => match.hit);
}
