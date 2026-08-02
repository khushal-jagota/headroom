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
    visited = [],
    ticketId = null
  }: {
    text?: unknown;
    quiet?: string;
    depth?: number;
    visited?: string[];
    ticketId?: string | null;
  } = $props();
  let host = $state<HTMLDivElement | null>(null);
  let surface: ReadOnlyManagedMarkdownSurface | null = null;

  $effect(() => {
    if (!host) return;
    surface ??= createManagedMarkdownSurface(host, { mode: "read-only" });
    surface.update({ source: text, emptyText: quiet, depth, visited, ticketId });
  });

  // Only in a browser. Nothing is mounted when this is rendered on a server, so there is
  // nothing to tear down — and asking to be told about a teardown that cannot happen is
  // what stopped this component being server-rendered at all.
  if (typeof window !== "undefined") {
    onDestroy(() => {
      surface?.destroy();
      surface = null;
    });
  }
</script>

<div class="markdown-host" bind:this={host}></div>

<style>
  .markdown-host {
    display: contents;
  }
</style>
