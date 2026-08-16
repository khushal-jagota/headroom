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

<!--
  What the screen has to show is asked before anything that went wrong with it.
  A screen holding good data keeps showing it when a read fails, and the failure
  is a line above the data instead of a replacement for it. Only a screen with
  nothing to show is given over to the error or to the loading line.
-->

{#if hasData}
  {#if error}
    <ErrorLine {error} />
  {/if}
  {@render children?.()}
{:else if error}
  <ErrorLine {error} />
{:else if loading}
  <div class="quiet-line">{loadingText}</div>
{:else}
  {@render children?.()}
{/if}
