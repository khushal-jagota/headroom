<script lang="ts">
  import { setContext } from 'svelte';
  import PageChart from '../pageKit/PageChart.svelte';
  import { PAGE_CONTEXT, type PageContext } from '../../lib/pageKit/context';
  import { createManualMeasurementExamples } from '../../lib/dev/manualMeasurementExamples';
  let revision = $state(0);
  let samples = $derived.by(() => { revision; return createManualMeasurementExamples(); });
  setContext<PageContext>(PAGE_CONTEXT, {
    goalId: 'dev-manual-samples', revision: 0, narrow: false, completed: false, status: 'neutral',
    get values() { return samples.values; },
    manualAdapter: measureId => samples.adapters.get(measureId)!,
  });
</script>

<section class="manual-page" data-manual-component-dev aria-label="Manual measurement examples">
  <header class="page-heading"><div><h1>Record progress</h1><p>Sample data · Changes stay on this page and do not save to measures.</p></div><button type="button" class="reset" onclick={() => revision++}>Reset</button></header>
  {#key revision}
    <div class="manual-grid">
      {#each samples.definitions as example, index (example.block.id)}
        <section data-example={example.block.id}>
          <div class="variant"><span>0{index + 1}</span><h2>{example.label}</h2></div>
          <PageChart block={example.block} />
        </section>
      {/each}
    </div>
  {/key}
</section>

<style>
  .manual-page { box-sizing:border-box;width:100%; max-width:1240px; margin:auto; padding:var(--space-6) var(--space-5) var(--space-7); color:var(--text-strong);font-family:var(--font-ui);font-size:var(--type-sm); }
  .page-heading { display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-6);gap:var(--space-4); }
  .page-heading>div { min-width:0; }
  h1 { font-size:var(--type-display);font-weight:550;letter-spacing:var(--tracking-title);margin:0; }
  .page-heading p { color:var(--text-faintest);font-size:var(--type-xs);margin:var(--space-2) 0 0; }
  .reset { border:0;background:none;color:var(--text-faint);padding:var(--space-2);border-radius:var(--radius-md);font:inherit;cursor:pointer; }
  .reset:hover { background:var(--interaction-hover);color:var(--text-strong); }
  .reset:focus-visible { outline:2px solid var(--accent-bright);outline-offset:4px; }
  .manual-grid { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--space-5);align-items:start; }
  .manual-grid>section { min-width:0; }
  .variant { display:flex;gap:var(--space-3);align-items:baseline;margin:0 0 var(--space-4); }
  .variant>span { color:var(--text-faintest);font-size:var(--type-xs); }
  .variant h2 { font-size:var(--type-sm);font-weight:400;color:var(--text-faint);margin:0; }
  @media(max-width:1000px) { .manual-grid { gap:var(--space-4); }.manual-page { padding:var(--space-5) var(--space-4); } }
  @media(max-width:850px) { .manual-grid { grid-template-columns:1fr;max-width:440px;margin:auto;gap:var(--space-6); }.page-heading { max-width:440px;margin:0 auto var(--space-6); } }
  @media(max-width:650px) { .manual-page { padding-inline:0; } }
</style>
