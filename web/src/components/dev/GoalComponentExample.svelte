<script lang="ts">
  import { setContext, untrack } from "svelte";
  import PageDocument from "../pageKit/PageDocument.svelte";
  import { PAGE_CONTEXT, type PageContext } from "../../lib/pageKit/context";
  import type { PageDocument as PageDocumentValue } from "../../lib/goals/contracts";
  import type { GoalComponentExample } from "../../lib/dev/goalComponentExamples";

  let { example, narrowPreview = false }: { example: GoalComponentExample; narrowPreview?: boolean } = $props();
  let sampleId = $state(untrack(() => example.initial));
  let sample = $derived(example.samples.find(value => value.id === sampleId) ?? example.samples[0]);
  let narrow = $derived(example.mobile || narrowPreview);
  let compact = $derived(["activation", "response", "signups"].includes(example.key));
  let document = $derived<PageDocumentValue>({ kind: "headroom.page", version: 1, title: example.label, summary: "Sample data", blocks: [sample.block] });

  setContext<PageContext>(PAGE_CONTEXT, {
    get goalId() { return `dev-sheet-${example.key}`; },
    revision: 0,
    get narrow() { return narrow; },
    values: [],
    get completed() { return sample.completed ?? false; },
    get status() { return sample.status ?? "neutral"; },
  });

</script>

<section class="example" class:mobile-study={example.mobile} class:indicator-example={["completion", "single-progress", "time-budget", "time-allocation"].includes(example.key)} data-example={example.key} aria-label={example.label}>
  {#if !example.mobile && !compact}
  <header class="example-heading">
    <span>{example.label.split(" — ")[0]}</span>
    {#if example.samples.length > 1}
      <select bind:value={sampleId} aria-label={`${example.label.split(" — ")[0]} example`}>
          {#each example.samples as state (state.id)}<option value={state.id}>{state.label}</option>{/each}
        </select>
    {/if}
  </header>
  {/if}
  <div class="example-host">
    <PageDocument {document} showHeader={false} />
  </div>
</section>

<style>
  .example { min-width: 0; display: flex; flex-direction: column; }
  .example-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--text-faintest); font-size: 11px; min-height: 28px; margin-bottom: 8px; padding: 0 8px; }
  select { background: var(--chart-sample-control); border: 0; color: var(--text-faint); font: inherit; padding: 4px 8px; border-radius: 4px; max-width: 100%; }
  select:focus-visible { outline: 2px solid var(--accent-bright); outline-offset: 4px; }
  .example-host { width: 100%; }
  .indicator-example .example-host { flex: 1; }
  .indicator-example .example-host :global(.page-kit), .indicator-example .example-host :global(.page-body), .indicator-example .example-host :global([data-page-blocks]), .indicator-example .example-host :global([data-block="chart"]), .indicator-example .example-host :global(.goal-chart-host) { height: 100%; }
  .indicator-example .example-host :global(.indicator-card) { height: 100%; }
</style>
