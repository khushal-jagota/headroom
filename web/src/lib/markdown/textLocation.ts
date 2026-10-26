import type { MarkdownContentIdentity, MarkdownPresentation, MarkdownTargetDemand, MarkdownTargetState, MarkdownTextLocation } from './presentationContracts';
import { demandVisibleContent, visibleContentTargetReady } from '../visibleContent';

export function sameMarkdownIdentity(a: MarkdownContentIdentity, b: MarkdownContentIdentity): boolean {
  return a.algorithm === b.algorithm && a.digest === b.digest && a.utf16Length === b.utf16Length;
}

/** Hash exact UTF-16 code units, including unpaired surrogates, without retaining source. */
export async function markdownContentIdentity(source: string): Promise<MarkdownContentIdentity> {
  const bytes = new Uint8Array(source.length * 2);
  for (let index = 0; index < source.length; index++) {
    const unit = source.charCodeAt(index);
    bytes[index * 2] = unit & 255; bytes[index * 2 + 1] = unit >>> 8;
  }
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return Object.freeze({ algorithm: 'sha256', digest: Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join(''), utf16Length: source.length });
}

export function validCharacterOffset(text: string, offset: number): boolean {
  return Number.isInteger(offset) && offset >= 0 && offset < text.length &&
    !(offset > 0 && /[\uDC00-\uDFFF]/.test(text[offset]) && /[\uD800-\uDBFF]/.test(text[offset - 1]));
}

export function characterRect(node: Text, offset: number): DOMRectReadOnly | null {
  if (!node.isConnected || !validCharacterOffset(node.data, offset)) return null;
  const range = node.ownerDocument.createRange();
  range.setStart(node, offset);
  range.setEnd(node, offset + (node.data.codePointAt(offset)! > 0xffff ? 2 : 1));
  return Array.from(range.getClientRects()).find(rect => rect.height > 0 && rect.width > 0) ?? null;
}

type Leaf = { node: Text; blockIndex: number; path: readonly number[] };
type Group = { element: HTMLElement; start: number; end: number };
const key = (block: number, path: readonly number[]) => `${block}:${path.join('.')}`;

