<script lang="ts">
  import { onDestroy } from "svelte";
  import { MARKDOWN_WORKER_THRESHOLD } from "../lib/markdown/config";
  import { observeVisibleContent } from "../lib/visibleContent";
  import {
    createManagedMarkdownSurface,
    type ReadOnlyManagedMarkdownSurface
  } from "../lib/managedMarkdown";

  let {
    text = "",
    quiet = "(none)",
    depth = 0,
    visited = []
  }: {
    text?: unknown;
    quiet?: string;
    depth?: number;
    visited?: string[];
  } = $props();
  let host = $state<HTMLDivElement | null>(null);
  let surface: ReadOnlyManagedMarkdownSurface | null = null;
  let active = $state(false);
  let heldHeight = $state(160);
  const large = $derived(typeof text === "string" && text.length >= MARKDOWN_WORKER_THRESHOLD);

  $effect(() => {
    if (!host) return;
    if (!large) { active = true; return; }
    active = false;
    return observeVisibleContent(host, (visible, height) => {
      if (!visible && height > 0) heldHeight = height;
      active = visible;
    });
  });

  $effect(() => {
    if (!host) return;
    if (!active) {
      surface?.destroy(); surface = null; host.replaceChildren(); return;
    }
    surface ??= createManagedMarkdownSurface(host, { mode: "read-only" });
    surface.update({ source: text, emptyText: quiet, depth, visited });
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

<div class="markdown-host" class:markdown-host--large={large} style:min-height={large && !active ? `${heldHeight}px` : undefined} bind:this={host}></div>

<style>
  .markdown-host--large { display: block !important; }
  .markdown-host {
    display: contents;
  }
</style>
