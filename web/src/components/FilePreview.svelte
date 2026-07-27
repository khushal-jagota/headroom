<script lang="ts">
  import { onDestroy } from "svelte";
  import type { FilePreviewTarget } from "../lib/filePreview";
  import {
    MANAGED_HTML_PREVIEW_SANDBOX,
    markdownExpansionFor,
    prepareManagedHtmlPreviewDocument,
    resolvePreview
  } from "../lib/filePreview";
  import MarkdownBlock from "./MarkdownBlock.svelte";

  let {
    target,
    mode = "embedded",
    depth = 0,
    visited = []
  }: {
    target: FilePreviewTarget;
    mode?: "embedded" | "full";
    depth?: number;
    visited?: string[];
  } = $props();

  let text = $state<string | null>(null);
  let error = $state("");
  let htmlFrame = $state<HTMLIFrameElement | null>(null);
  let resolved = $derived(resolvePreview(target));
  let expansion = $derived(markdownExpansionFor(resolved, depth, visited));
  let shouldFetchText = $derived(
    (resolved.kind === "markdown" && expansion.expandable) || resolved.kind === "html"
  );
  let showsMedia = $derived(
    resolved.kind === "image" || resolved.kind === "video" || resolved.kind === "audio"
  );
  let showsDocument = $derived(shouldFetchText && !error);
  let inline = $derived(!showsMedia && !showsDocument);

  $effect(() => {
    const current = resolved;
    text = null;
    error = "";
    if (!shouldFetchText) return;
    const controller = new AbortController();
    fetch(current.href, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`file fetch failed: ${response.status}`);
        return response.text();
      })
      .then((body) => {
        text =
          current.kind === "html"
            ? prepareManagedHtmlPreviewDocument(body, current.href)
            : body;
      })
      .catch((err) => {
        if (!controller.signal.aborted) error = err instanceof Error ? err.message : String(err);
      });
    return () => controller.abort();
  });

  $effect(() => {
    if (resolved.kind === "html" && htmlFrame) {
      htmlFrame.srcdoc = text || "";
    }
  });

  onDestroy(() => {
    if (htmlFrame) htmlFrame.srcdoc = "";
  });
</script>

{#snippet openLink(href: string | undefined)}
  <!-- Opened here rather than in a new tab. One thing at a time is easier to follow and
       easier to leave — back is always the way out — and it is the same on a phone, where
       a second tab is a place you have to go and find. -->
  <a class="file-preview-link" {href} rel="noopener noreferrer">Open {resolved.label}</a>
{/snippet}

<div
  class={`file-preview file-preview--${mode}${inline ? " file-preview--inline" : ""}`}
  data-file-preview
  data-file-preview-kind={resolved.kind}
>
  {#if resolved.kind === "image"}
    <img class="file-preview-image" src={resolved.href} alt={resolved.label} loading="lazy" />
  {:else if resolved.kind === "video"}
    <!-- svelte-ignore a11y_media_has_caption -->
    <video class="file-preview-video" src={resolved.href} controls preload="metadata"></video>
  {:else if resolved.kind === "audio"}
    <audio class="file-preview-audio" src={resolved.href} controls preload="metadata"></audio>
  {:else if error}
    <span class="quiet-line">{error}</span>
  {:else if resolved.kind === "html"}
    <article class="file-preview-document">
      <div class="file-preview-document-header">
        {@render openLink(resolved.previewHref)}
      </div>
      <iframe
        bind:this={htmlFrame}
        class="file-preview-frame"
        data-file-preview-html
        sandbox={MANAGED_HTML_PREVIEW_SANDBOX}
        title={resolved.label}
      ></iframe>
    </article>
  {:else if resolved.kind === "markdown" && expansion.expandable}
    <article class="file-preview-document">
      <div class="file-preview-document-header">
        {@render openLink(resolved.previewHref)}
      </div>
      <div class="file-preview-document-body">
        {#if text === null}
          <div class="quiet-line">Loading preview...</div>
        {:else}
          <MarkdownBlock text={text} depth={expansion.nextDepth} visited={expansion.nextVisited} />
        {/if}
      </div>
    </article>
  {:else if resolved.kind === "markdown"}
    {@render openLink(resolved.previewHref)}
  {:else if resolved.kind === "download"}
    <a class="file-preview-link" href={resolved.href} download="">Download {resolved.label}</a>
  {:else}
    {@render openLink(resolved.href)}
  {/if}
</div>
