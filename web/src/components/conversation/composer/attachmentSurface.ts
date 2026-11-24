/** Every composer receives files through the same surface, regardless of its layout. */
export interface ComposerAttachmentSurfaceOptions {
  disabled: boolean;
  onFiles: (files: File[]) => void;
  onDragChange?: (dragging: boolean) => void;
}

export function attachmentSurface(node: HTMLElement, initial: ComposerAttachmentSurfaceOptions) {
  let options = initial;
  let depth = 0;
  let dragging = false;
  node.setAttribute("data-composer-attachment-surface", "");

  function setDragging(next: boolean): void {
    if (dragging === next) return;
    dragging = next;
    options.onDragChange?.(next);
  }

  function hasFiles(event: DragEvent): boolean {
    const transfer = event.dataTransfer;
    return Boolean(transfer && (
      Array.from(transfer.types).includes("Files")
      || Array.from(transfer.items).some((item) => item.kind === "file")
      || transfer.files.length > 0
    ));
  }

  function enter(event: DragEvent): void {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (options.disabled) return;
    depth += 1;
    setDragging(true);
  }

  function over(event: DragEvent): void {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = options.disabled ? "none" : "copy";
    if (!options.disabled) setDragging(true);
  }

  function leave(event: DragEvent): void {
    if (!hasFiles(event) && depth === 0) return;
    event.stopPropagation();
    depth = Math.max(0, depth - 1);
    if (depth === 0) setDragging(false);
  }

  function drop(event: DragEvent): void {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    depth = 0;
    setDragging(false);
    if (!options.disabled) options.onFiles(Array.from(event.dataTransfer?.files ?? []));
  }

  function paste(event: ClipboardEvent): void {
    const files = Array.from(event.clipboardData?.files ?? []);
    if (files.length === 0) return;
    event.preventDefault();
    event.stopPropagation();
    if (!options.disabled) options.onFiles(files);
  }

  node.addEventListener("dragenter", enter);
  node.addEventListener("dragover", over);
  node.addEventListener("dragleave", leave);
  node.addEventListener("drop", drop);
  node.addEventListener("paste", paste);
  return {
    update(next: ComposerAttachmentSurfaceOptions): void {
      options = next;
      if (options.disabled) { depth = 0; setDragging(false); }
    },
    destroy(): void {
      node.removeEventListener("dragenter", enter);
      node.removeEventListener("dragover", over);
      node.removeEventListener("dragleave", leave);
      node.removeEventListener("drop", drop);
      node.removeEventListener("paste", paste);
      node.removeAttribute("data-composer-attachment-surface");
    }
  };
}
