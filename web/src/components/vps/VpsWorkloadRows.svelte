<script lang="ts">
  import type { WorkloadRow } from '../../lib/vpsMetrics/contracts';
  import { formatBytes, formatPercent } from '../../lib/vpsMetrics/presentation';
  import VpsSkeleton from './VpsSkeleton.svelte';
  let { rows, kind, loading = false, unavailable = null }: {
    rows: WorkloadRow[]; kind: 'services' | 'processes'; loading?: boolean; unavailable?: string | null;
  } = $props();
</script>

<section aria-label={kind === 'services' ? 'Services' : 'Processes'}>
  <h2 class="section-heading">{kind === 'services' ? 'Services' : 'Processes'}
    <span class="section-heading-count">{kind === 'services' ? "of the machine’s memory" : 'by share of the machine'}</span>
  </h2>
  <div class="rows">
    {#if loading}
      {#each Array(kind === 'services' ? 3 : 5) as _}
        <div class="list-row" aria-hidden="true"><span class="list-row-title"><VpsSkeleton shape="detail" /></span><span class="row-value"><VpsSkeleton /></span>{#if kind === 'processes'}<span class="row-value"><VpsSkeleton /></span>{/if}</div>
      {/each}
    {:else if unavailable}
      <p class="quiet-line">{unavailable}</p>
    {:else if !rows.length}
      <p class="quiet-line">No {kind} reported</p>
    {:else}
      {#each rows as row (row.id)}
        <div class="list-row" data-vps-row={kind} data-vps-name={row.name}>
          {#if row.share !== null}<span class="row-share" style:width={`${Math.min(1, Math.max(0, row.share)) * 100}%`} aria-hidden="true"></span>{/if}
          <span class="list-row-title" title={row.name}>{row.name}{#if row.partial}<span class="row-partial"> · partial</span>{/if}</span>
          {#if kind === 'processes'}<span class="row-count">×{row.count}</span><span class="row-value" title={row.cpu.reason ?? undefined}>{row.cpu.value === null ? 'Unavailable' : `${formatPercent(row.cpu.value)} CPU`}{#if row.cpu.reason}<span class="vps-accessible">: {row.cpu.reason}</span>{/if}</span>{/if}
          <span class="row-value" title={row.memory.reason ?? undefined}>{row.memory.value === null ? 'Unavailable' : formatBytes(row.memory.value)}{#if row.memory.reason}<span class="vps-accessible">: {row.memory.reason}</span>{/if}</span>
          {#if kind === 'services'}<span class="row-value row-value--quiet" title={row.waiting?.reason ?? undefined}>{row.waiting?.value == null ? 'waiting unavailable' : `waiting ${formatPercent(row.waiting.value)}`}</span>{/if}
        </div>
      {/each}
    {/if}
  </div>
</section>
