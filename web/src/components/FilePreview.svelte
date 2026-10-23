<script lang="ts">
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
    visited = []
  }: {
    target: FilePreviewTarget;
    mode?: "embedded" | "full";
    depth?: number;
    visited?: string[];
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

<!-- The file's own name, for the previews that cannot say it themselves: any preview on
     a narrow screen, where the file is not drawn at all, and every media preview, which
     draws a player with nothing in it until somebody presses play. -->
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
  {#if mode === "embedded" && resolved.kind !== "download" && resolved.target.kind !== "external-link" && resolved.previewHref}
    {@render nameLink(resolved.previewHref)}
  {/if}
  {#if !visible && !inline}
    <span aria-hidden="true"></span>
  {:else if showsMedia && resolved.kind === "image"}
    <img
      class="file-preview-image"
      src={resolved.href}
      alt={resolved.label}
      decoding="async"
      onerror={onMediaError}
    />
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
    <span class="quiet-line">{error}</span>
  {:else if resolved.kind === "pdf"}
    <iframe
      class="file-preview-frame file-preview-frame--pdf"
      src={resolved.href}
      title={resolved.label}
    ></iframe>
  {:else if resolved.kind === "html"}
    <article class="file-preview-document">
      <div class="file-preview-document-header">
        {@render openLink(resolved.previewHref ?? resolved.href)}
      </div>
      <ManagedHtmlDocument href={resolved.href} label={resolved.label} active={visible} />
    </article>
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
  .conversation-text-preview {
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
