<script lang="ts">
  import { queries } from "../lib/queryCatalogue";
  import type { ConversationStartValues } from "../lib/conversation/wire";
  import { machineName, type TicketLocale } from "../lib/ticketMachines";
  import MachineSymbol from "./machines/MachineSymbol.svelte";
  import SkeletonRegion from "./SkeletonRegion.svelte";
  import { createEmployeeConfigurationEditor } from "../lib/conversation/employeeConfigurationEditor.svelte";
  import type { EmployeeConfigurationSnapshot, TicketDetail } from "../lib/types";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import UnifiedModelPicker from "./conversation/UnifiedModelPicker.svelte";

  let {
    destination,
    locked = false,
    workspace,
    ticketId,
    employeeBackend,
    employeeLaunchModel,
    employeeLaunchReasoningEffort,
    onSave
  }: {
    destination?: TicketLocale;
    locked?: boolean;
    workspace?: ConversationStartValues | null;
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
  }), (next) => onSave(next), (refresh) => ({...queries.ticketBackends(ticketId,
    [destination, workspace?.workspace_folder, workspace?.workspace_path_policy], refresh),
    enabled: workspace?.workspace_folder !== undefined}));
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
  {#if destination}<span class="machine" aria-label={`Runs on ${machineName(destination)}`}><MachineSymbol locale={destination} />{machineName(destination)}</span>{/if}
  <UnifiedModelPicker
    view={editor.picker}
    bind:snapshots={editor.backends}
    models={editor.snapshot?.available_models ?? []}
    backendEffortOptions={editor.snapshot?.reasoning_effort_options ?? []}
    below chevron="none" scrollPanel
    readError={editor.backendsError ? String(editor.backendsError) : null}
    onRetry={() => editor.loadBackends()}
    disabled={locked || editor.saving}
    keepOpenWhenDisabled
    attributes={{ "data-employee-configuration-picker": "" }}
    onChooseBackend={(backend, defaults) => editor.chooseBackend(backend, defaults)}
    onChooseModel={(model, effort) => editor.chooseModel(model, effort)}
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
        onclick={() => void editor.loadBackends().catch(() => {})}
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
  .machine { display:inline-flex; align-items:center; gap:var(--space-2); }
  .employee-configuration-key {
    color: var(--text-muted); font-family: var(--font-mono); font-size: var(--type-xs);
  }
  .employee-configuration-problem { flex-basis: 100%; display: flex; align-items: center; gap: var(--space-2); }
</style>
