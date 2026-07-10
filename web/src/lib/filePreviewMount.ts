import { mount, unmount } from "svelte";
import FilePreview from "../components/FilePreview.svelte";
import { targetFromHref } from "./filePreview";

type MountedPreview = {
  slot: HTMLElement;
  component: Record<string, unknown>;
};

type MountFilePreviewOptions = {
  editable?: boolean;
  depth?: number;
  visited?: string[];
};

const EDITABLE_CARET_GUARD = "\u200b";

function caretGuard(position: "before" | "after"): HTMLSpanElement {
  const guard = document.createElement("span");
  guard.dataset.markdownCaretGuard = position;
  guard.textContent = EDITABLE_CARET_GUARD;
  return guard;
}

export function mountFilePreviews(
  root: HTMLElement,
  { editable = false, depth = 0, visited = [] }: MountFilePreviewOptions = {}
): () => void {
  const mounted: MountedPreview[] = [];
  let observer: MutationObserver | null = null;

  function unmountSlot(slot: HTMLElement): void {
    const index = mounted.findIndex((preview) => preview.slot === slot);
    if (index < 0) return;
    const [preview] = mounted.splice(index, 1);
    void unmount(preview.component);
  }

  function unmountRemovedPreviews(node: Node): void {
    for (const preview of [...mounted]) {
      const removedSlot =
        node === preview.slot || (node instanceof HTMLElement && node.contains(preview.slot));
      if (removedSlot && !root.contains(preview.slot)) {
        unmountSlot(preview.slot);
      }
    }
  }

  for (const anchor of Array.from(root.querySelectorAll("a[href]"))) {
    const href = anchor.getAttribute("href");
    if (!href) continue;
    const sourceToken = anchor.getAttribute("data-markdown-source-token");
    const target = targetFromHref(href, anchor.textContent || href);
    const slot = document.createElement("span");
    slot.className = "file-preview-slot";
    if (editable) {
      slot.contentEditable = "false";
      slot.dataset.markdownAtomicSlot = "true";
      if (sourceToken) slot.dataset.markdownSourceToken = sourceToken;
      anchor.replaceWith(caretGuard("before"), slot, caretGuard("after"));
    } else {
      anchor.replaceWith(slot);
    }
    mounted.push({
      slot,
      component: mount(FilePreview, { target: slot, props: { target, depth, visited } })
    });
  }

  if (editable && mounted.length > 0) {
    observer = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of Array.from(record.removedNodes)) unmountRemovedPreviews(node);
      }
    });
    observer.observe(root, { childList: true, subtree: true });
  }

  return () => {
    observer?.disconnect();
    observer = null;
    for (const preview of [...mounted]) unmountSlot(preview.slot);
  };
}
