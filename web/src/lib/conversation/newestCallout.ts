/** v5: only the newest error callout in a conversation wears the filled primary button; older
 *  callouts show every action unfilled. Callouts register their element here and are told
 *  whether they are the last one, in document order, inside their conversation pane. */
type Entry = { element: HTMLElement; setNewest: (newest: boolean) => void };

const entries = new Set<Entry>();
let scheduled = false;

function paneOf(element: HTMLElement): Element | null {
  return element.closest("[data-conversation-pane]");
}

export function recomputeNewestCallouts(): void {
  scheduled = false;
  const lastByPane = new Map<Element | null, Entry>();
  for (const entry of entries) {
    if (!entry.element.isConnected) continue;
    const pane = paneOf(entry.element);
    const last = lastByPane.get(pane);
    if (!last || last.element.compareDocumentPosition(entry.element) & Node.DOCUMENT_POSITION_FOLLOWING) {
      lastByPane.set(pane, entry);
    }
  }
  for (const entry of entries) entry.setNewest(lastByPane.get(paneOf(entry.element)) === entry);
}

function schedule(): void {
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(recomputeNewestCallouts);
}

/** Registers a callout; returns its unregister. */
export function registerCallout(element: HTMLElement, setNewest: (newest: boolean) => void): () => void {
  const entry: Entry = { element, setNewest };
  entries.add(entry);
  schedule();
  return () => {
    entries.delete(entry);
    schedule();
  };
}
