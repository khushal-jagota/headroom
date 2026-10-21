<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import SkeletonRegion from "./SkeletonRegion.svelte";
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
  import { observeVisibleContent, holdVisibleContentHeight, resetVisibleContentHeight } from "../lib/visibleContent";
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

  let visible = $state(false);
  let host: HTMLElement;
  function visibility(element: HTMLElement, _identity: string) {
    const stop = observeVisibleContent(element, next => { visible = next; }, { canRetain: () => !error });
    return { destroy: stop, update() { resetVisibleContentHeight(element); } };
  }
  let resolved = $derived(resolvePreview(target));
  let htmlFrameHref = $state<string | null>(null);
  let markdownSource = $state<string | null>(null);
  let error = $state("");
  let markdownVisited = $derived(resolved.kind === "markdown" ? [resolved.href] : []);

  let displayedIdentity = "";
  let objectUrl: string | null = null;
  $effect(() => {
    const current = resolved;
    const currentReloadSignal = reloadSignal;
    const identity = `${current.kind}/${current.href}`;
    const retained = visible && displayedIdentity === identity;
    if (!retained) {
      htmlFrameHref = null;
      markdownSource = null;
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    }
    error = "";
    if (!visible || (current.kind !== "html" && current.kind !== "markdown")) return;

    const controller = new AbortController();
    fetch(current.href, {
      signal: controller.signal,
      cache: currentReloadSignal > 0 ? "no-store" : "default"
    })
      .then((response) => {
        if (!response.ok) throw new Error(`file fetch failed: ${response.status}`);
        return response.text();
      })
      .then((body) => {
        if (controller.signal.aborted) return;
        if (retained) holdVisibleContentHeight(host);
        if (current.kind === "html") {
          const documentBody = prepareManagedHtmlPreviewDocument(body, current.href);
          const previousUrl = objectUrl;
          objectUrl = URL.createObjectURL(new Blob([documentBody], { type: "text/html" }));
          htmlFrameHref = objectUrl;
          if (previousUrl) void tick().then(() => URL.revokeObjectURL(previousUrl));
        } else {
          markdownSource = body;
        }
        displayedIdentity = identity;
      })
      .catch((err) => {
        if (!controller.signal.aborted) error = err instanceof Error ? err.message : String(err);
      });

    return () => controller.abort();
  });
  onDestroy(() => { if (objectUrl) URL.revokeObjectURL(objectUrl); });

</script>

<div class="visible-file-document" bind:this={host} use:visibility={`${resolved.kind}/${resolved.href}`} data-visible-content-failed={Boolean(error) || undefined}>
{#if !visible}
  <div aria-hidden="true"></div>
{:else if resolved.kind === "html"}
  <SkeletonRegion shape="document" layout="fill" loading={!error && !htmlFrameHref} loadingText="Loading preview...">
  {#if error}
    <div class="quiet-line">{error}</div>
  {/if}
  {#if htmlFrameHref}
    <iframe
      class="file-preview-document-frame"
      data-file-preview-html
      src={htmlFrameHref}
      sandbox={MANAGED_HTML_PREVIEW_SANDBOX}
      title={resolved.label}
    ></iframe>
  {/if}
  </SkeletonRegion>
{:else if resolved.kind === "markdown"}
  <SkeletonRegion shape="document" layout="fill" loading={!error && markdownSource === null} loadingText="Loading preview...">
  {#if error}
    <div class="quiet-line">{error}</div>
  {/if}
  {#if markdownSource !== null}
    <article class="file-preview-markdown-document" data-file-preview-markdown>
      <MarkdownBlock text={markdownSource} depth={1} visited={markdownVisited} />
    </article>
  {/if}
  </SkeletonRegion>
{:else}
  <FilePreview {target} mode="full" />
{/if}

</div>
<style>
  .visible-file-document { width: 100%; height: 100%; min-height: 0; }
</style>
