<script lang="ts">
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
    onSave
  }: {
    value?: unknown;
    markdown?: boolean;
    multiline?: boolean;
    placeholder?: string;
    className?: string;
    dataAttr?: "day-focus" | "day-take-body" | "day-watch-body" | "day-lands-body" | "";
    onSave: (raw: string) => Promise<unknown>;
  } = $props();

  let el: HTMLDivElement;
  let editing = $state(false);
  let inFlight = $state(false);
  let error = $state<unknown>(null);
  let reverting = false;
  let editSnapshot = "";

  function rawValue(): string {
    return value === null || value === undefined ? "" : String(value);
  }

  function paint(raw: unknown): void {
    if (!el) return;
    if (markdown) paintMarkdownEditable(el, raw);
    else paintPlainEditable(el, raw);
  }

  function enterEdit(): void {
    if (editing || inFlight) return;
    editing = true;
    error = null;
    editSnapshot = editableMarkupSnapshot(el);
  }

  async function commit(): Promise<void> {
    if (!editing || inFlight) return;
    if (!editableMarkupChanged(el, editSnapshot)) {
      editing = false;
      paint(rawValue());
      return;
    }
    const raw = markdown ? readMarkdownEditable(el) : readPlainEditable(el);
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
      el.blur();
      return;
    }
    if (!multiline && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      el.blur();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      reverting = true;
      editing = false;
      error = null;
      paint(rawValue());
      el.blur();
    }
  }

  $effect(() => {
    const current = value;
    if (!editing && !inFlight) paint(current);
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
  data-day-focus={dataAttr === "day-focus" ? "" : undefined}
  data-day-take-body={dataAttr === "day-take-body" ? "" : undefined}
  data-day-watch-body={dataAttr === "day-watch-body" ? "" : undefined}
  data-day-lands-body={dataAttr === "day-lands-body" ? "" : undefined}
  onfocus={enterEdit}
  onblur={() => {
    if (reverting) {
      reverting = false;
      return;
    }
    void commit();
  }}
  onkeydown={onKeydown}
  oninput={() => refreshEditableEmptyState(el, markdown)}
  onpaste={handlePlainTextPaste}
></div>
{#if error}
  <ErrorLine {error} />
{/if}
