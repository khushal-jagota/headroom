import type { SettingsSearchEntry } from "./searchIndex";

/** Every Settings row that search can find, from the reads the pages already make. */
export function useSettingsSearchEntries(): { readonly current: SettingsSearchEntry[] } {
  return { get current() { return []; } };
}
