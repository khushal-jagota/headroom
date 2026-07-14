import { mount, unmount } from "svelte";
import FilePreview from "../components/FilePreview.svelte";
import { targetFromHref } from "./filePreview";

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
    for (const anchor of Array.from(root.querySelectorAll("a[href]"))) {
      const href = anchor.getAttribute("href");
      if (!href) continue;
      const sourceToken = anchor.getAttribute("data-markdown-source-token");
      const target = targetFromHref(href, anchor.textContent || href);
      const slot = document.createElement("span");
      slot.className = "file-preview-slot";
      if (mode === "editable") {
        slot.contentEditable = "false";
        slot.dataset.markdownAtomicSlot = "true";
        if (sourceToken) slot.dataset.markdownSourceToken = sourceToken;
        anchor.replaceWith(caretGuard("before"), slot, caretGuard("after"));
      } else {
        anchor.replaceWith(slot);
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
    presentation: { emptyText: string; depth: number; visited: readonly string[] } | null
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

    const rendered = window.Planner?.markdown?.render?.(source);
    if (rendered) {
      rendered.classList.add("markdown-block");
      mountRenderedPreviews(
        rendered,
        presentation?.depth || 0,
        presentation?.visited || []
      );
      host.appendChild(rendered);
    } else if (mode === "read-only") {
      const fallback = document.createElement("div");
      fallback.className = "markdown markdown-block";
      fallback.textContent = source;
      host.appendChild(fallback);
    } else {
      host.textContent = source;
    }

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
          visited: [...input.visited]
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
      const raw = serializeBlockContainer(markdownBlock || host);
      setEmptyState(raw);
      return raw;
    },
    refreshEmptyState(): void {
      if (destroyed) return;
      const markdownBlock = directMarkdownBlock(host);
      setEmptyState(serializeBlockContainer(markdownBlock || host));
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

function editableText(value: string | null): string {
  return (value || "").replace(/\u200b/g, "").replace(/\u00a0/g, " ");
}

function serializeBlockContainer(parent: Node): string {
  const blocks: string[] = [];
  let text = "";
  for (const child of Array.from(parent.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      text += editableText(child.textContent);
      continue;
    }
    if (!(child instanceof HTMLElement)) continue;
    if (child.tagName === "BR") {
      text += "\n";
      continue;
    }
    const block = serializeBlockElement(child);
    if (block === null) {
      text += serializeInlineNode(child);
      continue;
    }
    if (text !== "") {
      blocks.push(text);
      text = "";
    }
    if (block !== "") blocks.push(block);
  }
  if (text !== "") blocks.push(text);
  return blocks.join("\n\n");
}

function serializeBlockElement(element: HTMLElement): string | null {
  const tag = element.tagName;
  if (tag === "H1" || tag === "H2" || tag === "H3") {
    return `${"#".repeat(Number(tag.slice(1)))} ${serializeInlineChildren(element)}`;
  }
  if (tag === "P") return serializeInlineChildren(element);
  if (tag === "UL") return serializeList(element, false);
  if (tag === "OL") return serializeList(element, true);
  if (tag === "PRE") return serializePre(element);
  if (tag === "DIV") {
    const nested = serializeBlockContainer(element);
    return nested || serializeInlineChildren(element);
  }
  return null;
}

function serializeList(element: HTMLElement, ordered: boolean): string {
  const items = Array.from(element.children).filter(
    (child): child is HTMLElement => child instanceof HTMLElement && child.tagName === "LI"
  );
  return items
    .map((item, index) => {
      const marker = ordered ? `${index + 1}. ` : "- ";
      return `${marker}${serializeInlineChildren(item).replace(/\n+/g, " ")}`;
    })
    .join("\n");
}

function serializePre(element: HTMLElement): string {
  const code = element.querySelector("code");
  const text = code ? code.textContent || "" : element.textContent || "";
  return `\`\`\`\n${text}\n\`\`\``;
}

function serializeInlineChildren(parent: Node): string {
  return Array.from(parent.childNodes).map(serializeInlineNode).join("");
}

function serializeInlineNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return editableText(node.textContent);
  if (!(node instanceof HTMLElement)) return "";
  if (node.hasAttribute("data-markdown-caret-guard")) return editableText(node.textContent);
  const atomicToken = node.getAttribute("data-markdown-source-token");
  if (node.getAttribute("data-markdown-atomic-slot") === "true" && atomicToken !== null) {
    return atomicToken;
  }
  const tag = node.tagName;
  if (tag === "BR") return "\n";
  if (tag === "STRONG" || tag === "B") return `**${serializeInlineChildren(node)}**`;
  if (tag === "EM" || tag === "I") return `*${serializeInlineChildren(node)}*`;
  if (tag === "CODE") {
    const text = node.textContent || "";
    return text.includes("`") ? text : `\`${text}\``;
  }
  if (tag === "A") {
    const text = serializeInlineChildren(node);
    const href = node.getAttribute("href");
    return href ? `[${text}](${href})` : text;
  }
  return serializeInlineChildren(node);
}
