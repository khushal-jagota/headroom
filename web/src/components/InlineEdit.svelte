<script lang="ts">
  import { onDestroy } from "svelte";
  import {
    editableMarkupChanged,
    editableMarkupSnapshot,
    handlePlainTextPaste,
    paintPlainEditable,
    readPlainEditable,
    refreshPlainEditableEmptyState
  } from "../lib/editableText";
  import {
    createManagedMarkdownSurface,
    type EditableManagedMarkdownSurface
  } from "../lib/managedMarkdown";
  import ErrorLine from "./ErrorLine.svelte";

  let {
    value = "",
    markdown = false,
    multiline = false,
    placeholder = "",
    ariaLabel = "",
    className = "",
    dataAttr = "",
    dataEdit = false,
    onCancel,
    onSave
  }: {
    value?: unknown;
    markdown?: boolean;
    multiline?: boolean;
    placeholder?: string;
    ariaLabel?: string;
    className?: string;
    dataAttr?: "day-focus" | "day-take-body" | "day-watch-body" | "day-lands-body" | "day-midday-body" | "";
    dataEdit?: boolean;
    onCancel?: () => unknown;
    onSave: (raw: string) => Promise<unknown>;
  } = $props();

  let el = $state<HTMLDivElement | null>(null);
  let editing = $state(false);
  let inFlight = $state(false);
  let error = $state<unknown>(null);
  let reverting = false;
  let editSnapshot = "";
  let markdownSurface: EditableManagedMarkdownSurface | null = null;
  let markdownSurfaceHost: HTMLDivElement | null = null;
  let activeMarkdownMode: boolean | null = null;
  let pendingMarkdownSave: string | null = null;

  function rawValue(): string {
    return value === null || value === undefined ? "" : String(value);
  }

  function selectMode(): boolean {
    if (!el) return false;
    if (
      activeMarkdownMode === markdown &&
      (!markdown || (markdownSurface !== null && markdownSurfaceHost === el))
    ) {
      return false;
    }
    markdownSurface?.destroy();
    markdownSurface = null;
    markdownSurfaceHost = null;
    pendingMarkdownSave = null;
    activeMarkdownMode = markdown;
    if (markdown) {
      markdownSurface = createManagedMarkdownSurface(el, { mode: "editable" });
      markdownSurfaceHost = el;
    }
    return true;
  }

  function paint(raw: unknown): void {
    if (!el) return;
    selectMode();
    if (markdown) markdownSurface?.update(raw);
    else paintPlainEditable(el, raw);
  }

  function enterEdit(): void {
    if (editing || inFlight) return;
    if (!el) return;
    editing = true;
    error = null;
    if (!markdown) editSnapshot = editableMarkupSnapshot(el);
  }

  async function commit(): Promise<void> {
    if (!editing || inFlight) return;
    const node = el;
    if (!node) return;
    let raw: string;
    if (markdown) {
      const surface = markdownSurface;
      if (!surface) return;
      if (surface.hasChanges()) raw = surface.read();
      else if (pendingMarkdownSave !== null) raw = pendingMarkdownSave;
      else {
        editing = false;
        return;
      }
    } else {
      if (!editableMarkupChanged(node, editSnapshot)) {
        editing = false;
        paint(rawValue());
        return;
      }
      raw = readPlainEditable(node);
    }
    if (raw === rawValue()) {
      pendingMarkdownSave = null;
      editing = false;
      paint(raw);
      return;
    }
    if (markdown) pendingMarkdownSave = raw;
    inFlight = true;
    error = null;
    try {
      await onSave(raw);
      if (markdown) pendingMarkdownSave = null;
      editing = false;
      paint(raw);
    } catch (err) {
      // The visible error is the retry boundary. Clear the save guard first so an
      // immediate focus and blur can resubmit the preserved Markdown source.
      inFlight = false;
      error = err;
      editing = true;
      if (markdown) pendingMarkdownSave = raw;
      paint(raw);
    } finally {
      inFlight = false;
    }
  }

  function onKeydown(event: KeyboardEvent): void {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      el?.blur();
      return;
    }
    if (!multiline && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      el?.blur();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      reverting = true;
      editing = false;
      error = null;
      pendingMarkdownSave = null;
      paint(onCancel ? onCancel() : rawValue());
      el?.blur();
    }
  }

  $effect(() => {
    const current = value;
    const node = el;
    if (!node) return;
    const modeChanged = selectMode();
    if (modeChanged || (!editing && !inFlight)) paint(current);
  });

  onDestroy(() => {
    markdownSurface?.destroy();
    markdownSurface = null;
    markdownSurfaceHost = null;
  });
</script>

<div
  bind:this={el}
  class={`ed ${className}`.trim()}
  contenteditable="true"
  role="textbox"
  aria-label={ariaLabel || undefined}
  aria-multiline={multiline}
  tabindex="0"
  data-ph={placeholder || undefined}
  data-edit={dataEdit ? "" : undefined}
  data-markdown-inline-edit={markdown ? "" : undefined}
  data-day-focus={dataAttr === "day-focus" ? "" : undefined}
  data-day-take-body={dataAttr === "day-take-body" ? "" : undefined}
  data-day-watch-body={dataAttr === "day-watch-body" ? "" : undefined}
  data-day-lands-body={dataAttr === "day-lands-body" ? "" : undefined}
  data-day-midday-body={dataAttr === "day-midday-body" ? "" : undefined}
  onfocus={enterEdit}
  onblur={(event) => {
    if (reverting) {
      reverting = false;
      return;
    }
    if (event.relatedTarget instanceof Node && el?.contains(event.relatedTarget)) return;
    void commit();
  }}
  onkeydown={onKeydown}
  oninput={() => {
    if (markdown) markdownSurface?.refreshEmptyState();
    else if (el) refreshPlainEditableEmptyState(el);
  }}
  onpaste={handlePlainTextPaste}
></div>
{#if error}
  <ErrorLine {error} />
{/if}
