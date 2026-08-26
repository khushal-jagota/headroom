<script lang="ts">
  /** One managed file, at full size, filling whatever box the host gives it.
   *
   * There are two hosts and one rendering: the `#/preview` address, and an artifact
   * opened on the Ticket screen it was clicked from. HTML is fetched and handed to a
   * sandboxed iframe through a short-lived Blob URL, so the file's own scripts run
   * without same-origin or host-page privileges. Markdown is fetched as source and
   * rendered through the ordinary markdown renderer, so managed links inside it keep the
   * same nested-preview and self-link bounds as everywhere else. Every other kind is the
   * shared preview at full size, which already knows how to draw an image, a player, or
   * a download line.
   */
  import FilePreview from "./FilePreview.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import type { FilePreviewTarget } from "../lib/filePreview";
  import {
    MANAGED_HTML_PREVIEW_SANDBOX,
    prepareManagedHtmlPreviewDocument,
    resolvePreview
  } from "../lib/filePreview";

  let {
    target,
    reloadSignal = 0
  }: { target: FilePreviewTarget; reloadSignal?: number } = $props();

  let resolved = $derived(resolvePreview(target));
  let htmlFrameHref = $state<string | null>(null);
  let markdownSource = $state<string | null>(null);
  let error = $state("");
  let markdownVisited = $derived(resolved.kind === "markdown" ? [resolved.href] : []);

  $effect(() => {
    const current = resolved;
    const currentReloadSignal = reloadSignal;
    htmlFrameHref = null;
    markdownSource = null;
    error = "";
    if (current.kind !== "html" && current.kind !== "markdown") return;

    const controller = new AbortController();
    let objectUrl: string | null = null;
    fetch(current.href, {
      signal: controller.signal,
      cache: current.kind === "html" && currentReloadSignal > 0 ? "no-store" : "default"
    })
      .then((response) => {
        if (!response.ok) throw new Error(`file fetch failed: ${response.status}`);
        return response.text();
      })
      .then((body) => {
        if (controller.signal.aborted) return;
        if (current.kind === "html") {
          const documentBody = prepareManagedHtmlPreviewDocument(body, current.href);
          objectUrl = URL.createObjectURL(new Blob([documentBody], { type: "text/html" }));
          htmlFrameHref = objectUrl;
        } else {
          markdownSource = body;
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) error = err instanceof Error ? err.message : String(err);
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  });
</script>

{#if resolved.kind === "html"}
  {#if error}
    <div class="quiet-line">{error}</div>
  {:else if htmlFrameHref}
    <iframe
      class="file-preview-document-frame"
      data-file-preview-html
      src={htmlFrameHref}
      sandbox={MANAGED_HTML_PREVIEW_SANDBOX}
      title={resolved.label}
    ></iframe>
  {:else}
    <div class="quiet-line">Loading preview...</div>
  {/if}
{:else if resolved.kind === "markdown"}
  {#if error}
    <div class="quiet-line">{error}</div>
  {:else if markdownSource !== null}
    <article class="file-preview-markdown-document" data-file-preview-markdown>
      <MarkdownBlock text={markdownSource} depth={1} visited={markdownVisited} />
    </article>
  {:else}
    <div class="quiet-line">Loading preview...</div>
  {/if}
{:else}
  <FilePreview {target} mode="full" />
{/if}
