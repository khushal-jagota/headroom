import { mount, unmount } from "svelte";
import FilePreview from "../components/FilePreview.svelte";
import ManagedMarkdownRegion from "../components/ManagedMarkdownRegion.svelte";
import { resolvePreview, targetFromHref } from "./filePreview";
import { renderMarkdownToElement, renderParsedMarkdown, serializeMarkdownDomToSource } from "./markdownPipeline";
import { parseMarkdownInWorker } from "./markdown/workerClient";
import { MARKDOWN_GROUP_WEIGHT, MARKDOWN_WORKER_THRESHOLD } from "./markdown/config";
import type { MarkdownTreeNode, ParsedMarkdown } from "./markdown/contracts";
import { observeVisibleContent } from "./visibleContent";
import { registerMarkdownCopyRange } from "./markdown/copy";

export type ReadOnlyManagedMarkdownInput = Readonly<{
  source: unknown;
  emptyText: string;
  depth: number;
  visited: readonly string[];
}>;

export interface ReadOnlyManagedMarkdownSurface {
  readonly mode: "read-only";
  update(input: ReadOnlyManagedMarkdownInput): void;
  destroy(): void;
}

export interface EditableManagedMarkdownSurface {
  readonly mode: "editable";
  update(source: unknown): void;
  hasChanges(): boolean;
  read(): string;
  refreshEmptyState(): void;
  destroy(): void;
}

type MountedPreview = {
  slot: HTMLElement;
  component: Record<string, unknown>;
  unmounted: boolean;
};

type ReadOnlyRenderInput = {
  source: string;
  emptyText: string;
  depth: number;
  visited: string[];
};

type ManagedMarkdownMode = "read-only" | "editable";

const EDITABLE_CARET_GUARD = "\u200b";
const SOURCE_UNSET = Symbol("source-unset");

export function createManagedMarkdownSurface(
  host: HTMLElement,
  options: { mode: "read-only" }
): ReadOnlyManagedMarkdownSurface;

export function createManagedMarkdownSurface(
  host: HTMLElement,
  options: { mode: "editable" }
): EditableManagedMarkdownSurface;

