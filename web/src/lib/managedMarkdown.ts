import { mount, unmount } from "svelte";
import FilePreview from "../components/FilePreview.svelte";
import { resolvePreview, targetFromHref } from "./filePreview";
import { renderMarkdownToElement, serializeMarkdownDomToSource } from "./markdownPipeline";
import { ticketDevServerHref } from "./ticketDevServerLink";

export type ReadOnlyManagedMarkdownInput = Readonly<{
  source: unknown;
  emptyText: string;
  depth: number;
  visited: readonly string[];
  ticketId?: string | null;
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
  ticketId: string | null;
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
  let destroyed = false;
  let observer: MutationObserver | null = null;
  let observerConnected = false;
  let lastReadOnlyInput: ReadOnlyRenderInput | null = null;
  let lastPaintedSource: string | typeof SOURCE_UNSET = SOURCE_UNSET;
  let dirtySincePaint = false;

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
  }

  function reconcileRemovedPreviews(): void {
    for (const preview of [...previews.values()]) {
      if (!host.contains(preview.slot)) unmountPreview(preview);
    }
  }

  function connectEditableObserver(): void {
    if (mode !== "editable" || previews.size === 0 || destroyed) return;
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
      // Links are claimed only when they name a managed ticket file; every other link
      // stays the anchor markdown rendered. Images are claimed wherever the preview
      // renders one, except inside a link that was left standing.
      const claimed = isImage
        ? !withinLink(renderedTarget, root) && resolvePreview(target).kind === "image"
        : target.kind === "ticket-file";
      if (!claimed) continue;
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

  function rewriteTicketDevServerLinks(root: HTMLElement, ticketId: string | null): void {
    if (ticketId === null) return;
    for (const anchor of Array.from(root.querySelectorAll("a[href]"))) {
      const href = anchor.getAttribute("href");
      if (href === null) continue;
      const rewritten = ticketDevServerHref(href, ticketId);
      if (rewritten !== href) anchor.setAttribute("href", rewritten);
    }
  }

  function repaint(
    source: string,
    presentation: {
      emptyText: string;
      depth: number;
      visited: readonly string[];
      ticketId?: string | null;
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
    if (mode === "read-only") {
      rewriteTicketDevServerLinks(rendered, presentation?.ticketId ?? null);
    }
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
          visited: [...input.visited],
          ticketId: input.ticketId ?? null
        };
        if (sameReadOnlyInput(lastReadOnlyInput, next)) return;
        repaint(next.source, next);
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
    previous.ticketId === next.ticketId &&
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
