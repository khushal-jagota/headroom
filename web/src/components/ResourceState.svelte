<script lang="ts">
  import type { Snippet } from "svelte";
  import ErrorLine from "./ErrorLine.svelte";

  let {
    error,
    loading = false,
    hasData = false,
    loadingText,
    children
  }: {
    error?: unknown;
    loading?: boolean;
    hasData?: boolean;
    loadingText: string;
    children?: Snippet;
  } = $props();
</script>

{#if error}
  <ErrorLine {error} />
{:else if loading && !hasData}
  <div class="quiet-line">{loadingText}</div>
{:else}
  {@render children?.()}
{/if}
