<script lang="ts">
  import FilePreview from "../components/FilePreview.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import type { FilePreviewTarget } from "../lib/filePreview";
  import {
    MANAGED_HTML_PREVIEW_SANDBOX,
    prepareManagedHtmlPreviewDocument,
    resolvePreview,
    ticketFileTarget
  } from "../lib/filePreview";

  function parseTarget(): FilePreviewTarget | null {
    const hash = window.location.hash;
    const queryIndex = hash.indexOf("?");
    const params = new URLSearchParams(queryIndex >= 0 ? hash.slice(queryIndex + 1) : "");
    const path = params.get("path") || "";
    if (params.get("source") === "ticket") {
      return ticketFileTarget(params.get("ticket") || "", path);
    }
    return null;
  }

  let target = $derived(parseTarget());
  let resolved = $derived(target ? resolvePreview(target) : null);
  let htmlFrameHref = $state<string | null>(null);
  let markdownSource = $state<string | null>(null);
  let error = $state("");
  let isDocumentPreview = $derived(resolved?.kind === "html" || resolved?.kind === "markdown");
  let markdownVisited = $derived(resolved?.kind === "markdown" ? [resolved.href] : []);

  $effect(() => {
    const current = resolved;
    htmlFrameHref = null;
    markdownSource = null;
    error = "";
    if (!current || (current.kind !== "html" && current.kind !== "markdown")) return;

    const controller = new AbortController();
    let objectUrl: string | null = null;
    fetch(current.href, { signal: controller.signal })
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

<section
  class={`file-preview-route${isDocumentPreview ? " file-preview-route--document" : ""}`}
  data-file-preview-route
>
  {#if resolved?.kind === "html"}
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
  {:else if resolved?.kind === "markdown"}
    {#if error}
      <div class="quiet-line">{error}</div>
    {:else if markdownSource !== null}
      <article class="file-preview-markdown-document" data-file-preview-markdown>
        <MarkdownBlock text={markdownSource} depth={1} visited={markdownVisited} />
      </article>
    {:else}
      <div class="quiet-line">Loading preview...</div>
    {/if}
  {:else if target}
    <FilePreview {target} mode="full" />
  {:else}
    <div class="quiet-line">file preview not found</div>
  {/if}
</section>
