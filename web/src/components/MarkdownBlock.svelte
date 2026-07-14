<script lang="ts">
  import { onDestroy } from "svelte";
  import {
    createManagedMarkdownSurface,
    type ReadOnlyManagedMarkdownSurface
  } from "../lib/managedMarkdown";

  let {
    text = "",
    quiet = "(none)",
    depth = 0,
    visited = []
  }: { text?: unknown; quiet?: string; depth?: number; visited?: string[] } = $props();
  let host = $state<HTMLDivElement | null>(null);
  let surface: ReadOnlyManagedMarkdownSurface | null = null;

  $effect(() => {
    if (!host) return;
    surface ??= createManagedMarkdownSurface(host, { mode: "read-only" });
    surface.update({ source: text, emptyText: quiet, depth, visited });
  });

  onDestroy(() => {
    surface?.destroy();
    surface = null;
  });
</script>

<div class="markdown-host" bind:this={host}></div>

<style>
  .markdown-host {
    display: contents;
  }
</style>
