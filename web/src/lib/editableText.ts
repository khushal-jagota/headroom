function stringValue(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function setEmptyState(element: HTMLElement, raw: string): void {
  if (raw.trim()) element.removeAttribute("data-empty");
  else element.setAttribute("data-empty", "true");
}

export function paintPlainEditable(element: HTMLElement, value: unknown): void {
  const raw = stringValue(value);
  element.textContent = raw;
  setEmptyState(element, raw);
}

export function readPlainEditable(element: HTMLElement): string {
  return element.textContent || "";
}

export function editableMarkupSnapshot(element: HTMLElement): string {
  return element.innerHTML;
}

export function editableMarkupChanged(element: HTMLElement, snapshot: string): boolean {
  return element.innerHTML !== snapshot;
}

export function refreshPlainEditableEmptyState(element: HTMLElement): void {
  setEmptyState(element, readPlainEditable(element));
}

export function handlePlainTextPaste(event: ClipboardEvent): void {
  const text = event.clipboardData?.getData("text/plain") || "";
  event.preventDefault();
  if (document.queryCommandSupported?.("insertText")) {
    document.execCommand("insertText", false, text);
    return;
  }
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return;
  const range = selection.getRangeAt(0);
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  event.currentTarget?.dispatchEvent(new InputEvent("input", { bubbles: true }));
}
