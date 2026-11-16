<script lang="ts">
  import type { RegisterManagedArtifactFrame } from "../lib/managedArtifactPress";
  import SkeletonRegion from "./SkeletonRegion.svelte";

  let { href, label, active = true, reloadSignal = 0, full = false, registerArtifactFrame }: {
    href: string; label: string; active?: boolean; reloadSignal?: number; full?: boolean; registerArtifactFrame?: RegisterManagedArtifactFrame;
  } = $props();
  let loaded = $state(false);
  $effect(() => { href; reloadSignal; active; loaded = false; });
  function artifactFrame(element: HTMLIFrameElement) {
    return { destroy: registerArtifactFrame?.(element) };
  }
</script>

<div class:full class="managed-html-document">
  <SkeletonRegion shape={full ? "document" : "frame"} layout={full ? "fill" : "flow"}
    loading={active && !full && !loaded} loadingText="Loading preview...">
    {#if active}
      {#key `${href}:${reloadSignal}`}
        <iframe use:artifactFrame class={full ? "file-preview-document-frame" : "file-preview-frame"}
          data-file-preview-html src={href} title={label} onload={() => { loaded = true; }}></iframe>
      {/key}
    {/if}
  </SkeletonRegion>
</div>
<style>
  .managed-html-document { min-height: 0; width: 100%; }
  .managed-html-document.full { height: 100%; display: flex; flex-direction: column; }
</style>
