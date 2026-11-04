<script lang="ts">
  import EmbeddedMediaPreview from "./EmbeddedMediaPreview.svelte";
  import SkeletonRegion from "./SkeletonRegion.svelte";
  import ManagedHtmlDocument from "./ManagedHtmlDocument.svelte";
  import { observeVisibleContent, resetVisibleContentHeight } from "../lib/visibleContent";
  import type { FilePreviewTarget } from "../lib/filePreview";
  import {
    boundedConversationTextPreview,
    markdownExpansionFor,
    resolvePreview
  } from "../lib/filePreview";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import { readManagedFile } from "../lib/managedFileRead";

  let {
    target,
    mode = "embedded",
    depth = 0,
    visited = [],
    knownSizeLabel,
    download
  }: {
    target: FilePreviewTarget;
    mode?: "embedded" | "full";
    depth?: number;
    visited?: string[];
    knownSizeLabel?: string;
    download?: { href: string; fileName: string };
  } = $props();

  const MEDIA_FETCH_FAILED = "file fetch failed";

  let visible = $state(false);
  function visibility(element: HTMLElement, _identity: string) {
    const stop = observeVisibleContent(element, next => { visible = next; }, { canRetain: () => !error });
    return { destroy: stop, update() { resetVisibleContentHeight(element); } };
  }

  let text = $state<string | null>(null);
  let error = $state("");
  let resolved = $derived(resolvePreview(target));
  let expansion = $derived(markdownExpansionFor(resolved, depth, visited));
  let shouldFetchText = $derived(
    (resolved.kind === "markdown" && expansion.expandable)
      || resolved.kind === "text"
  );
  let showsMedia = $derived(
    (resolved.kind === "image" || resolved.kind === "video" || resolved.kind === "audio") &&
      !error
  );
  let showsDocument = $derived((shouldFetchText || resolved.kind === "pdf" || resolved.kind === "html") && !error);
  let inline = $derived(!showsMedia && !showsDocument);

  let isCard = $derived(mode === "embedded" && showsDocument);
  function clipping(element: HTMLElement) {
    const inner = element.firstElementChild;
    const measure = () => element.classList.toggle("file-preview-clip--cut",
      text !== null && element.scrollHeight > element.clientHeight);
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    if (inner) observer.observe(inner);
    measure();
    return { destroy: () => observer.disconnect() };
  }

  function onMediaError() {
    error = MEDIA_FETCH_FAILED;
  }

  $effect(() => {
    const current = resolved;
    text = null;
    error = "";
    if (!visible || !shouldFetchText) return;
    const controller = new AbortController();
    readManagedFile(current.href, controller.signal)
      .then((body) => {
        // Shared reads abort when the last visible reader leaves.
        if (controller.signal.aborted) return;
        text = (current.kind === "text" || current.kind === "markdown")
              && current.target.kind === "conversation-file"
            ? boundedConversationTextPreview(body)
            : body;
      })
      .catch((err) => {
        if (!controller.signal.aborted) error = err instanceof Error ? err.message : String(err);
      });
    return () => controller.abort();
  });

</script>

