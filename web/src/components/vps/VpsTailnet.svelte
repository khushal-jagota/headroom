<script lang="ts">
  import type { Sample, TailnetHistory } from '../../lib/vpsMetrics/contracts';
  import { pathBars } from '../../lib/vpsMetrics/logic/history';
  import { tailnetRates, tailnetRows } from '../../lib/vpsMetrics/presentation';
  import VpsSkeleton from './VpsSkeleton.svelte';
  import VpsTrend from './VpsTrend.svelte';
  let { sample, history, loading = false }: { sample: Sample | null; history: TailnetHistory | null; loading?: boolean } = $props();
  let rows = $derived(sample ? tailnetRows(sample) : []);
  let rates = $derived(sample ? tailnetRates(sample) : '');
  let unavailable = $derived(sample?.tailnet === undefined ? 'Not recorded by this machine' : sample.tailnet.availability === 'unavailable' ? `Unavailable: ${sample.tailnet.reason}` : null);
</script>

<section aria-label="Tailnet path" data-vps-metric="tailnet">
  <h2 class="section-heading">Tailnet path
    <span class="section-heading-count">{sample?.tailnet?.home_relay ? `home relay ${sample.tailnet.home_relay}` : 'how this machine reaches each device'}</span>
  </h2>
  <div class="rows">
    {#if loading}
      <div class="list-row" aria-hidden="true"><span class="list-row-title"><VpsSkeleton shape="detail" /></span><span class="row-value"><VpsSkeleton /></span></div>
    {:else if unavailable}
      <p class="quiet-line">{unavailable}</p>
    {:else if !rows.length}
      <p class="quiet-line">No devices on the tailnet</p>
    {:else}
      {#each rows as row (row.id)}
        {@const peer = history?.peers[row.id]}
        <div class="list-row path-row" data-vps-row="tailnet" data-vps-name={row.name} data-vps-path={row.direct === null ? 'none' : row.direct ? 'direct' : 'relayed'}>
          <span class="list-row-title" title={row.name}>{row.name}</span>
          <span class="row-value">{row.path}</span>
          <span class="row-value row-value--quiet">{row.handshake}</span>
          {#if history && peer && history.rates.end > history.rates.start}
            <svg class="path-strip" viewBox="0 0 960 24" preserveAspectRatio="none" shape-rendering="crispEdges" role="img" aria-label={`Path over time for ${row.name}: ${peer.changes.filter(point => (point.value ?? 0) > 0).length} changes`}>
              {#each pathBars(peer.direct, history.rates.start, history.rates.end, history.rates.gapSeconds) as bar}
                <rect class="path-bar" class:path-bar--direct={bar.share >= 0.99} class:path-bar--relayed={bar.share <= 0.01} x={bar.x * 960} width={Math.max(1, bar.width * 960)} y="6" height="12" />
              {/each}
              {#each peer.changes.filter(point => (point.value ?? 0) > 0) as change}
                <line class="path-change" x1={(change.timestamp - history.rates.start) / (history.rates.end - history.rates.start) * 960} x2={(change.timestamp - history.rates.start) / (history.rates.end - history.rates.start) * 960} y1="0" y2="24" />
              {/each}
            </svg>
          {/if}
        </div>
      {/each}
      {#if rates}<p class="quiet-line path-rates">{rates}</p>{/if}
      {#if history}<VpsTrend trend={history.rates} readingUnit="per s" primaryLabel="Relay packets" secondaryLabel="Path discovery" />{/if}
    {/if}
  </div>
</section>
