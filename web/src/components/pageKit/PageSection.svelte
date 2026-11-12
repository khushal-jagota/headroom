<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Block, SectionBlock } from "../../lib/pageKit";
  import PageText from "./PageText.svelte";
  import PageSegmented from "./PageSegmented.svelte";

  let { block, view }: { block: SectionBlock; view: Snippet<[Block[]]> } = $props();

</script>

<section class="section" data-block="section">
  <header class="section-header">
    <div class="section-heading">
      <div class="section-title" role="heading" aria-level="2"><PageText blockId={block.id} field="title" value={block.title} label="Section title" update={(current, raw) => current.type === "section" ? {...current, title: raw} : current} /></div>
      {#if block.summary}<div class="section-summary"><PageText blockId={block.id} field="summary" value={block.summary} label="Section summary" multiline update={(current, raw) => current.type === "section" ? {...current, summary: raw} : current} /></div>{/if}
    </div>
  </header>
  {#if block.views}
    <div class="section-tabs">
      <PageSegmented blockId={block.id} views={block.views} body={view} />
    </div>
  {:else if block.blocks}
    <div class="section-body">{@render view(block.blocks)}</div>
  {/if}
</section>

<style>
  .section-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-items: start;
    column-gap: var(--space-5);
    row-gap: var(--space-4);
  }
  .section-heading {
    display: grid;
    gap: var(--space-3);
    min-width: 0;
  }
  .section-title {
    margin: 0;
    font-size: var(--type-page-section);
    font-weight: var(--weight-page-section);
    letter-spacing: 0;
    line-height: 20px;
    color: var(--tone-primary);
  }
  .section-summary {
    margin: 0;
    max-width: var(--page-prose-measure);
    color: var(--tone-secondary);
    font-size: var(--type-body);
    line-height: var(--leading-body);
    white-space: pre-wrap;
  }
  .section-tabs { min-width: 0; margin-top: var(--space-4); }
  .section-body { margin-top: var(--page-head-gap); }
</style>
