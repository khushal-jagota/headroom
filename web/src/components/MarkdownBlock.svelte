<script lang="ts">
  import { onDestroy } from "svelte";
  import { mountFilePreviews } from "../lib/filePreviewMount";

  let {
    text = "",
    quiet = "(none)",
    depth = 0,
    visited = []
  }: { text?: unknown; quiet?: string; depth?: number; visited?: string[] } = $props();
  let host: HTMLDivElement;
  let cleanupPreviews: (() => void) | null = null;

  function clearPreviewComponents(): void {
    cleanupPreviews?.();
    cleanupPreviews = null;
  }

  function renderMarkdown(value: unknown): void {
    if (!host) return;
    clearPreviewComponents();
    const raw = value === null || value === undefined ? "" : String(value);
    host.replaceChildren();
    if (!raw.trim()) {
      const empty = document.createElement("div");
      empty.className = "quiet-line";
      empty.textContent = quiet;
      host.appendChild(empty);
      return;
    }
    const rendered = window.Planner?.markdown?.render(raw);
    if (rendered) {
      rendered.classList.add("markdown-block");
      cleanupPreviews = mountFilePreviews(rendered, { depth, visited });
      host.appendChild(rendered);
      return;
    }
    const fallback = document.createElement("div");
    fallback.className = "markdown markdown-block";
    fallback.textContent = raw;
    host.appendChild(fallback);
  }

  $effect(() => {
    renderMarkdown(text);
  });

  onDestroy(clearPreviewComponents);
</script>

<div class="markdown-host" bind:this={host}></div>

<style>
  .markdown-host {
    display: contents;
  }
</style>
