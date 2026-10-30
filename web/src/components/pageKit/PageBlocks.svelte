<script lang="ts">
  import type { Block } from "../../lib/pageKit";
  import PageText from "./PageText.svelte";
  import PageBlocks from "./PageBlocks.svelte";
  import PageChart from "./PageChart.svelte";
  import PageChecklist from "./PageChecklist.svelte";
  import PageSection from "./PageSection.svelte";
  import PageTable from "./PageTable.svelte";
  import PageSegmented from "./PageSegmented.svelte";

  let { blocks, inset = false }: { blocks: Block[]; inset?: boolean } = $props();
</script>

{#snippet view(items: Block[])}
  <PageBlocks blocks={items} />
{/snippet}

{#snippet insetView(items: Block[])}
  <PageBlocks blocks={items} inset />
{/snippet}

<div class="blocks" data-page-blocks>
  {#each blocks as block, index (block.id)}
    {#if block.type === "section"}
      <PageSection {block} {view} />
    {:else if block.type === "text"}
      <div class="text" data-block="text"><PageText blockId={block.id} field="text" value={block.text} label="Text" markdown multiline update={(current, raw) => current.type === "text" ? {...current, text: raw} : current} /></div>
    {:else if block.type === "callout"}
      <aside class="callout" data-block="callout" data-tone={block.tone}>
        {#if block.title}<div class="callout-title"><PageText blockId={block.id} field="title" value={block.title} label="Callout title" update={(current, raw) => current.type === "callout" ? {...current, title: raw} : current} /></div>{/if}
        <div class="callout-text"><PageText blockId={block.id} field="text" value={block.text} label="Callout text" multiline update={(current, raw) => current.type === "callout" ? {...current, text: raw} : current} /></div>
      </aside>
    {:else if block.type === "metrics"}
      <div class="metrics" data-block="metrics" data-count={block.items.length}>
        {#each block.items as item, itemIndex (`metric-${itemIndex}`)}
          <article class="metric">
            <div class="metric-top">
              <div class="metric-label"><PageText blockId={block.id} field={`items.${itemIndex}.label`} value={item.label} label="Metric label" update={(current, raw) => current.type === "metrics" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, label: raw} : entry)} : current} /></div>
              {#if item.change}
                <div class="metric-change" data-tone={item.tone ?? "neutral"}><PageText blockId={block.id} field={`items.${itemIndex}.change`} value={item.change} label="Metric change" update={(current, raw) => current.type === "metrics" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, change: raw} : entry)} : current} /></div>
              {/if}
            </div>
            <div class="metric-value"><PageText blockId={block.id} field={`items.${itemIndex}.value`} value={item.value} label="Metric value" update={(current, raw) => current.type === "metrics" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, value: raw} : entry)} : current} /></div>
            {#if item.hint}<div class="metric-hint"><PageText blockId={block.id} field={`items.${itemIndex}.hint`} value={item.hint} label="Metric hint" update={(current, raw) => current.type === "metrics" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, hint: raw} : entry)} : current} /></div>{/if}
          </article>
        {/each}
      </div>
    {:else if block.type === "table"}
      <PageTable {block} />
    {:else if block.type === "properties"}
      <dl class="facts" data-block="properties" data-count={block.items.length}>
        {#each block.items as item, itemIndex (itemIndex)}
          <div class="fact">
            <dt><PageText blockId={block.id} field={`items.${itemIndex}.label`} value={item.label} label="Property label" update={(current, raw) => current.type === "properties" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, label: raw} : entry)} : current} /></dt>
            <dd><PageText blockId={block.id} field={`items.${itemIndex}.value`} value={item.value} label="Property value" update={(current, raw) => current.type === "properties" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, value: raw} : entry)} : current} /></dd>
          </div>
        {/each}
      </dl>
    {:else if block.type === "status"}
      <div class="status" data-block="status" data-tone={block.tone}><PageText blockId={block.id} field="label" value={block.label} label="Status label" update={(current, raw) => current.type === "status" ? {...current, label: raw} : current} /></div>
    {:else if block.type === "checklist"}
      <PageChecklist {block} />
    {:else if block.type === "grid"}
      <div
        class="grid"
        data-block="grid"
        data-columns={block.columns ?? 2}
        data-joined={block.joined ? "true" : "false"}
        data-align={block.align ?? "stretch"}
      >
        {#each block.blocks as child, childIndex (child.id)}
          <div class="grid-cell">
            {@render insetView([child])}
          </div>
        {/each}
      </div>
    {:else if block.type === "segmented"}
      <PageSegmented blockId={block.id} views={block.views} body={view} />
    {:else if block.type === "chart"}
      <div data-block="chart">
        <PageChart {block} {inset} />
      </div>
    {:else if block.type === "rows"}
      <ul class="rows" data-block="rows">
        {#each block.items as item, itemIndex (`row-${itemIndex}`)}
          <li class="row">
            <div class="row-copy">
              <div class="row-title"><PageText blockId={block.id} field={`items.${itemIndex}.title`} value={item.title} label="Row title" update={(current, raw) => current.type === "rows" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, title: raw} : entry)} : current} /></div>
              {#if item.detail}<div class="row-detail"><PageText blockId={block.id} field={`items.${itemIndex}.detail`} value={item.detail} label="Row detail" update={(current, raw) => current.type === "rows" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, detail: raw} : entry)} : current} /></div>{/if}
            </div>
            {#if item.status || item.meta}
              <div class="row-aside" data-tone={item.tone ?? "neutral"}>
                {#if item.status}<div class="row-status"><PageText blockId={block.id} field={`items.${itemIndex}.status`} value={item.status} label="Row status" update={(current, raw) => current.type === "rows" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, status: raw} : entry)} : current} /></div>{/if}
                {#if item.meta}<div class="row-meta"><PageText blockId={block.id} field={`items.${itemIndex}.meta`} value={item.meta} label="Row meta" update={(current, raw) => current.type === "rows" ? {...current, items: current.items.map((entry, position) => position === itemIndex ? {...entry, meta: raw} : entry)} : current} /></div>{/if}
              </div>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  {/each}
</div>

<style>
  .blocks {
    display: flex;
    flex-direction: column;
    gap: var(--space-6);
    min-width: 0;
  }
  .blocks > :global(.section) ~ :global(.section) {
    margin-top: calc(var(--space-7) - var(--space-6));
  }

  .text {
    margin: 0;
    color: var(--text-default);
    font-size: var(--type-prose-lg);
    font-weight: 400;
    line-height: 1.6;
  }
  .text :global(p) {
    white-space: pre-wrap;
  }
  .text :global(.file-preview-slot),
  .text :global(.file-preview-slot p) {
    white-space: normal;
  }

  .callout {
    margin: 0;
    padding: var(--space-5);
    border: 0;
    border-radius: var(--space-5);
    background: var(--surface-recessed);
  }
  .callout[data-tone="attention"] { background: var(--accent-surface); }
  .callout[data-tone="bad"] { background: var(--error-surface); }
  .callout[data-tone="good"] {
    background: color-mix(in srgb, var(--accent-done) 12%, var(--surface-recessed));
  }
  .callout-title {
    margin: 0 0 var(--space-2);
    color: var(--text-strong);
    font-size: var(--type-md);
    font-weight: 500;
    line-height: 1.4;
  }
  .callout[data-tone="attention"] .callout-title { color: var(--accent-bright); }
  .callout[data-tone="bad"] .callout-title { color: var(--accent-error); }
  .callout[data-tone="good"] .callout-title { color: var(--accent-done); }
  .callout-text {
    margin: 0;
    color: var(--text-default);
    font-size: var(--type-md);
    font-weight: 400;
    line-height: 1.5;
    white-space: pre-wrap;
  }

  .metrics {
    display: grid;
    gap: var(--space-4);
    align-items: stretch;
  }
  .metrics[data-count="1"] { grid-template-columns: minmax(0, 1fr); }
  .metrics[data-count="2"] { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metrics[data-count="3"] { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .metrics[data-count="4"] { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .metrics[data-count="5"] { grid-template-columns: repeat(5, minmax(0, 1fr)); }
  .metrics[data-count="6"] { grid-template-columns: repeat(6, minmax(0, 1fr)); }
  .metric {
    min-width: 0;
    padding: var(--space-5);
    border-radius: var(--space-5);
    background: var(--surface-recessed);
  }
  .metric-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-3);
  }
  .metric-label {
    margin: 0;
    color: var(--text-muted);
    font-size: var(--type-sm);
    font-weight: 400;
    line-height: 1.4;
  }
  .metric-change {
    margin: 0;
    flex: none;
    padding: 2px var(--space-2);
    border-radius: var(--radius-pill);
    background: var(--surface-2);
    color: var(--text-muted);
    font-size: var(--type-xs);
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    line-height: 1.4;
  }
  .metric-change[data-tone="good"] {
    background: color-mix(in srgb, var(--accent-done) 18%, var(--surface-2));
    color: var(--accent-done);
  }
  .metric-change[data-tone="bad"] {
    background: color-mix(in srgb, var(--accent-error) 18%, var(--surface-2));
    color: var(--accent-error);
  }
  .metric-change[data-tone="attention"] {
    background: var(--accent-surface);
    color: var(--accent-bright);
  }
  .metric-value {
    margin: var(--space-4) 0 0;
    color: var(--text-strong);
    font-size: var(--type-hero);
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    letter-spacing: var(--tracking-title);
    line-height: 1;
  }
  .metric-hint {
    margin: var(--space-2) 0 0;
    color: var(--text-faint);
    font-size: var(--type-xs);
    font-weight: 400;
    line-height: 1.4;
  }

  .facts {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5) var(--space-6);
    margin: 0;
  }
  .fact { min-width: 0; }
  .fact dt {
    margin: 0;
    color: var(--text-faint);
    font-size: var(--type-sm);
    font-weight: 400;
    line-height: 1.4;
  }
  .fact dd {
    margin: var(--space-1) 0 0;
    color: var(--text-strong);
    font-size: var(--type-md);
    font-weight: 500;
    line-height: 1.4;
    white-space: pre-wrap;
  }

  .status {
    margin: 0;
    min-height: var(--space-6);
    color: var(--text-default);
    font-size: var(--type-md);
    font-weight: 500;
    line-height: 1.4;
  }
  .status[data-tone="attention"] { color: var(--accent-bright); }
  .status[data-tone="good"] { color: var(--accent-done); }
  .status[data-tone="bad"] { color: var(--accent-error); }

  .grid {
    display: grid;
    gap: var(--space-6);
    align-items: start;
    min-width: 0;
  }
  .grid[data-align="stretch"] { align-items: stretch; }
  /* A chart stops being readable before it stops fitting. Its date axis carries one label
     per 76px and needs three to say anything, so the plot needs 228px and the value gutter
     the rest. Below that a cell wraps to the next row: two charts and one beats three thin
     ones. The authored count stays the ceiling, and min() keeps a narrow page from overflowing. */
  .grid[data-columns] {
    --grid-min: 280px;
    --grid-gap: var(--space-6);
    grid-template-columns: repeat(auto-fit, minmax(min(100%, max(var(--grid-min), (100% - (var(--grid-columns) - 1) * var(--grid-gap)) / var(--grid-columns))), 1fr));
  }
  .grid[data-columns="2"] { --grid-columns: 2; }
  .grid[data-columns="3"] { --grid-columns: 3; }
  .grid[data-columns="4"] { --grid-columns: 4; }
  /* A joined grid draws its own dividers from the column count, so it keeps that count. */
  .grid[data-joined="true"] { --grid-gap: 0px; }
  .grid[data-joined="true"][data-columns="2"] { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid[data-joined="true"][data-columns="3"] { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .grid[data-joined="true"][data-columns="4"] { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .grid-cell { min-width: 0; }
  .grid[data-joined="true"] {
    gap: 0;
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--space-6);
    overflow: hidden;
  }
  .grid[data-joined="true"] .grid-cell {
    padding: var(--space-5);
    border-top: var(--border-hairline) solid var(--border-color);
    border-left: var(--border-hairline) solid var(--border-color);
  }
  .grid[data-joined="true"][data-columns="2"] .grid-cell:nth-child(-n + 2),
  .grid[data-joined="true"][data-columns="3"] .grid-cell:nth-child(-n + 3),
  .grid[data-joined="true"][data-columns="4"] .grid-cell:nth-child(-n + 4) { border-top: 0; }
  .grid[data-joined="true"][data-columns="2"] .grid-cell:nth-child(2n + 1),
  .grid[data-joined="true"][data-columns="3"] .grid-cell:nth-child(3n + 1),
  .grid[data-joined="true"][data-columns="4"] .grid-cell:nth-child(4n + 1) { border-left: 0; }

  .rows { margin: 0; padding: 0; list-style: none; }
  .row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: var(--space-4) var(--space-6);
    align-items: start;
    padding: var(--space-4) 0;
  }
  .row + .row { border-top: var(--border-hairline) solid var(--border-color); }
  .row-copy { min-width: 0; }
  .row-title {
    margin: 0;
    color: var(--text-strong);
    font-size: var(--type-md);
    font-weight: 500;
    line-height: 1.4;
  }
  .row-detail {
    margin: 6px 0 0;
    color: var(--text-faint);
    font-size: var(--type-sm);
    font-weight: 400;
    line-height: 1.4;
  }
  .row-aside {
    min-width: 0;
    max-width: 11rem;
    padding-top: 2px;
    text-align: right;
  }
  .row-status {
    margin: 0;
    color: var(--text-default);
    font-size: var(--type-sm);
    font-weight: 500;
    line-height: 1.4;
  }
  .row-aside[data-tone="attention"] .row-status { color: var(--accent-bright); }
  .row-aside[data-tone="good"] .row-status { color: var(--accent-done); }
  .row-aside[data-tone="bad"] .row-status { color: var(--accent-error); }
  .row-meta {
    margin: 4px 0 0;
    color: var(--text-faint);
    font-size: var(--type-sm);
    font-weight: 400;
    line-height: 1.4;
  }

  @container page (min-width: 480px) {
    .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .facts[data-count="1"] { grid-template-columns: minmax(0, 1fr); }
  }
  @container page (max-width: 720px) {
    .metrics[data-count="4"] { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .metrics[data-count="6"] { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .grid[data-joined="true"][data-columns="4"] { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @container page (max-width: 640px) {
    .metrics[data-count="5"] { grid-template-columns: minmax(0, 1fr); }
  }
  @container page (max-width: 480px) {
    .metrics[data-count="3"] { grid-template-columns: minmax(0, 1fr); }
    .grid[data-joined="true"][data-columns="3"] { grid-template-columns: minmax(0, 1fr); }
    .grid[data-joined="true"][data-columns="3"] .grid-cell { border-left: 0; }
    .grid[data-joined="true"][data-columns="3"] .grid-cell:first-child { border-top: 0; }
    .grid[data-joined="true"][data-columns="3"] .grid-cell + .grid-cell {
      border-top: var(--border-hairline) solid var(--border-color);
    }
  }
  @container page (max-width: 420px) {
    .metrics[data-count="2"],
    .metrics[data-count="4"],
    .metrics[data-count="6"] { grid-template-columns: minmax(0, 1fr); }
    .grid[data-columns="2"],
    .grid[data-columns="4"] { grid-template-columns: minmax(0, 1fr); }
    .grid[data-joined="true"][data-columns="2"] .grid-cell,
    .grid[data-joined="true"][data-columns="4"] .grid-cell { border-left: 0; }
    .grid[data-joined="true"][data-columns="2"] .grid-cell:first-child,
    .grid[data-joined="true"][data-columns="4"] .grid-cell:first-child { border-top: 0; }
    .grid[data-joined="true"][data-columns="2"] .grid-cell + .grid-cell,
    .grid[data-joined="true"][data-columns="4"] .grid-cell + .grid-cell {
      border-top: var(--border-hairline) solid var(--border-color);
    }
  }
</style>