export function createManagedMarkdownSurface(
  host: HTMLElement,
  options: { mode: ManagedMarkdownMode }
): ReadOnlyManagedMarkdownSurface | EditableManagedMarkdownSurface {
  const mode = options.mode;
  const previews = new Map<HTMLElement, MountedPreview>();
  const imageObservers = new Map<HTMLImageElement, () => void>();
  let destroyed = false;
  let observer: MutationObserver | null = null;
  let observerConnected = false;
  let lastReadOnlyInput: ReadOnlyRenderInput | null = null;
  let lastPaintedSource: string | typeof SOURCE_UNSET = SOURCE_UNSET;
  let dirtySincePaint = false;
  let parsing: AbortController | null = null;
  let stopVirtualDocument: (() => void) | null = null;
  let renderGeneration = 0;
  let documentRegion: ReturnType<typeof ManagedMarkdownRegion> | null = null;

  function cancelReadOnlyWork(): void {
    renderGeneration += 1;
    parsing?.abort(); parsing = null;
    stopVirtualDocument?.(); stopVirtualDocument = null;
    unmountAllPreviews();
    if (documentRegion) { void unmount(documentRegion); documentRegion = null; }
  }

  function renderLargeDocument(target: HTMLElement, document: ParsedMarkdown, presentation: ReadOnlyRenderInput): void {
    const root = globalThis.document.createElement("div");
    root.className = "markdown markdown-block";
    target.appendChild(root);
    const groups: { element: HTMLElement; nodes: MarkdownTreeNode[]; mounted: boolean }[] = [];
    const stops: (() => void)[] = [];
    let pending: MarkdownTreeNode[] = []; let weight = 0;
    const admit = () => {
      if (!pending.length) return;
      const nodes = pending; const size = weight; pending = []; weight = 0;
      const element = globalThis.document.createElement("div");
      element.dataset.markdownWindow = "";
      element.style.minHeight = `${Math.max(24, Math.ceil(size / 100) * 20)}px`;
      root.appendChild(element);
      const group = { element, nodes, mounted: false }; groups.push(group);
      stops.push(registerMarkdownCopyRange(element, () => group.mounted ? null : nodes.map(markdownText).join("")));
      stops.push(observeVisibleContent(element, (visible, measuredHeight) => {
        if (group.mounted === visible || destroyed) return;
        group.mounted = visible;
        if (visible) {
          const rendered = renderParsedMarkdown({ children: nodes, definitions: document.definitions });
          mountRenderedPreviews(rendered, presentation.depth, presentation.visited);
          element.style.minHeight = ""; element.replaceChildren(rendered);
        } else {
          for (const preview of [...previews.values()]) {
            if (element.contains(preview.slot)) unmountPreview(preview);
          }
          element.style.minHeight = `${Math.max(24, measuredHeight)}px`;
          for (const [image, stop] of imageObservers) {
            if (element.contains(image)) { stop(); imageObservers.delete(image); }
          }
          element.replaceChildren();
        }
      }));
    };
    for (const node of document.children) {
      const size = markdownWeight(node);
      if (weight && weight + size > MARKDOWN_GROUP_WEIGHT) admit();
      pending.push(node); weight += size;
    }
    admit();
    stopVirtualDocument = () => {
      stops.forEach(stop => stop());
      groups.length = 0;
    };
  }

  function updateReadOnly(next: ReadOnlyRenderInput): void {
    cancelReadOnlyWork();
    if (next.source.length < MARKDOWN_WORKER_THRESHOLD) { repaint(next.source, next); return; }
    disconnectObserver(); unmountAllPreviews(); host.replaceChildren();
    const region = mount(ManagedMarkdownRegion, { target: host });
    documentRegion = region;
    const controller = new AbortController(); parsing = controller;
    const generation = renderGeneration;
    parseMarkdownInWorker(next.source, controller.signal).then(document => {
      if (destroyed || controller.signal.aborted || generation !== renderGeneration) return;
      parsing = null;
      region.reveal(target => {
        if (destroyed || generation !== renderGeneration) return;
        renderLargeDocument(target, document, next);
      });
    }).catch(error => {
      if (destroyed || controller.signal.aborted || generation !== renderGeneration) return;
      parsing = null;
      region.reveal(target => {
        if (destroyed || generation !== renderGeneration) return;
        const message = globalThis.document.createElement("div");
        message.className = "quiet-line";
        message.textContent = error instanceof Error ? error.message : "Text could not load.";
        target.appendChild(message);
      });
    });
  }

  const onEditableInput = (): void => {
    if (!destroyed) dirtySincePaint = true;
  };

  if (mode === "editable") host.addEventListener("input", onEditableInput);

  function normalizeSource(source: unknown): string {
    return source === null || source === undefined ? "" : String(source);
  }

  function setEmptyState(raw: string): void {
    if (raw.trim()) host.removeAttribute("data-empty");
    else host.setAttribute("data-empty", "true");
  }

  function caretGuard(position: "before" | "after"): HTMLSpanElement {
    const guard = document.createElement("span");
    guard.dataset.markdownCaretGuard = position;
    guard.textContent = EDITABLE_CARET_GUARD;
    return guard;
  }

  function unmountPreview(preview: MountedPreview): void {
    if (preview.unmounted) return;
    preview.unmounted = true;
    previews.delete(preview.slot);
    void unmount(preview.component);
  }

  function unmountAllPreviews(): void {
    for (const preview of [...previews.values()]) unmountPreview(preview);
    for (const stop of imageObservers.values()) stop();
    imageObservers.clear();
  }

  function reconcileRemovedPreviews(): void {
    for (const [image, stop] of imageObservers) {
      if (!host.contains(image)) { stop(); imageObservers.delete(image); }
    }
    for (const preview of [...previews.values()]) {
      if (!host.contains(preview.slot)) unmountPreview(preview);
    }
  }

  function connectEditableObserver(): void {
    if (mode !== "editable" || (previews.size === 0 && imageObservers.size === 0) || destroyed) return;
    if (observer === null) {
      observer = new MutationObserver(() => reconcileRemovedPreviews());
    }
    observer.observe(host, { childList: true, subtree: true });
    observerConnected = true;
  }

  function disconnectObserver(): void {
    if (observer === null || !observerConnected) return;
    observer.disconnect();
    observerConnected = false;
  }

  function mountRenderedPreviews(
    root: HTMLElement,
    depth: number,
    visited: readonly string[]
  ): void {
    for (const renderedTarget of Array.from(root.querySelectorAll("a[href], img[src]"))) {
      if (!root.contains(renderedTarget)) continue;
      const isImage = renderedTarget.tagName === "IMG";
      const href = renderedTarget.getAttribute(isImage ? "src" : "href");
      if (!href) continue;
      const sourceToken = renderedTarget.getAttribute("data-markdown-source-token");
      const referenceIdentifier = renderedTarget.getAttribute(
        "data-markdown-reference-identifier"
      );
      const label = isImage
        ? renderedTarget.getAttribute("alt") || href
        : renderedTarget.textContent || href;
      const target = targetFromHref(href, label);
      // Links are claimed when they name a managed ticket file. Every other link stays
      // the anchor Markdown rendered. Images are claimed wherever the preview renders
      // one, except inside a link that was left standing.
      const claimed = isImage
        ? !withinLink(renderedTarget, root) && resolvePreview(target).kind === "image"
        : target.kind === "ticket-file";
      if (!claimed) {
        if (renderedTarget instanceof HTMLImageElement) {
          const image = renderedTarget;
          image.removeAttribute("src"); image.loading = "eager"; image.style.minHeight = "160px";
          const stop = observeVisibleContent(image, (visible, height) => {
            if (visible) { image.style.minHeight = ""; image.src = href; }
            else { image.style.minHeight = `${Math.max(1, height)}px`; image.removeAttribute("src"); }
          });
          imageObservers.set(image, () => { stop(); image.removeAttribute("src"); });
        }
        continue;
      }
      const slot = document.createElement("span");
      slot.className = "file-preview-slot";
      if (mode === "editable") {
        slot.contentEditable = "false";
        slot.dataset.markdownAtomicSlot = "true";
        if (sourceToken) slot.dataset.markdownSourceToken = sourceToken;
        if (referenceIdentifier) {
          slot.dataset.markdownReferenceIdentifier = referenceIdentifier;
        }
        renderedTarget.replaceWith(caretGuard("before"), slot, caretGuard("after"));
      } else {
        renderedTarget.replaceWith(slot);
      }
      const preview: MountedPreview = {
        slot,
        component: mount(FilePreview, {
          target: slot,
          props: { target, depth, visited: [...visited] }
        }),
        unmounted: false
      };
      previews.set(slot, preview);
    }
  }

  function repaint(
    source: string,
    presentation: {
      emptyText: string;
      depth: number;
      visited: readonly string[];
    } | null
  ): void {
    disconnectObserver();
    unmountAllPreviews();
    host.replaceChildren();

    if (!source.trim()) {
      if (mode === "read-only") {
        const empty = document.createElement("div");
        empty.className = "quiet-line";
        empty.textContent = presentation?.emptyText || "";
        host.appendChild(empty);
      } else {
        setEmptyState(source);
      }
      return;
    }

    const rendered = renderMarkdownToElement(source);
    rendered.classList.add("markdown-block");
    mountRenderedPreviews(
      rendered,
      presentation?.depth || 0,
      presentation?.visited || []
    );
    host.appendChild(rendered);

    if (mode === "editable") {
      setEmptyState(source);
      connectEditableObserver();
    }
  }

  function destroy(): void {
    if (destroyed) return;
    destroyed = true;
    cancelReadOnlyWork();
    if (mode === "editable") host.removeEventListener("input", onEditableInput);
    disconnectObserver();
    observer = null;
    unmountAllPreviews();
    lastReadOnlyInput = null;
    lastPaintedSource = SOURCE_UNSET;
    dirtySincePaint = false;
  }

  if (mode === "read-only") {
    return {
      mode,
      update(input: ReadOnlyManagedMarkdownInput): void {
        if (destroyed) return;
        const next: ReadOnlyRenderInput = {
          source: normalizeSource(input.source),
          emptyText: input.emptyText,
          depth: input.depth,
          visited: [...input.visited]
        };
        if (sameReadOnlyInput(lastReadOnlyInput, next)) return;
        updateReadOnly(next);
        lastReadOnlyInput = next;
      },
      destroy
    };
  }

  return {
    mode,
    update(source: unknown): void {
      if (destroyed) return;
      const normalized = normalizeSource(source);
      if (!dirtySincePaint && lastPaintedSource === normalized) return;
      repaint(normalized, null);
      lastPaintedSource = normalized;
      dirtySincePaint = false;
    },
    hasChanges(): boolean {
      return !destroyed && dirtySincePaint;
    },
    read(): string {
      if (destroyed) return "";
      const markdownBlock = directMarkdownBlock(host);
      const raw = serializeMarkdownDomToSource(markdownBlock || host);
      setEmptyState(raw);
      return raw;
    },
    refreshEmptyState(): void {
      if (destroyed) return;
      const markdownBlock = directMarkdownBlock(host);
      setEmptyState(serializeMarkdownDomToSource(markdownBlock || host));
    },
    destroy
  };
}

