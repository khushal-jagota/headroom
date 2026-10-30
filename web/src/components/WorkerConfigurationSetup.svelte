<script lang="ts">
  import type { ModelPickerDisplaySelection } from "../lib/conversation/modelPickerFacts";
  import type { BackendSnapshot, ConversationBackendKey } from "../lib/conversation/wire";
  import { resolveModelPicker } from "../lib/conversation/modelPicker";
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
    snapshotLoading = false,
    configurationSaving = false,
    snapshotError = null,
    configurationSaveError = null,
    scopedBackends,
    displaySelection,
    onChooseCustomModel,
    onChooseBackend,
    onRetry,
    ticketId,
    employeeBackend,
    employeeLaunchModel,
    employeeLaunchReasoningEffort,
    onSave
  }: {
    destination?: TicketLocale;
    locked?: boolean;
    snapshotLoading?: boolean;
    configurationSaving?: boolean;
    snapshotError?: unknown;
    configurationSaveError?: unknown;
    scopedBackends?: readonly BackendSnapshot[];
    displaySelection?: ModelPickerDisplaySelection;
    onChooseCustomModel?: (model: string, effort: string | null) => void;
    onChooseBackend?: (backend: ConversationBackendKey) => void;
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
  }), (next) => onSave(next), () => scopedBackends === undefined);
  let selected = $derived(displaySelection ?? {
    backendKey: employeeBackend as ConversationBackendKey,
    model: employeeLaunchModel, reasoningEffort: employeeLaunchReasoningEffort
  });
  let scopedPicker = $derived(resolveModelPicker({
    backendKey: selected.backendKey, model: selected.model,
    reasoningEffort: selected.reasoningEffort, backends: scopedBackends ?? []
  }));
  let scopedSnapshot = $derived(scopedBackends?.find(item => item.backend_key === selected.backendKey));
  let scoped = $derived(scopedBackends !== undefined);
  let loading = $derived(scoped ? snapshotLoading : editor.loading);
  let saving = $derived(scoped ? configurationSaving : editor.saving);
  let readError = $derived(scoped ? snapshotError : editor.backendsError);
  let saveError = $derived(scoped ? configurationSaveError : editor.saveError);
</script>

<div
  class="employee-configuration-setup"
  data-employee-configuration-setup
  data-ticket-id={ticketId}
  data-employee-configuration-backend={scoped ? displaySelection?.backendKey ?? employeeBackend : editor.selected.employee_backend}
  data-employee-configuration-model={scoped ? displaySelection?.model ?? employeeLaunchModel ?? "" : editor.selected.employee_launch_model ?? ""}
  data-employee-configuration-reasoning={scoped ? (displaySelection ? displaySelection.reasoningEffort ?? "" : employeeLaunchReasoningEffort ?? "") : editor.selected.employee_launch_reasoning_effort ?? ""}
>
  <span class="employee-configuration-key">runs on</span>
  {#if destination}<span class="machine" aria-label={`Runs on ${machineName(destination)}`}><MachineSymbol locale={destination} />{machineName(destination)}</span>{/if}
  <UnifiedModelPicker
    view={scoped ? scopedPicker : editor.picker}
    snapshots={scopedBackends ?? editor.backends}
    models={(scoped ? scopedSnapshot : editor.snapshot)?.available_models ?? []}
    backendEffortOptions={(scoped ? scopedSnapshot : editor.snapshot)?.reasoning_effort_options ?? []}
    below
    chevron="none"
    scrollPanel
    displaySelection={scoped ? displaySelection ?? {
      backendKey: employeeBackend as ConversationBackendKey,
      model: employeeLaunchModel, reasoningEffort: employeeLaunchReasoningEffort
    } : undefined}
    {onRetry}
    disabled={locked || saving || (scopedBackends !== undefined && (!onChooseCustomModel || !onChooseBackend))}
    keepOpenWhenDisabled
    attributes={{ "data-employee-configuration-picker": "" }}
    onChooseBackend={(backend, defaults) => scopedBackends ? onChooseBackend?.(backend) : editor.chooseBackend(backend, defaults)}
    onChooseModel={(model, effort) => scoped ? onChooseCustomModel?.(model, effort) : editor.chooseModel(model, effort)}
  />

  <span data-employee-configuration-loading={loading || undefined}>
    <SkeletonRegion shape="inline" {loading} loadingText="Loading models…" />
  </span>

  {#if readError}
    <div class="employee-configuration-problem" data-employee-configuration-error>
      <ErrorLine error={readError} />
      <Button
        variant="quiet"
        data-employee-configuration-retry
        disabled={loading}
        onclick={() => void (scoped ? onRetry?.() : editor.loadBackends())}
      >Retry</Button>
    </div>
  {/if}

  {#if saveError}
    <div class="employee-configuration-problem" data-employee-configuration-save-error>
      <ErrorLine error={saveError} />
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
