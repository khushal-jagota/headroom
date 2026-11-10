<script lang="ts">
  import { onMount, setContext } from 'svelte';
  import PageChart from '../../../components/pageKit/PageChart.svelte';
  import { PAGE_CONTEXT } from '../../pageKit/context';
  import type { ChartBlock } from '../../goals/contracts';

  let { block, identity }: { block: ChartBlock; identity: string } = $props();
  let host: HTMLDivElement;
  let narrow = $state(false);
  // Chat snapshots never inherit a surrounding goal's values or projection.
  setContext(PAGE_CONTEXT, undefined);
  onMount(() => {
    const observer = new ResizeObserver(entries => {
      narrow = entries[0].contentRect.width <= 640;
    });
    observer.observe(host);
    return () => observer.disconnect();
  });
</script>

<div class="chat-chart" bind:this={host}>
  <PageChart {block} {narrow} values={[]} completed={false} status="neutral" goalId={identity} wide />
</div>

<style>
  .chat-chart { width: 100%; min-width: 0; }
</style>
