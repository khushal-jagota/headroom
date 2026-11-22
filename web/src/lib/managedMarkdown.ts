import type { MarkdownPresentationController } from "./markdown/textLocation";
import { mount, unmount } from "svelte";
import ChatChart from "./markdown/charts/ChatChart.svelte";
import FilePreview from "../components/FilePreview.svelte";
import ManagedMarkdownRegion from "../components/ManagedMarkdownRegion.svelte";
import { resolvePreview, targetFromHref } from "./filePreview";
import { renderMarkdownToElement, renderParsedMarkdown, markdownChartForElement, serializeMarkdownDomToSource } from "./markdownPipeline";
import { parseMarkdownInWorker } from "./markdown/workerClient";
import { MARKDOWN_GROUP_WEIGHT, MARKDOWN_WORKER_THRESHOLD } from "./markdown/config";
import type { MarkdownTreeNode, ParsedMarkdown } from "./markdown/contracts";
import { observeVisibleContent, holdVisibleContentHeight, resetVisibleContentHeight } from "./visibleContent";
import { registerMarkdownCopyRange } from "./markdown/copy";
import { presentCodeBlock } from "./markdown/codeBlocks";

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
  stopCopy?: () => void;
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
let nextChartIdentity = 0;

/** Whether Markdown drawn in `host` presents its code blocks (a header with the language and
 *  Copy, and syntax colour). Only the conversation's transcript does: artifacts, file
 *  previews and pages are out of the conversation's design and keep their own code. */
export function presentsCodeBlocks(host: Pick<Element, "closest">): boolean {
  return host.closest(".chat-thread") !== null;
}

export function createManagedMarkdownSurface(
  host: HTMLElement,
  options: { mode: "read-only"; onRendered?: () => void; presentation?: MarkdownPresentationController }
): ReadOnlyManagedMarkdownSurface;

export function createManagedMarkdownSurface(
  host: HTMLElement,
  options: { mode: "editable" }
): EditableManagedMarkdownSurface;

