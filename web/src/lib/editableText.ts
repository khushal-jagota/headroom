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

export function readPlainEditable(element: HTMLElement, multiline = false): string {
  const text = element.textContent || "";
  if (!multiline || !text) return text;
  // Select-all/delete can leave the accepted trailing newline as an empty
  // caret line. Treat a document containing only line breaks as cleared text.
  if (!text.replace(/[\r\n]/g, "")) return "";
  // Native plain editors use div/p boundaries for Enter and br for Shift+Enter.
  // A final br only holds the caret on an empty line. innerText counts that
  // placeholder as another break under pre-wrap, so read these boundaries once.
  function read(parent: Node): string {
    let raw = "";
    let previousBlock = false;
    for (const [index, child] of Array.from(parent.childNodes).entries()) {
      const block = child.nodeName === "DIV" || child.nodeName === "P";
      if (block) {
        if (index > 0 && !previousBlock && child.previousSibling?.nodeName !== "BR") raw += "\n";
        raw += read(child);
        if (child.nextSibling) raw += "\n";
      } else if (child.nodeName === "BR") {
        if (child.nextSibling) raw += "\n";
      } else {
        raw += child.nodeType === Node.TEXT_NODE ? child.textContent || "" : read(child);
      }
      previousBlock = block;
    }
    return raw;
  }
  return read(element).replace(/\r\n?/g, "\n");
}

export function editableMarkupSnapshot(element: HTMLElement): string {
  return element.innerHTML;
}

export function editableMarkupChanged(element: HTMLElement, snapshot: string): boolean {
  return element.innerHTML !== snapshot;
}

export function refreshPlainEditableEmptyState(element: HTMLElement, multiline = false): void {
  setEmptyState(element, readPlainEditable(element, multiline));
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
