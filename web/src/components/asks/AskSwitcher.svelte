<script lang="ts">
  import type { PreviewAsk } from "../../lib/asks/preview";

  let {
    asks,
    active,
    onActivate
  }: {
    asks: readonly PreviewAsk[];
    active: number;
    onActivate: (index: number) => void;
  } = $props();
</script>

{#if asks.length > 1}
  <div class="switcher" data-ask-switcher>
    <button
      type="button"
      class="button button--ghost"
      aria-label="Previous waiting ask"
      disabled={active === 0}
      onclick={() => onActivate(active - 1)}
    >Previous</button>
    <span>{active + 1} of {asks.length} waiting</span>
    <button
      type="button"
      class="button button--ghost"
      aria-label="Next waiting ask"
      disabled={active === asks.length - 1}
      onclick={() => onActivate(active + 1)}
    >Next</button>
  </div>
{/if}

<style>
  .switcher {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
  }
  span { font-variant-numeric: tabular-nums; }
</style>
