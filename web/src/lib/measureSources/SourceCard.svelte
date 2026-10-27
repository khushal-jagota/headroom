<script lang="ts">
  import { failureLabel, healthLabels, timestamp } from './presentation';
  import type { SourceStatus } from './wire';
  let { status, projectName, selected, onselect }: {
    status: SourceStatus; projectName: string; selected: boolean; onselect: () => void;
  } = $props();
</script>
<article class="source-card" data-source-name={status.source.name}>
  <header><div><h2>{status.source.name}</h2><p>{projectName} · {status.source.scope.account}</p></div>
    <strong data-source-health={status.health}>{healthLabels[status.health]}</strong></header>
  <p>{status.source.enabled ? 'Daily schedule enabled' : 'Daily schedule disabled'} · {status.source.local_time ? `Daily at ${status.source.local_time} (server local time)` : 'No daily schedule'}</p>
  <p>Last collection success: <span>{timestamp(status.latest_success_at)}</span></p>
  {#if status.collection_failure}<p class="failure" data-collection-failure>{failureLabel(status.collection_failure)}</p>{/if}
  {#if status.latest_run}<p data-latest-run>Latest {status.latest_run.mode === 'test' ? 'script test' : 'collection'}: {status.latest_run.status}{status.latest_run.failure ? ` — ${failureLabel(status.latest_run.failure)}` : ''}</p>{/if}
  <dl>
    {#each status.latest_values as item (item.measure_id)}
      <div><dt>{item.name}</dt><dd>{#if item.observation}<strong>{item.observation.value.toLocaleString()} {item.unit}</strong><time>{item.observation.date}</time>{:else}<span>No observation yet</span>{/if}</dd></div>
    {/each}
  </dl>
  {#if !status.output_count}<p>No output measures configured.</p>{:else if status.output_count > status.latest_values.length}<p>Showing {status.latest_values.length} of {status.output_count} outputs.</p>{/if}
  <button type="button" onclick={onselect} aria-expanded={selected}>{selected ? 'Close controls' : 'Manage source'}</button>
</article>
<style>
  .source-card { padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); }
  header { display:flex; gap:var(--space-3); justify-content:space-between; flex-wrap:wrap; } h2 { margin:0; font-size:var(--type-md); }
  p { margin:var(--space-2) 0; color:var(--text-muted); } .failure { color:var(--text-strong); }
  dl { margin:var(--space-3) 0; display:grid; gap:var(--space-2); } dl>div { display:flex; justify-content:space-between; gap:var(--space-4); }
  dd { margin:0; text-align:right; } time { display:block; color:var(--text-muted); font-size:var(--type-xs); }
</style>
