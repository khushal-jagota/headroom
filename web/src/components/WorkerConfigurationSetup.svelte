<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { labelize } from "../lib/ui";
  import { effortOptionsFor } from "../lib/conversation/composer";
  import { readBackends, type BackendSnapshot } from "../lib/conversation/wire";
  import type { EmployeeConfigurationSnapshot, TicketDetail } from "../lib/types";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";

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

  let selectedBackend = $state("");
  let selectedModel = $state<string | null>(null);
  let selectedReasoning = $state<string | null>(null);
  let lastIncomingSignature = $state("");
  let backends = $state<readonly BackendSnapshot[]>([]);
  let backendsLoading = $state(false);
  let backendsError = $state<unknown>(null);
  let saveError = $state<unknown>(null);
  let saving = $state(false);
  let requestGeneration = 0;

  // The backend the ticket will launch on, as the machine reports it: which models it
  // offers, which efforts each of those takes, and what it runs when nobody names one.
  let snapshot = $derived(
    backends.find((candidate) => candidate.backend_key === selectedBackend) ?? null
  );
  // Before the machine has answered, the only worker this pill knows of is the one the
  // Ticket already names — so that is what it shows, and there is nothing else to pick.
  let workerOptions = $derived(
    backends.length > 0
      ? backends.map((candidate) => ({
          value: candidate.backend_key as string,
          label: labelize(candidate.backend_key)
        }))
      : selectedBackend === ""
        ? []
        : [{ value: selectedBackend, label: labelize(selectedBackend) }]
  );
  let modelOptions = $derived(
    (snapshot?.available_models ?? []).map((model) => ({
      value: model.model_id,
      label: model.display_name ?? model.model_id
    }))
  );
  // Effort belongs to the model that will actually run. Nothing pinned means the backend's
  // own model runs, so the backend's own list is the one on offer — the same rule the
  // server applies when it saves the choice.
  let reasoningOptions = $derived(
    effortOptionsFor(
      snapshot?.available_models ?? [],
      selectedModel,
      snapshot?.reasoning_effort_options ?? []
    ).map((effort) => ({ value: effort, label: effort }))
  );

  function incomingSignature(): string {
    return JSON.stringify([
      employeeBackend,
      employeeLaunchModel,
      employeeLaunchReasoningEffort
    ]);
  }

  function displayLabel(
    options: Array<{ value: string; label: string }>,
    value: string | null,
    fallback: string
  ): string {
    const encoded = value ?? "";
    return options.find((option) => option.value === encoded)?.label ?? value ?? fallback;
  }

  // When nothing is pinned (value null) the control shows the backend's concrete
  // native value — the real name, never the word "default". No native value → empty.
  function displayValue(
    options: Array<{ value: string; label: string }>,
    value: string | null,
    nativeValue: string | null
  ): string {
    const effective = value ?? nativeValue;
    if (effective === null) return "";
    return options.find((option) => option.value === effective)?.label ?? effective;
  }

  function withSavedUnavailable(
    options: Array<{ value: string; label: string; unavailable?: boolean }>,
    savedValue: string | null
  ): Array<{ value: string; label: string; unavailable?: boolean }> {
    if (savedValue === null || options.some((option) => option.value === savedValue)) return options;
    return [
      ...options,
      { value: savedValue, label: `${savedValue} · unavailable`, unavailable: true }
    ];
  }

  async function loadBackends(refresh = false): Promise<void> {
    const generation = ++requestGeneration;
    backends = [];
    backendsError = null;
    backendsLoading = true;
    try {
      const answer = await readBackends(refresh);
      if (generation !== requestGeneration) return;
      backends = answer;
    } catch (error) {
      if (generation !== requestGeneration) return;
      backendsError = error;
    } finally {
      if (generation === requestGeneration) backendsLoading = false;
    }
  }

  async function persist(configuration: EmployeeConfigurationSnapshot): Promise<void> {
    if (saving) return;
    saving = true;
    saveError = null;
    try {
      const saved = await onSave(configuration);
      selectedBackend = saved.employee_backend;
      selectedModel = saved.employee_launch_model;
      selectedReasoning = saved.employee_launch_reasoning_effort;
    } catch (error) {
      saveError = error;
    } finally {
      saving = false;
    }
  }

  function selectWorker(event: Event): void {
    const target = event.currentTarget as HTMLSelectElement;
    const nextBackend = target.value;
    target.value = selectedBackend;
    if (nextBackend === selectedBackend || saving) return;
    void persist({
      employee_backend: nextBackend,
      employee_launch_model: null,
      employee_launch_reasoning_effort: null
    });
  }

  function selectModel(event: Event): void {
    const target = event.currentTarget as HTMLSelectElement;
    // Choosing the native value stores null ("not pinned"); anything else pins.
    const nextModel = target.value === (snapshot?.default_model_id ?? "") ? null : target.value || null;
    target.value = selectedModel ?? snapshot?.default_model_id ?? "";
    if (nextModel === selectedModel || saving) return;
    void persist({
      employee_backend: selectedBackend,
      employee_launch_model: nextModel,
      employee_launch_reasoning_effort: selectedReasoning
    });
  }

  function selectReasoning(event: Event): void {
    const target = event.currentTarget as HTMLSelectElement;
    const nextReasoning =
      target.value === (snapshot?.default_reasoning_effort ?? "") ? null : target.value || null;
    target.value = selectedReasoning ?? snapshot?.default_reasoning_effort ?? "";
    if (nextReasoning === selectedReasoning || saving) return;
    void persist({
      employee_backend: selectedBackend,
      employee_launch_model: selectedModel,
      employee_launch_reasoning_effort: nextReasoning
    });
  }

  $effect(() => {
    const signature = incomingSignature();
    if (signature === lastIncomingSignature) return;
    lastIncomingSignature = signature;
    selectedBackend = employeeBackend;
    selectedModel = employeeLaunchModel;
    selectedReasoning = employeeLaunchReasoningEffort;
  });

  onMount(() => {
    void loadBackends();
  });

  onDestroy(() => {
    requestGeneration += 1;
  });