function sameReadOnlyInput(
  previous: ReadOnlyRenderInput | null,
  next: ReadOnlyRenderInput
): boolean {
  return (
    previous !== null &&
    previous.source === next.source &&
    previous.emptyText === next.emptyText &&
    previous.depth === next.depth &&
    previous.visited.length === next.visited.length &&
    previous.visited.every((value, index) => value === next.visited[index])
  );
}

function withinLink(element: Element, root: HTMLElement): boolean {
  let node: Node | null = element.parentNode;
  while (node && node !== root) {
    if (node instanceof HTMLElement && node.tagName === "A") return true;
    node = node.parentNode;
  }
  return false;
}

function directMarkdownBlock(host: HTMLElement): HTMLElement | null {
  const meaningful = Array.from(host.childNodes).filter((node) => {
    if (node.nodeType === Node.TEXT_NODE) return Boolean(node.textContent);
    return node.nodeType === Node.ELEMENT_NODE;
  });
  if (meaningful.length !== 1) return null;
  const only = meaningful[0];
  if (!(only instanceof HTMLElement)) return null;
  return only.classList.contains("markdown-block") ? only : null;
}

function markdownWeight(node: MarkdownTreeNode): number {
  return 40 + (node.value?.length || 0) + (node.children || []).reduce((sum, child) => sum + markdownWeight(child), 0);
}
function markdownText(node: MarkdownTreeNode): string {
  if (node.type === "text") return node.value || "";
  if (node.tagName === "br") return "\n";
  if (node.tagName === "img") return String(node.properties?.alt || "");
  const text = (node.children || []).map(markdownText).join("");
  if (node.tagName === "td" || node.tagName === "th") return text + "\t";
  return /^(p|h[1-6]|li|pre|tr|blockquote)$/.test(node.tagName || "") ? text + "\n" : text;
}
