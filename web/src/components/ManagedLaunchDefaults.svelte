<script lang="ts">
  import SkeletonRegion from "./SkeletonRegion.svelte";
  import { createEmployeeConfigurationEditor } from "../lib/conversation/employeeConfigurationEditor.svelte";
  import type { EmployeeConfigurationSnapshot } from "../lib/types";
  import ErrorLine from "./ErrorLine.svelte";
  import UnifiedModelPicker from "./conversation/UnifiedModelPicker.svelte";

  let {
    label,
    heading = `${label} launch defaults`,
    description,
    backendLocked = false,
    value,
    onSave
  }: {
    label: string;
    heading?: string;
    description?: string;
    backendLocked?: boolean;
    value: EmployeeConfigurationSnapshot;
    onSave: (next: EmployeeConfigurationSnapshot) => Promise<EmployeeConfigurationSnapshot>;
  } = $props();

  const editor = createEmployeeConfigurationEditor(() => value, (next) => onSave(next));
  let picker = $derived({...editor.picker, backendLocked, backends: editor.picker.backends.map((backend) => ({
    ...backend, unavailableReason: backendLocked && backend.key !== editor.picker.backendKey
      ? "To change the backend, start a new conversation." : backend.unavailableReason
  }))});
</script>

<section class="worker-launch-defaults" data-launch-defaults data-launch-label={label}>
  <header class="worker-launch-defaults-head">
    <h2>{heading}</h2>
  </header>
  {#if description}<p>{description}</p>{/if}
  {#if backendLocked}<p>To change the backend, start a new conversation.</p>{/if}
  <div class="worker-launch-defaults-controls">
    <UnifiedModelPicker
      view={picker}
      bind:snapshots={editor.backends}
      models={editor.snapshot?.available_models ?? []}
      backendEffortOptions={editor.snapshot?.reasoning_effort_options ?? []}
      below
      disabled={editor.saving}
      keepOpenWhenDisabled
      attributes={{ "data-launch-defaults-picker": "" }}
      onChooseBackend={editor.chooseBackend}
      onChooseModel={editor.chooseModel}
    />
    <SkeletonRegion shape="inline" loading={editor.loading} loadingText="Loading models…" />
    {#if editor.backendsError}<ErrorLine error={editor.backendsError} />{/if}
  </div>
  {#if editor.saveError}<div class="worker-row-error" data-launch-defaults-error><ErrorLine error={editor.saveError} /></div>{/if}
</section>

<style>
  .worker-launch-defaults-controls { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
</style>
