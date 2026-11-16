<script lang="ts">
  import type { RegisterManagedArtifactFrame } from "../lib/managedArtifactPress";
  import { artifactPageUrl } from "../lib/artifactOrigin";
  import SkeletonRegion from "./SkeletonRegion.svelte";

  let { href, label, origin, active = true, reloadSignal = 0, full = false, registerArtifactFrame }: {
    href: string; label: string; origin: string; active?: boolean; reloadSignal?: number; full?: boolean; registerArtifactFrame?: RegisterManagedArtifactFrame;
  } = $props();
  let frameHref = $state<string | null>(null);
  let notice = $state("");
  let loaded = $state(false);
  function artifactFrame(element: HTMLIFrameElement) {
    return { destroy: registerArtifactFrame?.(element) };
  }
  $effect(() => {
    const candidate = href;
    const pageOrigin = origin;
    const enabled = active;
    reloadSignal;
    frameHref = null;
    notice = "";
    loaded = false;
    if (!enabled) return;
    try {
      frameHref = artifactPageUrl(pageOrigin, candidate, window.location.origin);
    } catch (error) {
      notice = error instanceof Error ? error.message : String(error);
    }
  });
</script>

<div class:full class="managed-html-document" data-visible-content-failed={Boolean(notice) || undefined}>
  <SkeletonRegion shape={full ? "document" : "frame"} layout={full ? "fill" : "flow"}
    loading={active && !notice && (full ? !frameHref : !loaded)} loadingText="Loading preview...">
    {#if notice}
      <span class="quiet-line managed-html-fetch-notice" role="status" data-html-fetch-notice>{notice}</span>
    {:else if frameHref}
      {#key `${frameHref}:${reloadSignal}`}
        <iframe use:artifactFrame class={full ? "file-preview-document-frame" : "file-preview-frame"}
          data-file-preview-html src={frameHref} title={label} onload={() => { loaded = true; }}></iframe>
      {/key}
    {/if}
  </SkeletonRegion>
</div>
<style>
  .managed-html-document { min-height: 0; width: 100%; }
  .managed-html-document.full { height: 100%; display: flex; flex-direction: column; }
</style>