/** One facade lives as long as its Markdown host, including released surfaces. */
export function createMarkdownPresentation(host: HTMLElement) {
  let source: string | undefined;
  let identity: MarkdownContentIdentity | null = null;
  let generation = 0;
  let failed = false;
  let parsed = false;
  let groups: Group[] = [];
  const leaves = new Map<string, Leaf>();
  const demands = new Set<{ location: MarkdownTextLocation; notify(): void; sync(): void; release(): void }>();
  const changed = () => { for (const demand of [...demands]) { demand.sync(); demand.notify(); } };
  const leafFor = (location: MarkdownTextLocation) => leaves.get(key(location.blockIndex, location.textLeafPath))?.node ?? null;
  const connectedLeaf = (location: MarkdownTextLocation) => { const node = leafFor(location); return node?.isConnected ? node : null; };
  function presentationPending(node: Text): boolean {
    if (host.hasAttribute('data-markdown-rendering') || !visibleContentTargetReady(host, node)) return true;
    for (let ancestor = node.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (ancestor.matches('[data-visible-content-restoring], [aria-busy="true"]')) return true;
      if (ancestor === host) break;
    }
    return false;
  }
  function measure(location: MarkdownTextLocation): MarkdownTargetState {
    if (identity && !sameMarkdownIdentity(identity, location.contentIdentity)) return { kind: 'unavailable', reason: 'source-changed' };
    if (failed) return { kind: 'failed', retryable: true };
    if (!identity) return { kind: 'pending' };
    if (!Number.isInteger(location.blockIndex) || location.blockIndex < 0 || !location.textLeafPath.every(index => Number.isInteger(index) && index >= 0)) return { kind: 'unavailable', reason: 'missing-leaf' };
    const group = groups.find(group => location.blockIndex >= group.start && location.blockIndex < group.end);
    const node = connectedLeaf(location);
    if (!node) {
      if (parsed && (!group || group.element.dataset.visibleContentState !== 'released')) return { kind: 'unavailable', reason: 'missing-leaf' };
      return { kind: 'pending' };
    }
    if (!validCharacterOffset(node.data, location.utf16Offset)) return { kind: 'unavailable', reason: 'missing-leaf' };
    if (presentationPending(node)) return { kind: 'pending' };
    const rect = characterRect(node, location.utf16Offset);
    return rect ? { kind: 'ready', rect } : { kind: 'unavailable', reason: 'missing-leaf' };
  }
  const presentation: MarkdownPresentation = {
    measure,
    capture(viewport) {
      if (!identity || failed || !parsed) return null;
      let best: { location: MarkdownTextLocation; rect: DOMRectReadOnly } | null = null;
      for (const leaf of leaves.values()) {
        if (!leaf.node.isConnected || !leaf.node.data.trim() || presentationPending(leaf.node)) continue;
        const range = leaf.node.ownerDocument.createRange(); range.selectNodeContents(leaf.node);
        const boxes = Array.from(range.getClientRects());
        if (!boxes.some(rect => rect.bottom > viewport.top && rect.top < viewport.bottom && rect.right > viewport.left && rect.left < viewport.right)) continue;
        // Binary search locates the first visible line even inside an oversized block.
        let low = 0; let high = leaf.node.length;
        while (low < high) {
          let mid = (low + high) >>> 1;
          if (!validCharacterOffset(leaf.node.data, mid) && mid > 0) mid--;
          const rect = characterRect(leaf.node, mid);
          if (rect && rect.bottom > viewport.top) high = mid;
          else low = Math.max(low + 1, mid + (leaf.node.data.codePointAt(mid)! > 0xffff ? 2 : 1));
        }
        for (let offset = low; offset < leaf.node.length; offset++) {
          if (!validCharacterOffset(leaf.node.data, offset) || !leaf.node.data[offset].trim()) continue;
          const rect = characterRect(leaf.node, offset);
          if (!rect || rect.bottom <= viewport.top || rect.right <= viewport.left) continue;
          if (rect.top >= viewport.bottom || rect.left >= viewport.right) break;
          const location = Object.freeze({ contentIdentity: identity, blockIndex: leaf.blockIndex, textLeafPath: Object.freeze([...leaf.path]), utf16Offset: offset });
          if (!best || rect.top < best.rect.top || (rect.top === best.rect.top && rect.left < best.rect.left)) best = { location, rect };
          break;
        }
      }
      return best;
    },
    demand(location): MarkdownTargetDemand {
      let released = false;
      const subscribers = new Set<() => void>();
      let groupElement: HTMLElement | null = null;
      let stopGroup = () => {};
      const scope = () => connectedLeaf(location);
      const stopHost = demandVisibleContent(host, scope);
      const entry = {
        location,
        notify() { for (const callback of [...subscribers]) callback(); },
        sync() {
          const next = groups.find(group => location.blockIndex >= group.start && location.blockIndex < group.end)?.element ?? null;
          if (next === groupElement) return;
          stopGroup(); groupElement = next;
          stopGroup = next && next !== host ? demandVisibleContent(next, scope) : () => {};
        },
        release() {
          if (released) return;
          released = true; demands.delete(entry); stopGroup(); stopHost(); subscribers.clear();
        }
      };
      demands.add(entry); entry.sync();
      return { read: () => measure(location), subscribe(callback) { subscribers.add(callback); return () => subscribers.delete(callback); }, release: entry.release };
    }
  };
  // Layout and reservation completion notify the viewport without a polling loop.
  const mutation = new MutationObserver(changed);
  mutation.observe(host, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-visible-content-restoring', 'data-markdown-rendering', 'aria-busy', 'data-visible-content-state'] });
  const resize = new ResizeObserver(changed); resize.observe(host);
  return {
    presentation,
    updateSource(next: string) {
      if (source === next) return;
      source = next; identity = null; failed = false; parsed = false; leaves.clear(); groups = [];
      const current = ++generation;
      markdownContentIdentity(next).then(value => { if (current === generation) { identity = value; changed(); } }).catch(() => { if (current === generation) { failed = true; changed(); } });
      changed();
    },
    mapText(node: Text, blockIndex: number, path: readonly number[]) { leaves.set(key(blockIndex, path), { node, blockIndex, path }); },
    begin() { parsed = false; failed = false; leaves.clear(); groups = []; changed(); },
    setGroups(next: Group[]) { groups = next; changed(); },
    rendered() { parsed = true; changed(); },
    failed() { failed = true; changed(); },
    prune() { for (const [address, leaf] of leaves) if (!host.contains(leaf.node)) leaves.delete(address); changed(); },
    destroy() { generation++; for (const demand of [...demands]) demand.release(); mutation.disconnect(); resize.disconnect(); leaves.clear(); groups = []; }
  };
}
export type MarkdownPresentationController = ReturnType<typeof createMarkdownPresentation>;
