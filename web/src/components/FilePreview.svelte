<script lang="ts">
  import type { FilePreviewTarget } from "../lib/filePreview";
  import { resolvePreview } from "../lib/filePreview";

  let {
    target,
    mode = "embedded"
  }: {
    target: FilePreviewTarget;
    mode?: "embedded" | "full";
  } = $props();

  let text = $state<string | null>(null);
  let error = $state("");
  let markdownHost = $state<HTMLDivElement | null>(null);
  let htmlFrame = $state<HTMLIFrameElement | null>(null);
  let resolved = $derived(resolvePreview(target));
  let shouldFetchText = $derived(mode === "full" && (resolved.kind === "markdown" || resolved.kind === "html"));

  function renderMarkdown(): void {
    if (!markdownHost || resolved.kind !== "markdown" || text === null) return;
    markdownHost.replaceChildren();
    const rendered = window.Planner?.markdown?.render(text);
    if (rendered) {
      rendered.classList.add("markdown-block");
      markdownHost.appendChild(rendered);
      return;
    }
    const fallback = document.createElement("div");
    fallback.className = "markdown markdown-block";
    fallback.textContent = text;
    markdownHost.appendChild(fallback);
  }

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
    renderMarkdown();
  });

  $effect(() => {
    if (resolved.kind === "html" && htmlFrame && text !== null) {
      htmlFrame.srcdoc = text;
    }
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
    {#if mode === "embedded"}
      <a class="file-preview-link" href={resolved.previewHref}>{resolved.label}</a>
    {:else}
      <article class="file-preview-doc">
        <a class="file-preview-title" href={resolved.previewHref}>{resolved.label}</a>
        {#if error}
          <div class="quiet-line">{error}</div>
        {:else}
          <div bind:this={markdownHost}></div>
        {/if}
      </article>
    {/if}
  {:else if resolved.kind === "html"}
    {#if mode === "embedded"}
      <a class="file-preview-link" href={resolved.previewHref}>{resolved.label}</a>
    {:else}
      <article class="file-preview-doc">
        <a class="file-preview-title" href={resolved.previewHref}>{resolved.label}</a>
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
    {/if}
  {:else if resolved.kind === "download"}
    <a class="file-preview-link" href={resolved.href} download>{resolved.label}</a>
  {:else}
    <a class="file-preview-link" href={resolved.href} rel="noreferrer" target="_blank">{resolved.label}</a>
  {/if}
</div>
