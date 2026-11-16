<script lang="ts">
  import { untrack } from "svelte";
  import type { RegisterManagedArtifactFrame } from "../lib/managedArtifactPress";
  import SkeletonRegion from "./SkeletonRegion.svelte";

  let { href, label, active = true, reloadSignal = 0, full = false, registerArtifactFrame }: {
    href: string; label: string; active?: boolean; reloadSignal?: number; full?: boolean; registerArtifactFrame?: RegisterManagedArtifactFrame;
  } = $props();
  let loaded = $state(false);
  let frame = $state<HTMLIFrameElement>();
  let previousReload = untrack(() => reloadSignal);
  $effect(() => { href; active; loaded = false; });
  $effect(() => {
    const next = reloadSignal;
    if (next === previousReload) return;
    previousReload = next;
    untrack(() => {
      if (!frame) return;
      loaded = false;
      // A linked page can be cross-origin, where browsers prohibit reading its location.
      try { frame.contentWindow?.location.reload(); }
      catch { frame.src = href; }
    });
  });
  function artifactFrame(element: HTMLIFrameElement) {
    return { destroy: registerArtifactFrame?.(element) };
  }
</script>

<div class:full class="managed-html-document">
  <SkeletonRegion shape={full ? "document" : "frame"} layout={full ? "fill" : "flow"}
    loading={active && !full && !loaded} loadingText="Loading preview...">
    {#if active}
      {#key href}
        <iframe use:artifactFrame bind:this={frame} class={full ? "file-preview-document-frame" : "file-preview-frame"}
          data-file-preview-html src={href} title={label} onload={() => { loaded = true; }}></iframe>
      {/key}
    {/if}
  </SkeletonRegion>
</div>
<style>
  .managed-html-document { min-height: 0; width: 100%; }
  .managed-html-document.full { height: 100%; display: flex; flex-direction: column; }
</style>
