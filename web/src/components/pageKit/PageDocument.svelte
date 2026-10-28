<script lang="ts">
  import type { PageDocument } from "../../lib/pageKit";
  import PageBlocks from "./PageBlocks.svelte";

  let { document, showHeader = true }: { document: PageDocument; showHeader?: boolean } = $props();
</script>

<article class="page-kit" data-page-title={document.title}>
  {#if showHeader}<header class="page-header">
    <h1>{document.title}</h1>
    <p class="summary">{document.summary}</p>
  </header>{/if}
  <div class="page-body">
    <PageBlocks blocks={document.blocks} />
  </div>
</article>

<style>
  .page-kit {
    container-name: page;
    container-type: inline-size;
    width: 100%;
    max-width: var(--document-channel-measure);
    color: var(--text-default);
    font-family: var(--font-ui);
  }
  .page-header { display: grid; gap: var(--space-5); }
  h1 {
    margin: 0;
    font-size: var(--type-display);
    font-weight: 500;
    letter-spacing: var(--tracking-title);
    line-height: 1.15;
    color: var(--text-strong);
  }
  .summary {
    margin: 0;
    font-size: var(--type-prose-lg);
    line-height: 1.6;
    color: var(--text-muted);
    white-space: pre-wrap;
  }
  .page-body { margin-top: var(--space-6); }
</style>
