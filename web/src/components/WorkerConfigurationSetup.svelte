<script lang="ts">
  import SkeletonRegion from "./SkeletonRegion.svelte";
  import { createEmployeeConfigurationEditor } from "../lib/conversation/employeeConfigurationEditor.svelte";
  import type { EmployeeConfigurationSnapshot, TicketDetail } from "../lib/types";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import UnifiedModelPicker from "./conversation/UnifiedModelPicker.svelte";

  let {
    ticketId,
    employeeBackend,
    employeeLaunchModel,
    employeeLaunchReasoningEffort,
    onSave
  }: {
    ticketId: string;
    employeeBackend: string;
    employeeLaunchModel: string | null;
    employeeLaunchReasoningEffort: string | null;
    onSave: (configuration: EmployeeConfigurationSnapshot) => Promise<TicketDetail>;
  } = $props();

  const editor = createEmployeeConfigurationEditor(() => ({
    employee_backend: employeeBackend,
    employee_launch_model: employeeLaunchModel,
    employee_launch_reasoning_effort: employeeLaunchReasoningEffort
  }), (next) => onSave(next));
</script>

<div
  class="employee-configuration-setup"
  data-employee-configuration-setup
  data-ticket-id={ticketId}
  data-employee-configuration-backend={editor.selected.employee_backend}
  data-employee-configuration-model={editor.selected.employee_launch_model ?? ""}
  data-employee-configuration-reasoning={editor.selected.employee_launch_reasoning_effort ?? ""}
>
  <span class="employee-configuration-key">runs on</span>
  <UnifiedModelPicker
    view={editor.picker}
    bind:snapshots={editor.backends}
    models={editor.snapshot?.available_models ?? []}
    backendEffortOptions={editor.snapshot?.reasoning_effort_options ?? []}
    below
    disabled={editor.saving}
    keepOpenWhenDisabled
    attributes={{ "data-employee-configuration-picker": "" }}
    onChooseBackend={editor.chooseBackend}
    onChooseModel={editor.chooseModel}
  />

  <span data-employee-configuration-loading={editor.loading || undefined}>
    <SkeletonRegion shape="inline" loading={editor.loading} loadingText="Loading models…" />
  </span>

  {#if editor.backendsError}
    <div class="employee-configuration-problem" data-employee-configuration-error>
      <ErrorLine error={editor.backendsError} />
      <Button
        variant="quiet"
        data-employee-configuration-retry
        disabled={editor.loading}
        onclick={() => void editor.loadBackends()}
      >Retry</Button>
    </div>
  {/if}

  {#if editor.saveError}
    <div class="employee-configuration-problem" data-employee-configuration-save-error>
      <ErrorLine error={editor.saveError} />
    </div>
  {/if}
</div>

<style>
  .employee-configuration-setup { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
  .employee-configuration-key {
    color: var(--text-faintest); font-family: var(--font-mono); font-size: var(--type-xs);
  }
  .employee-configuration-problem { flex-basis: 100%; display: flex; align-items: center; gap: var(--space-2); }
</style>
