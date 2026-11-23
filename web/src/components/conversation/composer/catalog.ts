import type { ComposerCatalogEntry, ComposerCatalogEntryKind } from "../../../lib/conversation/wire";

export function catalogEntriesForTrigger(
  entries: readonly ComposerCatalogEntry[],
  trigger: "/" | "$" | "@" | null
): ComposerCatalogEntry[] {
  const kinds: readonly ComposerCatalogEntryKind[] = trigger === "/"
    ? ["command"]
    : trigger === "$"
      ? ["skill"]
      : trigger === "@"
        ? ["app", "plugin"]
        : [];
  return entries.filter((entry) => kinds.includes(entry.kind));
}

/** Prefix matches come before contains matches, alphabetically within each group. */
export function catalogEntriesMatching(
  entries: readonly ComposerCatalogEntry[],
  typed: string,
  trigger: "/" | "$" | "@" | null
): ComposerCatalogEntry[] {
  const wanted = typed.toLowerCase();
  const searchable = (entry: ComposerCatalogEntry): string => {
    const displayText = entry.display_text.toLowerCase();
    return trigger !== null && displayText.startsWith(trigger)
      ? displayText.slice(trigger.length)
      : displayText;
  };
  const byDisplayText = (one: ComposerCatalogEntry, other: ComposerCatalogEntry): number =>
    one.display_text.localeCompare(other.display_text);
  const startsWithIt = entries.filter((entry) =>
    searchable(entry).startsWith(wanted)
  );
  const containsIt = entries.filter(
    (entry) => !searchable(entry).startsWith(wanted) && searchable(entry).includes(wanted)
  );
  return [...startsWithIt.sort(byDisplayText), ...containsIt.sort(byDisplayText)];
}
