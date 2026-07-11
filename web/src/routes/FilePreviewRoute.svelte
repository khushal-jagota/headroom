<script lang="ts">
  import FilePreview from "../components/FilePreview.svelte";
  import type { FilePreviewTarget } from "../lib/filePreview";
  import { chatFileTarget, resolvePreview, ticketFileTarget } from "../lib/filePreview";

  function parseTarget(): FilePreviewTarget | null {
    const hash = window.location.hash;
    const queryIndex = hash.indexOf("?");
    const params = new URLSearchParams(queryIndex >= 0 ? hash.slice(queryIndex + 1) : "");
    const path = params.get("path") || "";
    if (params.get("source") === "ticket") {
      return ticketFileTarget(params.get("ticket") || "", path);
    }
    if (params.get("source") === "chat") {
      return chatFileTarget(params.get("entity") || "", path);
    }
    return null;
  }

  let target = $derived(parseTarget());
  let resolved = $derived(target ? resolvePreview(target) : null);
  let htmlFrameHref = $state<string | null>(null);
  let error = $state("");

  $effect(() => {
    const current = resolved;
    htmlFrameHref = null;
    error = "";
    if (!current || current.kind !== "html") return;

    const controller = new AbortController();
    let objectUrl: string | null = null;
    fetch(current.href, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`file fetch failed: ${response.status}`);
        return response.text();
      })
      .then((body) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(new Blob([body], { type: "text/html" }));
        htmlFrameHref = objectUrl;
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
  class={`file-preview-route${resolved?.kind === "html" ? " file-preview-route--document" : ""}`}
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
        sandbox=""
        title={resolved.label}
      ></iframe>
    {:else}
      <div class="quiet-line">Loading preview...</div>
    {/if}
  {:else if target}
    <FilePreview {target} mode="full" />
  {:else}
    <div class="quiet-line">file preview not found</div>
  {/if}
</section>
