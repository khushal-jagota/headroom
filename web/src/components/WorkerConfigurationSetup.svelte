<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { resolveModelPicker } from "../lib/conversation/modelPicker";
  import {
    readBackends,
    type BackendSnapshot,
    type ConversationBackendKey
  } from "../lib/conversation/wire";
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

  let selectedBackend = $state("claude" as ConversationBackendKey);
  let selectedModel = $state<string | null>(null);
  let selectedReasoning = $state<string | null>(null);
  let lastIncomingSignature = $state("");
  let backends = $state<readonly BackendSnapshot[]>([]);
  let backendsLoading = $state(false);
  let backendsError = $state<unknown>(null);
  let saveError = $state<unknown>(null);
  let saving = $state(false);
  let requestGeneration = 0;

  let snapshot = $derived(
    backends.find((candidate) => candidate.backend_key === selectedBackend) ?? null
  );
  let picker = $derived(resolveModelPicker({
    backendKey: selectedBackend,
    model: selectedModel,
    reasoningEffort: selectedReasoning,
    backends
  }));

  async function loadBackends(): Promise<void> {
    const generation = ++requestGeneration;
    backendsError = null;
    backendsLoading = true;
    try {
      const answer = await readBackends();
      if (generation === requestGeneration) backends = answer;
    } catch (error) {
      if (generation === requestGeneration) backendsError = error;
    } finally {
      if (generation === requestGeneration) backendsLoading = false;
    }
  }

  async function persist(configuration: EmployeeConfigurationSnapshot): Promise<void> {
    if (saving) return;
    const previous = {
      employee_backend: selectedBackend,
      employee_launch_model: selectedModel,
      employee_launch_reasoning_effort: selectedReasoning
    };
    selectedBackend = configuration.employee_backend as ConversationBackendKey;
    selectedModel = configuration.employee_launch_model;
    selectedReasoning = configuration.employee_launch_reasoning_effort;
    saving = true;
    saveError = null;
    try {
      const saved = await onSave(configuration);
      selectedBackend = saved.employee_backend as ConversationBackendKey;
      selectedModel = saved.employee_launch_model;
      selectedReasoning = saved.employee_launch_reasoning_effort;
    } catch (error) {
      selectedBackend = previous.employee_backend;
      selectedModel = previous.employee_launch_model;
      selectedReasoning = previous.employee_launch_reasoning_effort;
      saveError = error;
    } finally {
      saving = false;
    }
  }

  function incomingSignature(): string {
    return JSON.stringify([
      employeeBackend,
      employeeLaunchModel,
      employeeLaunchReasoningEffort
    ]);
  }

  $effect(() => {
    const signature = incomingSignature();
    if (signature === lastIncomingSignature || saving) return;
    lastIncomingSignature = signature;
    selectedBackend = employeeBackend as ConversationBackendKey;
    selectedModel = employeeLaunchModel;
    selectedReasoning = employeeLaunchReasoningEffort;
  });

  onMount(() => void loadBackends());
  onDestroy(() => { requestGeneration += 1; });
</script>

<div
  class="employee-configuration-setup"
  data-employee-configuration-setup
  data-ticket-id={ticketId}
  data-employee-configuration-backend={selectedBackend}
  data-employee-configuration-model={selectedModel ?? ""}
  data-employee-configuration-reasoning={selectedReasoning ?? ""}
>
  <span class="employee-configuration-key">runs on</span>
  <UnifiedModelPicker
    view={picker}
    bind:snapshots={backends}
    models={snapshot?.available_models ?? []}
    backendEffortOptions={snapshot?.reasoning_effort_options ?? []}
    below
    disabled={saving}
    keepOpenWhenDisabled
    attributes={{ "data-employee-configuration-picker": "" }}
    onChooseBackend={(backend, defaults) => void persist({
      employee_backend: backend,
      employee_launch_model: defaults.model,
      employee_launch_reasoning_effort: defaults.reasoningEffort
    })}
    onChooseModel={(model, reasoningEffort) => void persist({
      employee_backend: selectedBackend,
      employee_launch_model: model,
      employee_launch_reasoning_effort: reasoningEffort
    })}
    onChooseReasoningEffort={(reasoningEffort) => void persist({
      employee_backend: selectedBackend,
      employee_launch_model: selectedModel ?? picker.defaultModel,
      employee_launch_reasoning_effort: reasoningEffort
    })}
  />

  {#if backendsLoading}
    <span class="employee-configuration-state" data-employee-configuration-loading>loading models…</span>
  {/if}

  {#if backendsError}
    <div class="employee-configuration-problem" data-employee-configuration-error>
      <ErrorLine error={backendsError} />
      <Button
        variant="quiet"
        data-employee-configuration-retry
        disabled={backendsLoading}
        onclick={() => void loadBackends()}
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
  .employee-configuration-key, .employee-configuration-state {
    color: var(--text-faintest); font-family: var(--font-mono); font-size: var(--type-xs);
  }
  .employee-configuration-problem { flex-basis: 100%; display: flex; align-items: center; gap: var(--space-2); }
</style>
