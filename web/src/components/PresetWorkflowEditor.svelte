<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { untrack } from 'svelte';
  import { queries } from '../lib/queryCatalogue';
  import { mutateJson } from '../lib/mutate';
  import { copyWorkflow, workflowError, type WorkflowRecord } from '../lib/workflow';
  import type { WorkerTypeRecord } from '../lib/types';
  import WorkflowEditor from './WorkflowEditor.svelte';
  import ErrorLine from './ErrorLine.svelte';
  import Button from './Button.svelte';
  import ResourceState from './ResourceState.svelte';
  let { workerType }: { workerType: string } = $props();
  const stableType = untrack(() => workerType);
  const record = createQuery(() => queries.workerTypeRecord(stableType));
  let draft = $state<WorkflowRecord | null>(null);
  let initial = $state('');
  let saving = $state(false);
  let error = $state<unknown>(null);
  let dirty = $derived(draft !== null && JSON.stringify(draft) !== initial);
  $effect(() => {
    const data = record.data;
    if (data && !draft) {
      draft = copyWorkflow(data);
      initial = JSON.stringify(draft);
    }
  });
  async function save(): Promise<void> {
    if (!draft || !record.data || saving) return;
    error = workflowError(draft);
    if (error) return;
    saving = true;
    try {
      const result = await mutateJson<WorkerTypeRecord>('/api/worker-types', {
        method: 'POST', body: { ...record.data, ...copyWorkflow($state.snapshot(draft)) }
      });
      draft = copyWorkflow(result);
      initial = JSON.stringify(draft);
    } catch (caught) { error = caught; }
    finally { saving = false; }
  }
  function reset(): void {
    if (!record.data) return;
    draft = copyWorkflow(record.data);
    initial = JSON.stringify(draft);
    error = null;
  }
</script>

<section class="preset-workflow" data-preset-workflow>
  <header><h2>Workflow preset</h2><p>New Tickets copy these stages. Existing Tickets keep their own stages.</p></header>
  <ResourceState shape="document" loading={record.isFetching} hasData={Boolean(record.data)} error={record.error} loadingText="Loading workflow…">
    {#if draft}
      <WorkflowEditor value={draft} onChange={next => { draft = next; error = null; }} disabled={saving} />
      {#if error}<ErrorLine {error} />{/if}
      <footer>
        <Button variant="primary" disabled={!dirty || saving} onclick={() => void save()} data-save-workflow>{saving ? 'Saving…' : 'Save preset'}</Button>
        {#if dirty}<Button disabled={saving} onclick={reset}>Discard changes</Button>{/if}
      </footer>
    {/if}
  </ResourceState>
</section>

<style>
  .preset-workflow { margin-top: var(--space-7); }
  header { margin-bottom: var(--space-5); }
  h2 { font-size: var(--type-md); font-weight: 530; }
  p { margin-top: var(--space-2); color: var(--text-muted); font-size: var(--type-sm); }
  footer { display: flex; align-items: center; gap: var(--space-3); margin-top: var(--space-5); }
</style>
