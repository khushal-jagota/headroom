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
  let renderedInput: {
    text: string;
    quiet: string;
    depth: number;
    visited: string[];
  } | null = null;

  function clearPreviewComponents(): void {
    cleanupPreviews?.();
    cleanupPreviews = null;
  }

  function renderMarkdown(
    raw: string,
    emptyText: string,
    previewDepth: number,
    previewVisited: string[]
  ): void {
    if (!host) return;
    clearPreviewComponents();
    host.replaceChildren();
    if (!raw.trim()) {
      const empty = document.createElement("div");
      empty.className = "quiet-line";
      empty.textContent = emptyText;
      host.appendChild(empty);
      return;
    }
    const rendered = window.Planner?.markdown?.render(raw);
    if (rendered) {
      rendered.classList.add("markdown-block");
      cleanupPreviews = mountFilePreviews(rendered, {
        depth: previewDepth,
        visited: previewVisited
      });
      host.appendChild(rendered);
      return;
    }
    const fallback = document.createElement("div");
    fallback.className = "markdown markdown-block";
    fallback.textContent = raw;
    host.appendChild(fallback);
  }

  $effect(() => {
    const nextInput = {
      text: text === null || text === undefined ? "" : String(text),
      quiet,
      depth,
      visited: [...visited]
    };
    if (
      renderedInput?.text === nextInput.text &&
      renderedInput.quiet === nextInput.quiet &&
      renderedInput.depth === nextInput.depth &&
      renderedInput.visited.length === nextInput.visited.length &&
      renderedInput.visited.every((value, index) => value === nextInput.visited[index])
    ) {
      return;
    }
    renderedInput = nextInput;
    renderMarkdown(nextInput.text, nextInput.quiet, nextInput.depth, nextInput.visited);
  });

  onDestroy(clearPreviewComponents);
</script>

<div class="markdown-host" bind:this={host}></div>

<style>
  .markdown-host {
    display: contents;
  }
</style>
