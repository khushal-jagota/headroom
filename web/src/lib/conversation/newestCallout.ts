/** The newest error in one conversation keeps its primary action. */
type Entry = { element: HTMLElement; setNewest: (newest: boolean) => void };

export const NEWEST_CALLOUTS = Symbol("conversation-newest-callouts");
export type NewestCallouts = ReturnType<typeof createNewestCallouts>;

/** Each conversation pane owns its registry and releases entries with its rows. */
export function createNewestCallouts() {
  const entries = new Set<Entry>();
  let scheduled = false;

  function recompute(): void {
    scheduled = false;
    let last: Entry | undefined;
    for (const entry of entries) {
      if (!entry.element.isConnected) continue;
      if (!last || last.element.compareDocumentPosition(entry.element) & Node.DOCUMENT_POSITION_FOLLOWING) {
        last = entry;
      }
    }
    for (const entry of entries) entry.setNewest(entry === last);
  }

  function schedule(): void {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(recompute);
  }

  function registerCallout(element: HTMLElement, setNewest: (newest: boolean) => void): () => void {
    const entry: Entry = { element, setNewest };
    entries.add(entry);
    schedule();
    return () => {
      entries.delete(entry);
      schedule();
    };
  }

  return { registerCallout };
}
