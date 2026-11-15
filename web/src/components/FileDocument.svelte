<script lang="ts">
  import type { RegisterManagedArtifactFrame } from "../lib/managedArtifactPress";
  import ManagedHtmlDocument from "./ManagedHtmlDocument.svelte";
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
    resolvePreview
  } from "../lib/filePreview";

  let {
    target,
    reloadSignal = 0,
    registerArtifactFrame,
    retainWhileSelected = false
  }: { target: FilePreviewTarget; reloadSignal?: number; registerArtifactFrame?: RegisterManagedArtifactFrame; retainWhileSelected?: boolean } = $props();

  let visible = $state(false);
  let selectedAdmitted = $state(false);
  let mounted = $derived(visible || retainWhileSelected && selectedAdmitted);
  let host: HTMLElement;
  function visibility(element: HTMLElement, _identity: string) {
    const stop = observeVisibleContent(element, next => { visible = next; if (next) selectedAdmitted = true; }, { canRetain: () => !error });
    return { destroy: stop, update() { resetVisibleContentHeight(element); } };
  }
  let resolved = $derived(resolvePreview(target));
  let markdownSource = $state<string | null>(null);
  let error = $state("");
  let markdownVisited = $derived(resolved.kind === "markdown" ? [resolved.href] : []);

  let displayedIdentity = "";
  $effect(() => {
    const current = resolved;
    const currentReloadSignal = reloadSignal;
    const identity = `${current.kind}/${current.href}`;
    const retained = mounted && displayedIdentity === identity;
    if (!retained) {
      markdownSource = null;
    }
    error = "";
    if (!mounted || current.kind !== "markdown") return;

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
        markdownSource = body;
        displayedIdentity = identity;
      })
      .catch((err) => {
        if (!controller.signal.aborted) error = err instanceof Error ? err.message : String(err);
      });

    return () => controller.abort();
  });

</script>

<div class="visible-file-document" bind:this={host} use:visibility={`${resolved.kind}/${resolved.href}`} data-visible-content-failed={Boolean(error) || undefined}>
{#if !mounted}
  <div aria-hidden="true"></div>
{:else if resolved.kind === "html"}
  <ManagedHtmlDocument href={resolved.href} label={resolved.label} active={mounted} {reloadSignal} {registerArtifactFrame} full />
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
