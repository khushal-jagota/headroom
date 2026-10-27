<script lang="ts">
  import { untrack } from 'svelte';
  import { queryClient } from '../queryClient';
  import { createQuery } from '@tanstack/svelte-query';
  import ResourceState from '../../components/ResourceState.svelte';
  import { resourceStateForQueries } from '../resourceStateForQueries';
  import { queries } from '../queryCatalogue';
  import { mutateJson } from '../mutate';
  import CredentialForm from './CredentialForm.svelte';
  import { failureLabel, fixedWriteError, runRevision, timestamp } from './presentation';
  import type { CredentialMetadata, RunMode, RunRequestBody, SourceScheduleBody, SourceStatus } from './wire';

  let { status }: { status: SourceStatus } = $props();
  const source = createQuery(() => queries.measureSource(status.source.id));
  let runOffset = $state(0);
  let credentialOffset = $state(0);
  const runs = createQuery(() => queries.measureSourceRuns(status.source.id, runOffset));
  const credentials = createQuery(() => queries.sourceCredentials(status.source.scope.project_id, status.source.scope.account, credentialOffset));
  let selectedRun = $state('');
  const run = createQuery(() => ({ ...queries.measureSourceRun(selectedRun), enabled: !!selectedRun }));
  let credentialForm = $state<{ existing: CredentialMetadata | null } | null>(null);
  let busy = $state(false);
  let error = $state('');
  let localTime = $state(untrack(() => status.source.local_time ?? ''));
  let enabled = $state(untrack(() => status.source.enabled));
  let notice = $state('');
  let scheduleRevision = $state(untrack(() => status.source.source_revision));
  let dirty = $state(false);
  $effect(() => {
    const current = status.source;
    if (!dirty) { localTime = current.local_time ?? ''; enabled = current.enabled; scheduleRevision = current.source_revision; }
  });
  async function schedule(event: SubmitEvent) {
    event.preventDefault(); busy = true; error = ''; notice = '';
    const body: SourceScheduleBody = { expected_revision: scheduleRevision, enabled, local_time: localTime || null };
    try {
      await mutateJson(`/api/measure-sources/${encodeURIComponent(status.source.id)}/schedule`, { method: 'PUT', body });
      dirty = false; notice = 'Schedule saved.';
    } catch (failure) { error = fixedWriteError(failure); }
    finally { busy = false; }
  }
  async function requestRun(mode: RunMode) {
    const revision = runRevision(status, mode);
    if (revision === null) return;
    busy = true; error = ''; notice = '';
    const body: RunRequestBody = { mode, expected_script_revision: revision, history: null };
    try {
      await mutateJson(`/api/measure-sources/${encodeURIComponent(status.source.id)}/runs`, { method: 'POST', body });
      notice = mode === 'test' ? 'Draft test requested. Test values are not saved as observations.' : 'Collection requested.';
    } catch (failure) { error = fixedWriteError(failure); }
    finally { busy = false; }
  }
