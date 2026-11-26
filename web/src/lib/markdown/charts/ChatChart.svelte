<script lang="ts">
  import { onMount, setContext } from 'svelte';
  import PageChart from '../../../components/pageKit/PageChart.svelte';
  import { PAGE_CONTEXT } from '../../pageKit/context';
  import type { ChartBlock } from '../../goals/contracts';

  const CHART_DRAWING_WAIT_MS = 1_000;
  let { block, identity }: { block: ChartBlock; identity: string } = $props();
  let host: HTMLDivElement;
  let narrow = $state(false);
  // React draws the chart after this mounts. Until its first drawing is in place the chart
  // is busy, so an opening conversation waits for its height instead of jumping after it.
  let drawn = $state(false);
  // Chat snapshots never inherit a surrounding goal's values or projection.
  setContext(PAGE_CONTEXT, undefined);
  onMount(() => {
    const observer = new ResizeObserver(entries => {
      narrow = entries[0].contentRect.width <= 640;
    });
    observer.observe(host);
    // The drawing is in place once it exists and holds its height across a frame: a narrow
    // column draws once more after its width is measured.
    let frame = 0;
    let height = -1;
    const settle = (): void => {
      frame = 0;
      // Nothing is drawn yet: the drawing's own mutation starts the wait again.
      if (host.querySelector('.goal-chart-host > *') === null) { height = -1; return; }
      const next = host.getBoundingClientRect().height;
      if (next === height) {
        drawn = true;
        drawing.disconnect();
        return;
      }
      height = next;
      frame = requestAnimationFrame(settle);
    };
    const finish = (): void => {
      if (drawn || frame !== 0) return;
      frame = requestAnimationFrame(settle);
    };
    const drawing = new MutationObserver(finish);
    drawing.observe(host, { childList: true, subtree: true });
    finish();
    // A drawing that fails leaves the host empty. Busy ends anyway, so the open is not held.
    const limit = setTimeout(() => { drawn = true; drawing.disconnect(); cancelAnimationFrame(frame); }, CHART_DRAWING_WAIT_MS);
    return () => { observer.disconnect(); drawing.disconnect(); cancelAnimationFrame(frame); clearTimeout(limit); };
  });
</script>

<div class="chat-chart" bind:this={host} aria-busy={drawn ? undefined : "true"}>
  <PageChart {block} {narrow} values={[]} completed={false} status="neutral" goalId={identity} wide chatSnapshot />
</div>

<style>
  .chat-chart { width: 100%; min-width: 0; }
</style>
