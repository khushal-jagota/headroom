/** One clipboard owner substitutes virtual ranges across the complete selection. */
type CopyRange = { element: HTMLElement; text: () => string | null };
type CopyOwner = { ranges: Map<string, CopyRange>; listener: (event: ClipboardEvent) => void };
const owners = new WeakMap<Document, CopyOwner>();
let nextIdentity = 0;
const ATTRIBUTE = 'data-markdown-copy-range';

export function registerMarkdownCopyRange(element: HTMLElement, text: () => string | null): () => void {
  const document = element.ownerDocument;
  let owner = owners.get(document);
  if (!owner) {
    const ranges = new Map<string, CopyRange>();
    const listener = (event: ClipboardEvent) => copySelection(document, ranges, event);
    owner = { ranges, listener };
    owners.set(document, owner);
    document.addEventListener('copy', listener);
  }
  const identity = String(++nextIdentity);
  element.setAttribute(ATTRIBUTE, identity);
  owner.ranges.set(identity, { element, text });
  const registeredOwner = owner;
  let registered = true;
  return () => {
    if (!registered) return;
    registered = false;
    registeredOwner.ranges.delete(identity);
    element.removeAttribute(ATTRIBUTE);
    if (registeredOwner.ranges.size === 0) {
      document.removeEventListener('copy', registeredOwner.listener);
      if (owners.get(document) === registeredOwner) owners.delete(document);
    }
  };
}

function copySelection(document: Document, ranges: Map<string, CopyRange>, event: ClipboardEvent): void {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement ||
      (event.target instanceof HTMLElement && event.target.isContentEditable)) return;
  const selection = document.getSelection();
  if (!selection?.rangeCount || selection.isCollapsed || !event.clipboardData) return;
  const selected: { range: Range; replacements: Map<string, string> }[] = [];
  for (let index = 0; index < selection.rangeCount; index++) {
    const range = selection.getRangeAt(index);
    const replacements = new Map<string, string>();
    for (const [identity, candidate] of ranges) {
      if (!candidate.element.isConnected || !range.intersectsNode(candidate.element)) continue;
      const value = candidate.text();
      if (value !== null) replacements.set(identity, value);
    }
    selected.push({ range, replacements });
  }
  if (!selected.some(item => item.replacements.size > 0)) return;
  const fragments: DocumentFragment[] = [];
  let substituted = false;
  for (const { range, replacements } of selected) {
    // The full selected fragment retains neighbors and the exact partial endpoints.
    const fragment = range.cloneContents();
    for (const placeholder of fragment.querySelectorAll(`[${ATTRIBUTE}]`)) {
      const value = replacements.get(placeholder.getAttribute(ATTRIBUTE)!);
      if (value === undefined) continue;
      placeholder.replaceChildren(document.createTextNode(value));
      substituted = true;
    }
    fragments.push(fragment);
  }
  if (!substituted) return;
  event.clipboardData.setData('text/plain', fragments.map(copiedDomText).join('\n'));
  event.preventDefault();
}

function copiedDomText(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
  const tag = node instanceof HTMLElement ? node.tagName.toLowerCase() : '';
  if (tag === 'br') return '\n';
  if (tag === 'img' && node instanceof HTMLElement) return node.getAttribute('alt') || '';
  const text = Array.from(node.childNodes).map(copiedDomText).join('');
  if (tag === 'td' || tag === 'th') return text + '\t';
  return /^(p|h[1-6]|li|pre|tr|blockquote)$/.test(tag) ? text + '\n' : text;
}
