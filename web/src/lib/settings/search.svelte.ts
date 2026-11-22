import type { SettingsSearchHit } from "./searchIndex";

/** The Settings search field's state, shared by the rail that holds the field and the page
 *  that shows the results. `flashRowId` names the row that an opened result scrolls to. */
export const settingsSearch = $state<{
  query: string;
  highlighted: number;
  hits: SettingsSearchHit[];
  flashRowId: string | null;
}>({ query: "", highlighted: 0, hits: [], flashRowId: null });

export function openSettingsSearchHit(hit: SettingsSearchHit): void {
  settingsSearch.query = "";
  settingsSearch.highlighted = 0;
  settingsSearch.flashRowId = hit.rowId;
  window.location.hash = hit.href;
}
