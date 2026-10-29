<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Block, SectionBlock } from "../../lib/pageKit";
  import PageSegmented from "./PageSegmented.svelte";

  let { block, view }: { block: SectionBlock; view: Snippet<[Block[]]> } = $props();

  let selected = $state("");
  let views = $derived(block.views ?? []);
  let current = $derived(views.find((item) => item.id === selected) ?? views[0]);
</script>

<section class="section" data-block="section">
  <header class="section-header">
    <div class="section-heading">
      <h2>{block.title}</h2>
      {#if block.summary}<p class="section-summary">{block.summary}</p>{/if}
    </div>
    {#if block.views}
      <div class="section-actions">
        <PageSegmented views={block.views} bind:selected />
      </div>
    {/if}
  </header>
  <div class="section-body">
    {#if block.views && current}
      {@render view(current.blocks)}
    {:else if block.blocks}
      {@render view(block.blocks ?? [])}
    {/if}
  </div>
</section>

<style>
  .section-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
    column-gap: var(--space-5);
    row-gap: var(--space-4);
  }
  .section-heading {
    display: grid;
    gap: var(--space-3);
    min-width: 0;
  }
  h2 {
    margin: 0;
    font-size: var(--type-lg);
    font-weight: 500;
    letter-spacing: var(--tracking-title);
    line-height: 1.3;
    color: var(--text-strong);
  }
  .section-summary {
    margin: 0;
    color: var(--text-faint);
    font-size: var(--type-md);
    line-height: 1.5;
    white-space: pre-wrap;
  }
  .section-actions {
    grid-column: 2;
    grid-row: 1 / span 2;
    justify-self: end;
    align-self: start;
  }
  .section-body { margin-top: var(--space-5); }
  @container page (max-width: 520px) {
    .section-header { grid-template-columns: minmax(0, 1fr); }
    .section-actions {
      grid-column: 1;
      grid-row: auto;
      justify-self: start;
    }
  }
</style>
