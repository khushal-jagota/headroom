<script lang="ts">
  import { onDestroy } from "svelte";
  import type { FilePreviewTarget } from "../lib/filePreview";
  import { markdownExpansionFor, resolvePreview } from "../lib/filePreview";
  import Button from "./Button.svelte";
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
        text = body;
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

<div
  class={`file-preview file-preview--${mode}`}
  data-file-preview
  data-file-preview-kind={resolved.kind}
>
  {#if resolved.kind === "image"}
    <figure class="file-preview-media">
      <a class="file-preview-title" href={resolved.previewHref}>{resolved.label}</a>
      <img src={resolved.href} alt={resolved.label} loading="lazy" />
    </figure>
  {:else if resolved.kind === "video"}
    <figure class="file-preview-media">
      <a class="file-preview-title" href={resolved.previewHref}>{resolved.label}</a>
      <!-- svelte-ignore a11y_media_has_caption -->
      <video src={resolved.href} controls preload="metadata"></video>
    </figure>
  {:else if resolved.kind === "audio"}
    <div class="file-preview-audio">
      <a class="file-preview-title" href={resolved.previewHref}>{resolved.label}</a>
      <audio src={resolved.href} controls preload="metadata"></audio>
    </div>
  {:else if resolved.kind === "markdown"}
    {#if expansion.expandable}
      <article class="file-preview-doc">
        <a
          class="file-preview-title"
          href={resolved.previewHref}
          target="_blank"
          rel="noopener noreferrer"
        >{resolved.label}</a>
        {#if error}
          <div class="quiet-line">{error}</div>
        {:else if text === null}
          <div class="quiet-line">Loading preview...</div>
        {:else}
          <MarkdownBlock
            text={text}
            depth={expansion.nextDepth}
            visited={expansion.nextVisited}
          />
        {/if}
      </article>
    {:else}
      <article class="file-preview-card">
        <div class="file-preview-card-body">
          <div class="file-preview-title">{resolved.label}</div>
          <div class="file-preview-meta">Markdown file</div>
        </div>
        <Button
          variant="quiet"
          href={resolved.previewHref}
          target="_blank"
          rel="noopener noreferrer"
        >Open preview</Button>
      </article>
    {/if}
  {:else if resolved.kind === "html"}
    <article class="file-preview-doc file-preview-html-card">
      <div class="file-preview-card-row">
        <div class="file-preview-card-body">
          <div class="file-preview-title">{resolved.label}</div>
          <div class="file-preview-meta">HTML file</div>
        </div>
        <Button
          variant="quiet"
          href={resolved.previewHref}
          target="_blank"
          rel="noopener noreferrer"
        >{resolved.actionLabel || "Open preview"}</Button>
      </div>
      {#if error}
        <div class="quiet-line">{error}</div>
      {:else}
        <iframe
          bind:this={htmlFrame}
          class="file-preview-frame"
          data-file-preview-html
          sandbox=""
          title={resolved.label}
        ></iframe>
      {/if}
    </article>
  {:else if resolved.kind === "download"}
    <article class="file-preview-card">
      <div class="file-preview-card-body">
        <div class="file-preview-title">{resolved.label}</div>
        <div class="file-preview-meta">Managed file</div>
      </div>
      <Button variant="quiet" href={resolved.href} download="">{resolved.actionLabel || "Download"}</Button>
    </article>
  {:else}
    <article class="file-preview-card">
      <div class="file-preview-card-body">
        <div class="file-preview-title">{resolved.label}</div>
        <div class="file-preview-meta">{resolved.displayHref || resolved.href}</div>
      </div>
      <Button variant="quiet" href={resolved.href} rel="noopener noreferrer" target="_blank">
        {resolved.actionLabel || "Open external link"}
      </Button>
    </article>
  {/if}
</div>
