<script lang="ts">
  import type { Snippet } from "svelte";
  import type { SkeletonShape, SkeletonLayout } from "../lib/skeleton/contracts";
  import SkeletonRegion from "./SkeletonRegion.svelte";
  let { promise, shape, layout = "flow", children }: {
    promise: Promise<unknown>;
    shape: SkeletonShape;
    layout?: SkeletonLayout;
    children: Snippet;
  } = $props();
  let imported = $state.raw<Promise<unknown> | null>(null);
  $effect(() => {
    const pending = promise;
    let obsolete = false;
    const settle = () => { if (!obsolete) imported = pending; };
    void pending.then(settle, settle);
    return () => { obsolete = true; };
  });
</script>

<SkeletonRegion {shape} {layout} loading={imported !== promise} loadingText="Loading content...">
  {@render children()}
</SkeletonRegion>
