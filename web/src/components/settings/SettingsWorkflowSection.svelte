<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { untrack } from "svelte";
  import { queries } from "../../lib/queryCatalogue";
  import { mutateJson } from "../../lib/mutate";
  import { copyWorkflow, workflowError, type WorkflowRecord } from "../../lib/workflow";
  import type { WorkerTypeRecord } from "../../lib/types";
  import { errorMessage } from "../../lib/ui";
  import Button from "../Button.svelte";
  import ResourceState from "../ResourceState.svelte";
  import WorkflowEditor from "../WorkflowEditor.svelte";
  import SettingsSection from "./SettingsSection.svelte";

  /* A Worker type's workflow preset: the stage editor, and while it holds edits, a bar
     that stays at the bottom of the page with Discard and Save. A save that is refused,
     or a workflow that is not valid yet, says why in that bar. */
  let { workerType, name }: { workerType: string; name: string } = $props();
  const stableType = untrack(() => workerType);
  const record = createQuery(() => queries.workerTypeRecord(stableType));
  let draft = $state<WorkflowRecord | null>(null);
  let initial = $state("");
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
      const result = await mutateJson<WorkerTypeRecord>("/api/worker-types", {
        method: "POST", body: { ...record.data, ...copyWorkflow($state.snapshot(draft)) }
      });
      draft = copyWorkflow(result);
      initial = JSON.stringify(draft);
    } catch (caught) { error = caught; }
    finally { saving = false; }
  }

  function discard(): void {
    if (!record.data) return;
    draft = copyWorkflow(record.data);
    initial = JSON.stringify(draft);
    error = null;
  }
</script>

<SettingsSection title="Workflow" note="New Tickets copy these stages. Existing Tickets keep their own stages." rows={false}>
  <div class="settings-workflow" data-preset-workflow>
    <ResourceState shape="document" loading={record.isFetching} hasData={Boolean(record.data)} error={record.error} loadingText="Loading workflow…">
      {#if draft}
        <WorkflowEditor value={draft} onChange={(next) => { draft = next; error = null; }} disabled={saving} />
      {/if}
    </ResourceState>
  </div>
  {#if dirty}
    <div class="settings-savebar" role="region" aria-label="Unsaved changes">
      <span class="settings-savebar-text">
        Unsaved changes to the {name} workflow
        {#if error}<span class="settings-savebar-error" role="alert">{errorMessage(error)}</span>{/if}
      </span>
      <Button variant="ghost" disabled={saving} onclick={discard}>Discard</Button>
      <Button variant="primary" disabled={saving} onclick={() => void save()} data-save-workflow>{saving ? "Saving…" : "Save"}</Button>
    </div>
  {/if}
</SettingsSection>

<style>
  .settings-workflow { margin-top:var(--space-4); }
  .settings-savebar { position:sticky; bottom:var(--space-4); z-index:5; display:flex; align-items:center; gap:var(--space-2); margin-top:var(--space-5); padding:var(--space-2) var(--space-2) var(--space-2) var(--space-4); background:var(--surface-raised); border:var(--border-hairline) solid var(--border-raised); border-radius:var(--radius-lg); box-shadow:var(--shadow-float); animation:settings-savebar-in var(--motion-base) var(--motion-ease); }
  .settings-savebar-text { flex:1; min-width:0; color:var(--tone-secondary); font-size:var(--type-row); }
  .settings-savebar-error { display:block; margin-top:2px; font-size:var(--type-small); color:var(--accent-error); }
  @keyframes settings-savebar-in { from { opacity:0; transform:translateY(var(--space-2)); } to { opacity:1; transform:none; } }
  @media (prefers-reduced-motion: reduce) { .settings-savebar { animation:none; } }
</style>
