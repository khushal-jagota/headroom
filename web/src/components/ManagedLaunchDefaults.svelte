<script lang="ts">
  import { createEmployeeConfigurationEditor } from "../lib/conversation/employeeConfigurationEditor.svelte";
  import type { EmployeeConfigurationSnapshot } from "../lib/types";
  import ErrorLine from "./ErrorLine.svelte";
  import UnifiedModelPicker from "./conversation/UnifiedModelPicker.svelte";

  let {
    label,
    value,
    onSave
  }: {
    label: string;
    value: EmployeeConfigurationSnapshot;
    onSave: (next: EmployeeConfigurationSnapshot) => Promise<EmployeeConfigurationSnapshot>;
  } = $props();

  const editor = createEmployeeConfigurationEditor(() => value, (next) => onSave(next));
</script>

<section class="worker-launch-defaults" data-launch-defaults data-launch-label={label}>
  <header class="worker-launch-defaults-head">
    <h2>{label} launch defaults</h2>
    <span class="worker-launch-defaults-note">applies to future launches</span>
  </header>
  <div class="worker-launch-defaults-controls">
    <span class="worker-launch-defaults-key">launches on</span>
    <UnifiedModelPicker
      view={editor.picker}
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
    {#if editor.loading}<span class="worker-launch-defaults-state">loading models…</span>{/if}
    {#if editor.backendsError}<ErrorLine error={editor.backendsError} />{/if}
  </div>
  {#if editor.saveError}<div class="worker-row-error" data-launch-defaults-error><ErrorLine error={editor.saveError} /></div>{/if}
</section>

<style>
  .worker-launch-defaults-controls { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
  .worker-launch-defaults-key, .worker-launch-defaults-state {
    color: var(--text-faintest); font-family: var(--font-mono); font-size: var(--type-xs);
  }
</style>