</script>

<div
  class="employee-configuration-setup"
  data-employee-configuration-setup
  data-ticket-id={ticketId}
  data-employee-configuration-backend={selectedBackend}
  data-employee-configuration-model={selectedModel ?? ""}
  data-employee-configuration-reasoning={selectedReasoning ?? ""}
>
  <span class="pill" data-employee-configuration-worker>
    <span class="pill-key">worker</span>
    {displayLabel(workerOptions, selectedBackend, selectedBackend)}
    <select value={selectedBackend} disabled={saving} onchange={selectWorker} aria-label="Worker">
      {#each workerOptions as option}
        <option value={option.value}>{option.label}</option>
      {/each}
    </select>
  </span>

  {#if backendsLoading}
    <span class="employee-configuration-state" data-employee-configuration-loading>
      loading models…
    </span>
  {:else if snapshot}
    <span class="pill" data-employee-configuration-model-control>
      <span class="pill-key">model</span>
      {displayValue(modelOptions, selectedModel, snapshot.default_model_id ?? null)}
      <select
        value={selectedModel ?? snapshot.default_model_id ?? ""}
        disabled={saving}
        onchange={selectModel}
        aria-label="Model"
      >
        {#each withSavedUnavailable(modelOptions, selectedModel) as option}
          <option value={option.value} disabled={option.unavailable}>{option.label}</option>
        {/each}
      </select>
    </span>

    {#if reasoningOptions.length > 0}
      <span class="pill" data-employee-configuration-reasoning-control>
        <span class="pill-key">reasoning</span>
        {displayValue(reasoningOptions, selectedReasoning, snapshot.default_reasoning_effort ?? null)}
        <select
          value={selectedReasoning ?? snapshot.default_reasoning_effort ?? ""}
          disabled={saving}
          onchange={selectReasoning}
          aria-label="Reasoning"
        >
          {#each withSavedUnavailable(reasoningOptions, selectedReasoning) as option}
            <option value={option.value} disabled={option.unavailable}>{option.label}</option>
          {/each}
        </select>
      </span>
    {/if}
  {:else if backendsError}
    <span class="pill" data-employee-configuration-saved-model>
      <span class="pill-key">model</span>{selectedModel ?? ""}
    </span>
    {#if selectedReasoning !== null}
      <span class="pill" data-employee-configuration-saved-reasoning>
        <span class="pill-key">reasoning</span>{selectedReasoning}
      </span>
    {/if}
  {/if}

  {#if backendsError}
    <div class="employee-configuration-problem" data-employee-configuration-error>
      <ErrorLine error={backendsError} />
      <Button
        variant="quiet"
        data-employee-configuration-retry
        disabled={backendsLoading}
        onclick={() => void loadBackends(true)}
      >Retry</Button>
    </div>
  {/if}

  {#if snapshot}
    <Button
      variant="quiet"
      data-employee-configuration-refresh
      disabled={backendsLoading}
      onclick={() => void loadBackends(true)}
    >Refresh</Button>
  {/if}

  {#if saveError}
    <div class="employee-configuration-problem" data-employee-configuration-save-error>
      <ErrorLine error={saveError} />
    </div>
  {/if}
</div>
