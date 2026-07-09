<script lang="ts">
  import { mount, onDestroy, unmount } from "svelte";
  import { targetFromHref } from "../lib/filePreview";
  import FilePreview from "./FilePreview.svelte";

  let { text = "", quiet = "(none)" }: { text?: unknown; quiet?: string } = $props();
  let host: HTMLDivElement;
  let previewComponents: Record<string, any>[] = [];

  function clearPreviewComponents(): void {
    for (const component of previewComponents) {
      void unmount(component);
    }
    previewComponents = [];
  }

  function hydrateFilePreviews(root: HTMLElement): void {
    const anchors = Array.from(root.querySelectorAll("a[href]"));
    for (const anchor of anchors) {
      const href = anchor.getAttribute("href");
      if (!href) continue;
      const target = targetFromHref(href, anchor.textContent || href);
      const slot = document.createElement("span");
      slot.className = "file-preview-slot";
      anchor.replaceWith(slot);
      previewComponents.push(mount(FilePreview, { target: slot, props: { target } }));
    }
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
      hydrateFilePreviews(rendered);
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
