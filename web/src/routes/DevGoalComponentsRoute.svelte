<script lang="ts">
  // The unlisted app development route uses the same page and chart adapters as goals.
  import ManualMeasurementExamples from "../components/dev/ManualMeasurementExamples.svelte";
  import GoalComponentExample from "../components/dev/GoalComponentExample.svelte";
  import { goalComponentExamples } from "../lib/dev/goalComponentExamples";

  let narrowPreview = $state(false);
  const groups = [
    { title: "", note: "", layout: "main", keys: ["trend", "threshold", "weekly", "budget", "composition", "missing"] },
    { title: "Supporting measures", note: "The same component, smaller", layout: "supporting", keys: ["activation", "response", "signups"] },
    { title: "Mobile supporting charts", note: "Tap to expand", layout: "mobile", keys: ["mobile-conversion", "mobile-growth", "mobile-distribution", "mobile-retention", "mobile-allocation", "mobile-progress"] },
    { title: "Completion, progress and time", note: "", layout: "indicators", keys: ["completion", "single-progress", "time-budget", "time-allocation"] },
    { title: "Current state", note: "", layout: "gauges", keys: ["activation-gauge", "usage-gauge"] },
    { title: "Supporting records", note: "", layout: "records", keys: ["experiments-table"] },
  ];
</script>

<section class="goal-component-gallery" class:narrow-preview={narrowPreview} data-goal-component-dev>
  <header class="page-heading">
    <h1>Chart components</h1>
    <p>Goals · Sample data</p>
    <details class="preview-controls">
      <summary>About this development page</summary>
      <p>These 22 approved sheet examples use the application’s production page and chart components. All dates and values are synthetic samples from October 28, 2026. Sample controls change this page only. The optional mobile goal host preview also demonstrates production disclosure behavior beyond the sheet’s always-open supporting measures.</p>
      <p>Inspect plots with a pointer or arrow keys. Use Home, End, and Escape on charts, legend buttons for series and rings, and column headers to sort records.</p>
      <label><input type="checkbox" bind:checked={narrowPreview} /> Preview the mobile goal host (supporting charts collapse)</label>
    </details>
  </header>
  {#each groups as group (group.layout)}
    <section class="example-group" aria-label={group.title || "Main chart examples"}>
      {#if group.title}<div class="section-heading"><h2>{group.title}</h2>{#if group.note}<span>{group.note}</span>{/if}</div>{/if}
      <div class="example-grid" data-layout={group.layout}>
        {#each group.keys as key (key)}
          {@const example = goalComponentExamples.find(value => value.key === key)!}
          <GoalComponentExample {example} {narrowPreview} />
        {/each}
      </div>
    </section>
  {/each}
  <footer>Line charts, gauges, tables, and counters adapted from <a href="https://uiarc.dev/components/line-chart" target="_blank" rel="noreferrer">Arc</a>. Sample data.</footer>
</section>

<ManualMeasurementExamples />

<style>
  .goal-component-gallery { --document-channel-measure: 100%; box-sizing: border-box; width: 100%; max-width: 1160px; margin-inline: auto; padding: 40px 32px; color: var(--text-strong); font-family: var(--font-ui); }
  .page-heading { margin-bottom: 32px; }
  h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: -.02em; font-weight: 600; }
  .page-heading > p { margin: 0; color: var(--text-faintest); font-size: 13px; }
  .preview-controls { margin-top: 12px; color: var(--text-faintest); font-size: 11px; line-height: 1.5; }
  .preview-controls summary { cursor: pointer; width: fit-content; }
  .preview-controls p { max-width: 78ch; }
  .preview-controls label { display: flex; align-items: center; gap: 8px; }
  .preview-controls input { accent-color: var(--accent-bright); }
  .section-heading { display: flex; gap: 12px; align-items: baseline; margin: 40px 0 20px; }
  h2 { font-size: 18px; font-weight: 550; letter-spacing: -.012em; margin: 0; }
  .section-heading span { font-size: 12px; color: var(--text-faintest); }
  .example-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 28px 24px; }
  .example-grid[data-layout="supporting"], .example-grid[data-layout="mobile"] { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; align-items: start; }
  .example-grid[data-layout="indicators"], .example-grid[data-layout="gauges"] { gap: 24px; }
  .example-grid[data-layout="records"] { grid-template-columns: minmax(0, 1fr); }
  .example-grid[data-layout="main"] > :global([data-example="trend"]), .example-grid[data-layout="main"] > :global([data-example="missing"]) { grid-column: 1 / -1; }
  .narrow-preview { max-width: 390px; padding: 28px 16px; }
  .narrow-preview .example-grid { grid-template-columns: minmax(0, 1fr); gap: 24px; }
  footer { margin-top: 32px; color: var(--text-faintest); font-size: 11px; }
  footer a { color: inherit; text-underline-offset: 3px; }
  @media (max-width: 950px) { .example-grid[data-layout="mobile"] { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 900px) {
    .goal-component-gallery { padding: 32px 24px; }
    .example-grid[data-layout="supporting"] { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .example-grid[data-layout="supporting"] > :global(:last-child) { grid-column: 1 / -1; }
  }
  @media (max-width: 650px) {
    .goal-component-gallery { padding: 28px 0; }
    .example-grid, .example-grid[data-layout="supporting"], .example-grid[data-layout="mobile"] { grid-template-columns: minmax(0, 1fr); gap: 24px; }
    .section-heading { flex-wrap: wrap; gap: 8px; }
  }
</style>
