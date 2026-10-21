/** The words somebody typed and has not sent.
 *
 * They live outside the component that draws them, because the component can be taken
 * away by anything above it — a brief disconnection, a reload, a pane that is rebuilt —
 * and words that were never sent cannot be recovered by retrying anything.
 */
const PREFIX = "panels:composer-draft:";

export function readComposerDraft(key: string): string {
  try {
    return window.sessionStorage.getItem(PREFIX + key) ?? "";
  } catch {
    return "";
  }
}

export function writeComposerDraft(key: string, text: string): void {
  try {
    if (text === "") window.sessionStorage.removeItem(PREFIX + key);
    else window.sessionStorage.setItem(PREFIX + key, text);
  } catch {
    // A browser that refuses storage still composes. It only loses the safety net.
  }
}
