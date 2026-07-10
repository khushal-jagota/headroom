<script lang="ts">
  import { onDestroy } from "svelte";
  import {
    editableMarkupChanged,
    editableMarkupSnapshot,
    handlePlainTextPaste,
    paintMarkdownEditable,
    paintPlainEditable,
    readMarkdownEditable,
    readPlainEditable,
    refreshEditableEmptyState
  } from "../lib/markdownEdit";
  import ErrorLine from "./ErrorLine.svelte";

  let {
    value = "",
    markdown = false,
    multiline = false,
    placeholder = "",
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
    className?: string;
    dataAttr?: "day-focus" | "day-take-body" | "day-watch-body" | "day-lands-body" | "";
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
  let markdownDirty = false;
  let cleanupMarkdownPreviews: (() => void) | null = null;

  function rawValue(): string {
    return value === null || value === undefined ? "" : String(value);
  }

  function paint(raw: unknown): void {
    if (!el) return;
    cleanupMarkdownPreviews?.();
    cleanupMarkdownPreviews = null;
    if (markdown) cleanupMarkdownPreviews = paintMarkdownEditable(el, raw);
    else paintPlainEditable(el, raw);
  }

  function enterEdit(): void {
    if (editing || inFlight) return;
    if (!el) return;
    editing = true;
    error = null;
    if (markdown) {
      markdownDirty = false;
    } else {
      editSnapshot = editableMarkupSnapshot(el);
    }
  }

  async function commit(): Promise<void> {
    if (!editing || inFlight) return;
    const node = el;
    if (!node) return;
    let raw: string;
    if (markdown) {
      if (!markdownDirty) {
        editing = false;
        return;
      }
      raw = readMarkdownEditable(node);
    } else {
      if (!editableMarkupChanged(node, editSnapshot)) {
        editing = false;
        paint(rawValue());
        return;
      }
      raw = readPlainEditable(node);
    }
    if (raw === rawValue()) {
      editing = false;
      paint(raw);
      return;
    }
    inFlight = true;
    error = null;
    try {
      await onSave(raw);
      editing = false;
      paint(raw);
    } catch (err) {
      error = err;
      editing = true;
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
      paint(onCancel ? onCancel() : rawValue());
      el?.blur();
    }
  }

  $effect(() => {
    const current = value;
    if (!editing && !inFlight) paint(current);
  });

  onDestroy(() => {
    cleanupMarkdownPreviews?.();
    cleanupMarkdownPreviews = null;
  });
</script>

<div
  bind:this={el}
  class={`ed ${className}`.trim()}
  contenteditable="true"
  role="textbox"
  aria-multiline={multiline}
  tabindex="0"
  data-ph={placeholder || undefined}
  data-edit={dataEdit ? "" : undefined}
  data-markdown-inline-edit={markdown ? "" : undefined}
  data-day-focus={dataAttr === "day-focus" ? "" : undefined}
  data-day-take-body={dataAttr === "day-take-body" ? "" : undefined}
  data-day-watch-body={dataAttr === "day-watch-body" ? "" : undefined}
  data-day-lands-body={dataAttr === "day-lands-body" ? "" : undefined}
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
    if (markdown) markdownDirty = true;
    if (el) refreshEditableEmptyState(el, markdown);
  }}
  onpaste={handlePlainTextPaste}
></div>
{#if error}
  <ErrorLine {error} />
{/if}
