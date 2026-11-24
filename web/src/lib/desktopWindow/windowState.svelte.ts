/* The desktop window's own state. The desktop app's preload marks the page root with
 * `data-desktop-window` (desktop/src/preload.ts); a normal browser never has it, so nothing
 * here changes what a browser shows. */
import { untrack, type Snippet } from "svelte";

/** True only inside the desktop app's main frame. Read when a component starts, never at import. */
export function isDesktopWindow(): boolean {
  return typeof document !== "undefined" && document.documentElement.hasAttribute("data-desktop-window");
}

/** Whether the sidebar is hidden. Kept for this run of the app only (owner: not across a restart). */
export const windowSidebar = $state({ hidden: false });

export function toggleWindowSidebar(): void {
  windowSidebar.hidden = !windowSidebar.hidden;
}

/** ⌃⌘S hides and shows the sidebar, the macOS standard. */
export function keyTogglesWindowSidebar(event: KeyboardEvent): boolean {
  return event.ctrlKey && event.metaKey && !event.altKey && !event.shiftKey && !event.repeat
    && !event.defaultPrevented && event.key.toLowerCase() === "s";
}

/** The rows the open pages hand to the window's top row. The newest one shows: a page that
 *  arrives while the one before it is still leaving (a Review card) takes the row at once. */
type PageRowEntry = { id: number; row: Snippet };
let nextPageRowId = 0;
const pageRows = $state<PageRowEntry[]>([]);

/** Hand a page's row to the top row; the returned function takes it back. It reads the list
 *  untracked, so a page that lifts its row from an effect does not depend on the list. */
export function liftPageRow(row: Snippet): () => void {
  const id = nextPageRowId++;
  untrack(() => pageRows.push({ id, row }));
  return () => untrack(() => {
    const index = pageRows.findIndex(entry => entry.id === id);
    if (index >= 0) pageRows.splice(index, 1);
  });
}

export function shownPageRow(): Snippet | null {
  return pageRows.at(-1)?.row ?? null;
}
