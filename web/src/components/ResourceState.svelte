<script lang="ts">
  import type { Snippet } from "svelte";
  import type { SkeletonShape, SkeletonLayout } from "../lib/skeleton/contracts";
  import ErrorLine from "./ErrorLine.svelte";
  import SkeletonRegion from "./SkeletonRegion.svelte";

  let { error, loading = false, hasData = false, loadingText, shape, layout = "flow", children }: {
    error?: unknown;
    loading?: boolean;
    hasData?: boolean;
    loadingText: string;
    shape: SkeletonShape;
    layout?: SkeletonLayout;
    children?: Snippet;
  } = $props();
</script>

<!-- Data takes precedence over errors and background reads. Keep the shared
     region mounted so the first data arrival can dissolve its outgoing shape. -->
<SkeletonRegion {shape} {layout} {loadingText} loading={!hasData && !error && loading}>
  {#if error}<ErrorLine {error} />{/if}
  {#if hasData || !error}{@render children?.()}{/if}
</SkeletonRegion>