export function createManagedMarkdownSurface(
  host: HTMLElement,
  options: { mode: ManagedMarkdownMode; onRendered?: () => void; presentation?: MarkdownPresentationController }
): ReadOnlyManagedMarkdownSurface | EditableManagedMarkdownSurface {
  const mode = options.mode;
  const previews = new Map<HTMLElement, MountedPreview>();
  const imageObservers = new Map<HTMLImageElement, () => void>();
  /** Code blocks given a header and colour, and how to take each off again. */
  const codeBlocks = new Map<HTMLElement, () => void>();
  let destroyed = false;
  let observer: MutationObserver | null = null;
  let observerConnected = false;
  let lastReadOnlyInput: ReadOnlyRenderInput | null = null;
  let lastPaintedSource: string | typeof SOURCE_UNSET = SOURCE_UNSET;
  let dirtySincePaint = false;
  let parsing: AbortController | null = null;
  let stopVirtualDocument: (() => void) | null = null;
  let renderGeneration = 0;
  let rendering = false;
  function rendered(): void { rendering = false; options.onRendered?.(); options.presentation?.rendered(); }
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
    const groups: { element: HTMLElement; nodes: MarkdownTreeNode[]; mounted: boolean; start: number; end: number }[] = [];
    let blockIndex = 0;
    const stops: (() => void)[] = [];
    let pending: MarkdownTreeNode[] = []; let weight = 0;
    const admit = () => {
      if (!pending.length) return;
      const nodes = pending; const size = weight; pending = []; weight = 0;
      const element = globalThis.document.createElement("div");
      element.dataset.markdownWindow = "";
      element.style.minHeight = `${Math.max(24, Math.ceil(size / 100) * 20)}px`;
      root.appendChild(element);
      const group = { element, nodes, mounted: false, start: blockIndex, end: blockIndex + nodes.length }; groups.push(group);
      blockIndex += nodes.length;
      stops.push(registerMarkdownCopyRange(element, () => group.mounted ? null : nodes.map(markdownText).join("")));
      stops.push(observeVisibleContent(element, visible => {
        if (group.mounted === visible || destroyed) return;
        group.mounted = visible;
        if (visible) {
          const rendered = renderParsedMarkdown({ children: nodes, definitions: document.definitions }, options.presentation?.mapText, group.start, { fileReferences: presentsCodeBlocks(host) });
          mountRenderedPreviews(rendered, presentation.depth, presentation.visited);
          if (!element.hasAttribute("data-visible-content-restoring")) element.style.minHeight = "";
          element.replaceChildren(rendered);
          options.presentation?.prune();
        } else {
          for (const preview of [...previews.values()]) {
            if (element.contains(preview.slot)) unmountPreview(preview);
          }
          for (const [image, stop] of imageObservers) {
            if (element.contains(image)) { stop(); imageObservers.delete(image); }
          }
          for (const [frame, stop] of codeBlocks) {
            if (element.contains(frame)) { stop(); codeBlocks.delete(frame); }
          }
          element.replaceChildren();
          options.presentation?.prune();
        }
      }));
    };
    for (const node of document.children) {
      const size = markdownWeight(node);
      if (weight && weight + size > MARKDOWN_GROUP_WEIGHT) admit();
      pending.push(node); weight += size;
    }
    admit();
    options.presentation?.setGroups(groups);
    stopVirtualDocument = () => {
      stops.forEach(stop => stop());
      groups.length = 0;
    };
  }

  function updateReadOnly(next: ReadOnlyRenderInput): void {
    rendering = true;
    options.presentation?.begin();
    if (next.source.length < MARKDOWN_WORKER_THRESHOLD) {
      cancelReadOnlyWork();
      resetVisibleContentHeight(host);
      repaint(next.source, next);
      rendered();
      return;
    }
    // A source refresh keeps its painted document while the replacement parses.
    // New targets mount a new surface and use the ordinary cold placeholder.
    const painted = Boolean(host.querySelector(".markdown-block"));
    renderGeneration += 1;
    parsing?.abort();
    if (!painted) {
      cancelReadOnlyWork();
      disconnectObserver(); host.replaceChildren();
      documentRegion = mount(ManagedMarkdownRegion, { target: host });
    }
    const controller = new AbortController(); parsing = controller;
    let generation = renderGeneration;
    function reveal(render: (target: HTMLElement) => void): void {
      if (painted) {
        holdVisibleContentHeight(host);
        cancelReadOnlyWork();
        generation = renderGeneration;
        disconnectObserver(); host.replaceChildren();
        documentRegion = mount(ManagedMarkdownRegion, { target: host });
      }
      documentRegion?.reveal((target: HTMLElement) => {
        if (destroyed || generation !== renderGeneration) return;
        render(target);
        rendered();
      });
    }
    parseMarkdownInWorker(next.source, controller.signal).then(document => {
      if (destroyed || controller.signal.aborted || generation !== renderGeneration) return;
      parsing = null;
      reveal(target => renderLargeDocument(target, document, next));
    }).catch(error => {
      if (destroyed || controller.signal.aborted || generation !== renderGeneration) return;
      parsing = null;
      options.presentation?.failed();
      // Keep available text on a failed refresh. The error remains visible and
      // causes a later viewport departure to release this failed view for retry.
      const message = globalThis.document.createElement("div");
      message.className = "quiet-line";
      message.dataset.visibleContentFailed = "";
      message.textContent = error instanceof Error ? error.message : "Text could not load.";
      if (painted) { host.appendChild(message); rendered(); }
      else reveal(target => target.appendChild(message));
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
    preview.stopCopy?.();
    void unmount(preview.component);
  }

  function unmountAllPreviews(): void {
    for (const preview of [...previews.values()]) unmountPreview(preview);
    for (const stop of imageObservers.values()) stop();
    imageObservers.clear();
    for (const stop of codeBlocks.values()) stop();
    codeBlocks.clear();
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
    if (mode === "read-only") {
      for (const code of root.querySelectorAll<HTMLElement>("pre")) {
        const chart = markdownChartForElement(code);
        if (!chart) continue;
        const slot = document.createElement("div");
        slot.className = "chat-chart-slot";
        code.replaceWith(slot);
        const stopCopy = registerMarkdownCopyRange(slot, () => chart.source + "\n\n");
        previews.set(slot, {
          slot, stopCopy, unmounted: false,
          component: mount(ChatChart, {
            target: slot,
            props: { block: chart.block, identity: `chat-${++nextChartIdentity}` }
          })
        });
      }
      // What is left is code. In the conversation each block gets its language, a copy
      // button and colour; an artifact or a page renders its code exactly as it always has.
      if (presentsCodeBlocks(host)) for (const pre of Array.from(root.querySelectorAll<HTMLElement>("pre"))) {
        const { frame, stop } = presentCodeBlock(pre);
        codeBlocks.set(frame, stop);
      }
    }
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
      // Links are claimed when they name a managed ticket, Manager or goal file. Other
      // links stay the anchor Markdown rendered. Images are claimed wherever the preview
      // renders one, except inside a link that was left standing.
      const claimed = isImage
        ? !withinLink(renderedTarget, root) && resolvePreview(target).kind === "image"
        : target.kind === "ticket-file" ||
          target.kind === "manager-file" ||
          target.kind === "goal-file";
      if (!claimed) {
        if (renderedTarget instanceof HTMLImageElement) {
          const image = renderedTarget;
          image.removeAttribute("src"); image.loading = "eager";
          const stop = observeVisibleContent(image, visible => {
            if (visible) image.src = href;
            else image.removeAttribute("src");
          });
          imageObservers.set(image, () => { stop(); image.removeAttribute("src"); });
        }
        continue;
      }
      const slot = document.createElement("span");
      slot.className = "file-preview-slot";
      const previewKind = resolvePreview(target).kind;
      if (previewKind === "image" || previewKind === "video") slot.dataset.mediaSlot = "";
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
          // A bare URL's text is the URL itself, so only written words are kept.
          props: { target, depth, visited: [...visited],
            linkText: !isImage && label !== href ? label : undefined }
        }),
        unmounted: false
      };
      previews.set(slot, preview);
    }
    if (mode === "read-only") groupAdjacentMedia(root);
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

    const rendered = renderMarkdownToElement(source, options.presentation?.mapText, { fileReferences: mode === "read-only" && presentsCodeBlocks(host) });
    rendered.classList.add("markdown-block");
    mountRenderedPreviews(
      rendered,
      presentation?.depth || 0,
      presentation?.visited || []
    );
    host.appendChild(rendered);
    options.presentation?.setGroups([{ element: host, start: 0, end: rendered.childNodes.length }]);
    options.presentation?.prune();

    if (mode === "editable") {
      setEmptyState(source);
      connectEditableObserver();
    }
  }

  function destroy(): void {
    if (destroyed) return;
    destroyed = true;
    cancelReadOnlyWork();
    options.presentation?.begin();
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
        if (sameReadOnlyInput(lastReadOnlyInput, next)) { if (!rendering) rendered(); return; }
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

/** Pictures and videos written next to each other, with only space or a line break
 * between them, are drawn together as a grid. The grid holds the same slots. */
function groupAdjacentMedia(root: HTMLElement): void {
  const isMedia = (node: Node | null): node is HTMLElement =>
    node instanceof HTMLElement && node.dataset.mediaSlot !== undefined;
  const isGap = (node: Node | null) =>
    node !== null && ((node.nodeType === Node.TEXT_NODE && !node.textContent?.trim())
      || (node instanceof HTMLElement && node.tagName === "BR"));
  for (const first of Array.from(root.querySelectorAll<HTMLElement>("[data-media-slot]"))) {
    if (first.parentElement?.classList.contains("file-preview-grid")) continue;
    let last: HTMLElement = first;
    let count = 1;
    for (let next = first.nextSibling; next && (isGap(next) || isMedia(next)); next = next.nextSibling) {
      if (isMedia(next)) { last = next; count += 1; }
    }
    if (count < 2) continue;
    const grid = document.createElement("span");
    grid.className = "file-preview-grid";
    first.before(grid);
    for (let node: Node | null = first; node; ) {
      const following: Node | null = node === last ? null : node.nextSibling;
      grid.appendChild(node);
      node = following;
    }
  }
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
  if (node.chart) return node.chart.source + "\n\n";
  if (node.type === "text") return node.value || "";
  if (node.tagName === "br") return "\n";
  if (node.tagName === "img") return String(node.properties?.alt || "");
  const text = (node.children || []).map(markdownText).join("");
  if (node.tagName === "td" || node.tagName === "th") return text + "\t";
  return /^(p|h[1-6]|li|pre|tr|blockquote)$/.test(node.tagName || "") ? text + "\n" : text;
}
