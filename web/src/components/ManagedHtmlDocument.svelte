<script lang="ts">
  import type { RegisterManagedArtifactFrame } from "../lib/managedArtifactPress";
  import LegacyManagedHtmlDocument from "./LegacyManagedHtmlDocument.svelte";
  import IsolatedHtmlDocument from "./IsolatedHtmlDocument.svelte";
  import SkeletonRegion from "./SkeletonRegion.svelte";

  let { href, label, active = true, reloadSignal = 0, full = false, registerArtifactFrame }: {
    href: string; label: string; active?: boolean; reloadSignal?: number; full?: boolean; registerArtifactFrame?: RegisterManagedArtifactFrame;
  } = $props();
  let origin = $state<string | null | undefined>(undefined);
  let notice = $state("");
  $effect(() => {
    if (!active) return;
    const controller = new AbortController();
    fetch('/api/artifact-origin', { signal: controller.signal }).then(response => {
      if (!response.ok) throw new Error(`Artifact origin lookup failed: ${response.status}`);
      return response.json() as Promise<{origin: string | null}>;
    }).then(result => { if (!controller.signal.aborted) origin = result.origin; })
      .catch(error => { if (!controller.signal.aborted) notice = error instanceof Error ? error.message : String(error); });
    return () => controller.abort();
  });
</script>

{#if notice}
  <span class="quiet-line" role="status">{notice}</span>
{:else if origin === undefined}
  <SkeletonRegion shape={full ? "document" : "frame"} layout={full ? "fill" : "flow"} loading={active} loadingText="Loading preview..." />
{:else if origin === null}
  <LegacyManagedHtmlDocument {href} {label} {active} {reloadSignal} {full} {registerArtifactFrame} />
{:else}
  <IsolatedHtmlDocument {href} {label} {active} {reloadSignal} {full} {registerArtifactFrame} {origin} />
{/if}
