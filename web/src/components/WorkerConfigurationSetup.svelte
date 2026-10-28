<script lang="ts">
  import type { PickerRow, CanonicalModelValidation } from "../lib/conversation/modelResolutionWire";
  import { machineName, type TicketLocale } from "../lib/ticketMachines";
  import ListboxPicker from "./conversation/ListboxPicker.svelte";
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
    canonicalRows,
    validation,
    onChooseDestination,
    onChooseCanonicalModel,
    onChooseCanonicalBackend,
    onRetry,
    ticketId,
    employeeBackend,
    employeeLaunchModel,
    employeeLaunchReasoningEffort,
    onSave
  }: {
    destination?: TicketLocale;
    locked?: boolean;
    canonicalRows?: readonly PickerRow[];
    validation?: CanonicalModelValidation | null;
    onChooseDestination?: (destination: TicketLocale) => void;
    onChooseCanonicalModel?: (row: PickerRow, effort: string | null) => void;
    onChooseCanonicalBackend?: (backend: CanonicalModelValidation["selection"]["backend_key"]) => void;
    onRetry?: () => Promise<void>;
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
  <ListboxPicker
    items={[{value: "macos", name: "Mac"}, {value: "vps", name: "VPS"}]}
    selectedValue={destination ?? null}
    disabled={locked || editor.saving || !onChooseDestination || !destination}
    label={destination ? `Runs on ${machineName(destination)}` : "Destination unavailable"}
    chevron="none"
    scrollPanel
    below
    attributes={{"data-employee-destination-picker": ""}}
    onChoose={(value) => onChooseDestination?.(value as TicketLocale)}
  >
    {#snippet triggerContent()}
      {#if destination}<MachineSymbol locale={destination} />{machineName(destination)}{:else}Destination unavailable{/if}
    {/snippet}
    {#snippet optionContent(choice)}<MachineSymbol locale={choice.value as TicketLocale} />{choice.name}{/snippet}
  </ListboxPicker>
  <UnifiedModelPicker
    view={editor.picker}
    bind:snapshots={editor.backends}
    models={editor.snapshot?.available_models ?? []}
    backendEffortOptions={editor.snapshot?.reasoning_effort_options ?? []}
    below
    chevron="none"
    scrollPanel
    {canonicalRows}
    {validation}
    {onRetry}
    disabled={locked || editor.saving || (canonicalRows !== undefined && (!onChooseCanonicalModel || !onChooseCanonicalBackend))}
    keepOpenWhenDisabled
    attributes={{ "data-employee-configuration-picker": "" }}
    onChooseBackend={(backend, defaults) => canonicalRows ? onChooseCanonicalBackend?.(backend) : editor.chooseBackend(backend, defaults)}
    onChooseModel={(model, effort, row) => row ? onChooseCanonicalModel?.(row, effort) : editor.chooseModel(model, effort)}
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
    color: var(--text-muted); font-family: var(--font-mono); font-size: var(--type-xs);
  }
  .employee-configuration-problem { flex-basis: 100%; display: flex; align-items: center; gap: var(--space-2); }
</style>