</script>
<section class="source-controls" aria-label={`Controls for ${status.source.name}`}>
  <h3>Schedule and runs</h3>
  <form onsubmit={schedule} class="schedule">
    <label><input type="checkbox" bind:checked={enabled} disabled={busy} onchange={() => dirty = true} /> Active</label>
    <label>Daily time (server local time) <input type="time" bind:value={localTime} disabled={busy} onchange={() => dirty = true} /></label>
    <button disabled={busy} type="submit">Save schedule</button>
  </form>
  <div class="actions"><button disabled={busy || runRevision(status, 'collection') === null} onclick={() => requestRun('collection')}>Collect now</button>
    <button disabled={busy || runRevision(status, 'test') === null} onclick={() => requestRun('test')}>Test draft</button></div>
  {#if status.source.active_script_revision === null}<p>No published script. Publish through the API or CLI before collection.</p>{/if}
  {#if notice}<p role="status">{notice}</p>{/if}
  {#if error}<p role="alert">{error} <button onclick={() => { dirty = false; void queryClient.invalidateQueries(); }}>Discard edits and refresh</button></p>{/if}

  <h3>Credentials</h3>
  <ResourceState shape="block" {...resourceStateForQueries(credentials)} loadingText="Loading credential metadata…">
    {#each credentials.data?.credentials ?? [] as credential (credential.id)}
      <div class="credential-row"><span>{credential.reference.name} · {credential.auth_kind} · Configured</span>
        <button onclick={() => credentialForm = { existing: credential }}>Replace {credential.reference.name}</button></div>
    {/each}
    {#if !credentials.data?.credentials.length}<p>No credentials configured for this Project and account.</p>{/if}
    <div class="actions"><button onclick={() => credentialForm = { existing: null }}>Add credential</button>
      {#if credentialOffset > 0}<button onclick={() => credentialOffset = Math.max(0, credentialOffset - 20)}>Previous credentials</button>{/if}
      {#if credentials.data?.page.next_offset != null}<button onclick={() => credentialOffset = credentials.data?.page.next_offset ?? 0}>More credentials</button>{/if}</div>
  </ResourceState>
  {#if credentialForm}
    {#key credentialForm}
      <CredentialForm scope={status.source.scope} existing={credentialForm.existing}
        suggestedName={source.data?.destinations.find(item => item.credential)?.credential?.name ?? ''}
        suggestedOrigins={[...new Set(source.data?.destinations.map(item => item.origin) ?? [])]}
        onclose={() => credentialForm = null} />
    {/key}
  {/if}

  <h3>Run history</h3>
  <ResourceState shape="block" {...resourceStateForQueries(runs)} loadingText="Loading runs…">
    {#each runs.data?.runs ?? [] as item (item.id)}
      <div class="run-row"><span>{item.mode === 'test' ? 'Draft test' : 'Collection'} · {item.status} · {timestamp(item.created_at)}</span><button onclick={() => selectedRun = selectedRun === item.id ? '' : item.id}>Run details</button></div>
    {/each}
    {#if !runs.data?.runs.length}<p>No runs yet.</p>{/if}
    <div class="actions">{#if runOffset > 0}<button onclick={() => runOffset = Math.max(0, runOffset - 10)}>Newer runs</button>{/if}
      {#if runs.data?.page.next_offset != null}<button onclick={() => runOffset = runs.data?.page.next_offset ?? 0}>Older runs</button>{/if}</div>
  </ResourceState>
  {#if selectedRun}
    <ResourceState shape="block" {...resourceStateForQueries(run)} loadingText="Loading run details…">
      {#if run.data}
        <div class="run-detail"><p>{run.data.run.mode === 'test' ? 'Draft test — candidate values are not committed.' : 'Collection'} · {run.data.run.status}</p>
          {#if run.data.run.failure}<p>{failureLabel(run.data.run.failure)}</p>{/if}
          <p>{run.data.run.observation_count} observations · script revision {run.data.run.script_revision}</p>
          {#if run.data.run.metrics}<p>{run.data.run.metrics.elapsed_milliseconds} ms · {run.data.run.metrics.request_count} requests · {run.data.run.metrics.response_bytes.toLocaleString()} response bytes</p>{/if}
          {#if run.data.test_candidates}<p>{run.data.test_candidates.length} candidate values. Tests do not update collection health.</p>
            <ul>{#each run.data.test_candidates.slice(0, 10) as candidate}
              <li>{status.latest_values.find(item => item.measure_id === candidate.measure_id)?.name ?? 'Output'} · {candidate.date} · {candidate.value ?? 'History unavailable'}</li>
            {/each}</ul>
            {#if run.data.test_candidates.length > 10}<p>Showing the first ten candidates.</p>{/if}
          {/if}
        </div>
      {/if}
    </ResourceState>
  {/if}
</section>
<style>
  .source-controls { border:var(--border-hairline) solid var(--border-color); border-top:0; padding:var(--space-4); display:grid; gap:var(--space-3); }
  h3,p { margin:0; } h3 { font-size:var(--type-sm); } p { color:var(--text-muted); }
  .actions,.schedule,.credential-row,.run-row { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-3); }
  .credential-row,.run-row { justify-content:space-between; padding:var(--space-2) 0; }
  label { display:flex; align-items:center; gap:var(--space-2); }.run-detail { padding:var(--space-3); border-left:2px solid var(--border-color); }
</style>