{#snippet openLink(href: string | undefined)}
  <!-- Opened here rather than in a new tab. One thing at a time is easier to follow and
       easier to leave — back is always the way out — and it is the same on a phone, where
       a second tab is a place you have to go and find. -->
  <a class="file-preview-link" {href} rel="noopener noreferrer">Open {resolved.label}</a>
{/snippet}

<!-- Media retains its name above the transport. -->
{#snippet nameLink(href: string | undefined)}
  <a class="file-preview-link file-preview-name-link" {href} rel="noopener noreferrer">
    Open {resolved.label}
  </a>
{/snippet}

<div
  class={`file-preview file-preview--${mode}${inline ? " file-preview--inline" : ""}${resolved.target.kind !== "external-link" ? " file-preview--managed" : ""}`}
  data-file-preview
  data-file-preview-kind={resolved.kind}
  use:visibility={resolved.href}
  data-visible-content-failed={Boolean(error) || undefined}
>
  {#if mode === "embedded" && showsMedia && (resolved.kind === "video" || resolved.kind === "audio")}
    {@render nameLink(resolved.previewHref ?? resolved.href)}
    {#if download}<a class="pv-act file-preview-media-download" href={download.href} download={download.fileName}>Download</a>{/if}
  {/if}
  {#if mode === "embedded" && showsMedia && resolved.kind === "image" && download}<a class="pv-act file-preview-media-download" href={download.href} download={download.fileName}>Download</a>{/if}
  {#if isCard}
    <article class="file-preview-card" class:file-preview-card--live={resolved.kind === "html" || resolved.kind === "pdf"}>
      <div class="file-preview-content">
        {#if resolved.kind === "html"}
          <ManagedHtmlDocument href={resolved.href} label={resolved.label} active={visible} inlineFailure />
        {:else if resolved.kind === "pdf"}
          {#if visible}<iframe class="file-preview-frame file-preview-frame--pdf" src={resolved.href} title={resolved.label}></iframe>{/if}
        {:else}
          <div class="file-preview-clip" class:file-preview-clip--loading={text === null} use:clipping>
            <div class="file-preview-natural" inert>
              <SkeletonRegion shape="document" loading={text === null} loadingText="Loading preview...">
                {#if text !== null}
                  {#if resolved.kind === "markdown"}<MarkdownBlock text={text} depth={expansion.nextDepth} visited={expansion.nextVisited} />
                  {:else}<pre class="conversation-text-preview">{text}</pre>{/if}
                {/if}
              </SkeletonRegion>
            </div>
          </div>
        {/if}
      </div>
      <div class="file-preview-band">
        <span class="file-preview-tile" aria-hidden="true"><svg viewBox="0 0 20 20" fill="none">
          {#if resolved.kind === "html"}<path d="M7.5 7 4 10l3.5 3M12.5 7 16 10l-3.5 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
          {:else if resolved.kind === "pdf"}<path d="M5 3h7l3 3v11H5Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M12 3v3h3" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M7.6 14v-3.4h1.3a1.1 1.1 0 0 1 0 2.2H7.6M11.6 14v-3.4h1.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          {:else if resolved.kind === "text"}<path d="M4.5 5h11M4.5 8.5h11M4.5 12h8M4.5 15.5h5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
          {:else}<path d="M5 3h7l3 3v11H5Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M12 3v3h3" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M7.5 10h5M7.5 13h3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>{/if}
        </svg></span>
        <span class="file-preview-identity">
          <a class="pv-name" href={resolved.previewHref ?? resolved.href}>{resolved.label}</a>
          <span class="file-preview-kind">{resolved.kind === "markdown" ? "Markdown" : resolved.kind === "html" ? "HTML" : resolved.kind === "pdf" ? "PDF" : "Text"}{knownSizeLabel ? ` · ${knownSizeLabel}` : ""}</span>
        </span>
        {#if download}<a class="pv-act" href={download.href} download={download.fileName}>Download</a>{/if}
      </div>
    </article>
  {:else if resolved.kind === "html" && !error}
    <!-- Drawn before the preview comes into view, so the box it will occupy is already
         there at first paint. Nothing is fetched until `visible`. -->
    <article class="file-preview-document">
      <div class="file-preview-document-header">
        {@render openLink(resolved.previewHref ?? resolved.href)}
      </div>
      <ManagedHtmlDocument href={resolved.href} label={resolved.label} active={visible} />
    </article>
  {:else if !visible && !inline}
    <span aria-hidden="true"></span>
  {:else if showsMedia && resolved.kind === "image"}
    <img
      class="file-preview-image"
      src={resolved.href}
      alt={resolved.label}
      decoding="async"
      onerror={onMediaError}
    />
  {:else if showsMedia && (resolved.kind === "video" || resolved.kind === "audio") && mode === "embedded"}
    {#key resolved.href}<EmbeddedMediaPreview href={resolved.href} label={resolved.label} kind={resolved.kind} onerror={onMediaError} />{/key}
  {:else if showsMedia && resolved.kind === "video"}
    <!-- svelte-ignore a11y_media_has_caption -->
    <video
      class="file-preview-video"
      src={resolved.href}
      controls
      preload="none"
      onerror={onMediaError}
    ></video>
  {:else if showsMedia && resolved.kind === "audio"}
    <audio
      class="file-preview-audio"
      src={resolved.href}
      controls
      preload="none"
      onerror={onMediaError}
    ></audio>
  {:else if error}
    <span class="quiet-line">{#if mode === "embedded"}<span class="file-preview-failed-name">{resolved.label}</span>{" — "}{/if}{error}</span>
    {#if mode === "embedded" && download}<a class="pv-act" href={download.href} download={download.fileName}>Download</a>{/if}
  {:else if resolved.kind === "pdf"}
    <iframe
      class="file-preview-frame file-preview-frame--pdf"
      src={resolved.href}
      title={resolved.label}
    ></iframe>
  {:else if resolved.kind === "markdown" && expansion.expandable}
    <article class="file-preview-document">
      <div class="file-preview-document-header">
        {@render openLink(resolved.previewHref ?? resolved.href)}
      </div>
      <div class="file-preview-document-body">
        <SkeletonRegion shape="document" loading={text === null} loadingText="Loading preview...">
          {#if text !== null}<MarkdownBlock text={text} depth={expansion.nextDepth} visited={expansion.nextVisited} />{/if}
        </SkeletonRegion>
      </div>
    </article>
  {:else if resolved.kind === "markdown"}
    {@render openLink(resolved.previewHref ?? resolved.href)}
  {:else if resolved.kind === "text"}
    <article class="file-preview-document">
      <div class="file-preview-document-header">
        {@render openLink(resolved.previewHref ?? resolved.href)}
      </div>
      <SkeletonRegion shape="document" loading={text === null} loadingText="Loading preview...">
        {#if text !== null}<pre class="conversation-text-preview">{text}</pre>{/if}
      </SkeletonRegion>
    </article>
  {:else if resolved.kind === "download"}
    <a class="file-preview-link" href={resolved.href} download="">Download {resolved.label}</a>
  {:else}
    {@render openLink(resolved.href)}
  {/if}
</div>

<style>
  .file-preview-frame--pdf { min-height: min(32rem, 60vh); }
  :global(.file-preview--full) .conversation-text-preview {
    max-height: min(24rem, 50vh);
    margin: 0;
    padding: var(--space-3);
    overflow: auto;
    color: var(--text-default);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
