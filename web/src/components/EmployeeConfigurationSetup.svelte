<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { labelize } from "../lib/ui";
  import type {
    EmployeeConfigurationCatalog,
    EmployeeConfigurationSnapshot,
    TicketDetail
  } from "../lib/types";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";

  let {
    ticketId,
    employeeBackends,
    employeeBackend,
    employeeLaunchModel,
    employeeLaunchReasoningEffort,
    onSave
  }: {
    ticketId: string;
    employeeBackends: string[];
    employeeBackend: string;
    employeeLaunchModel: string | null;
    employeeLaunchReasoningEffort: string | null;
    onSave: (configuration: EmployeeConfigurationSnapshot) => Promise<TicketDetail>;
  } = $props();

  let selectedBackend = $state("");
  let selectedModel = $state<string | null>(null);
  let selectedReasoning = $state<string | null>(null);
  let lastIncomingSignature = $state("");
  let catalogKey = $state("");
  let catalog = $state<EmployeeConfigurationCatalog | null>(null);
  let catalogLoading = $state(false);
  let catalogError = $state<unknown>(null);
  let saveError = $state<unknown>(null);
  let saving = $state(false);
  let requestGeneration = 0;
  let requestController: AbortController | null = null;

  let workerOptions = $derived(
    employeeBackends.map((backend) => ({ value: backend, label: labelize(backend) }))
  );
  let modelOptions = $derived(
    (catalog?.models ?? []).map((option) => ({ value: option.value, label: option.label }))
  );
  let reasoningOptions = $derived(
    (catalog?.reasoning_efforts ?? []).map((option) => ({
      value: option.value,
      label: option.label
    }))
  );

  function incomingSignature(): string {
    return JSON.stringify([
      employeeBackend,
      employeeLaunchModel,
      employeeLaunchReasoningEffort
    ]);
  }

  function requestKey(backend: string, model: string | null): string {
    return JSON.stringify([backend, model]);
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

  async function loadCatalog(backend: string, model: string | null, forceRefresh = false): Promise<void> {
    requestController?.abort();
    const controller = new AbortController();
    requestController = controller;
    const generation = ++requestGeneration;
    catalog = null;
    catalogError = null;
    catalogLoading = true;
    const query = new URLSearchParams({ employee_backend: backend });
    if (model !== null) query.set("candidate_model", model);
    if (forceRefresh) query.set("force_refresh", "true");
    try {
      const response = await fetchJson<EmployeeConfigurationCatalog>(
        `/api/employee-configuration-catalog?${query.toString()}`,
        { signal: controller.signal }
      );
      if (generation !== requestGeneration) return;
      catalog = response;
    } catch (error) {
      if (generation !== requestGeneration) return;
      catalogError = error;
    } finally {
      if (generation === requestGeneration) catalogLoading = false;
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
    const nextModel = target.value === (catalog?.native_model ?? "") ? null : target.value || null;
    target.value = selectedModel ?? catalog?.native_model ?? "";
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
      target.value === (catalog?.native_reasoning_effort ?? "") ? null : target.value || null;
    target.value = selectedReasoning ?? catalog?.native_reasoning_effort ?? "";
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

  $effect(() => {
    if (!selectedBackend) return;
    const nextKey = requestKey(selectedBackend, selectedModel);
    if (nextKey === catalogKey) return;
    catalogKey = nextKey;
    void loadCatalog(selectedBackend, selectedModel);
  });

  onDestroy(() => {
    requestGeneration += 1;
    requestController?.abort();
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

  {#if catalogLoading}
    <span class="employee-configuration-state" data-employee-configuration-loading>
      loading models…
    </span>
  {:else if catalog}
    <span class="pill" data-employee-configuration-model-control>
      <span class="pill-key">model</span>
      {displayValue(modelOptions, selectedModel, catalog.native_model)}
      <select
        value={selectedModel ?? catalog.native_model ?? ""}
        disabled={saving}
        onchange={selectModel}
        aria-label="Model"
      >
        {#each withSavedUnavailable(modelOptions, selectedModel) as option}
          <option value={option.value} disabled={option.unavailable}>{option.label}</option>
        {/each}
      </select>
    </span>

    {#if catalog.reasoning_supported}
      <span class="pill" data-employee-configuration-reasoning-control>
        <span class="pill-key">reasoning</span>
        {displayValue(reasoningOptions, selectedReasoning, catalog.native_reasoning_effort)}
        <select
          value={selectedReasoning ?? catalog.native_reasoning_effort ?? ""}
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
  {:else if catalogError}
    <span class="pill" data-employee-configuration-saved-model>
      <span class="pill-key">model</span>{selectedModel ?? ""}
    </span>
    {#if selectedReasoning !== null}
      <span class="pill" data-employee-configuration-saved-reasoning>
        <span class="pill-key">reasoning</span>{selectedReasoning}
      </span>
    {/if}
  {/if}

  {#if catalogError}
    <div class="employee-configuration-problem" data-employee-configuration-error>
      <ErrorLine error={catalogError} />
      <Button
        variant="quiet"
        data-employee-configuration-retry
        disabled={catalogLoading}
        onclick={() => void loadCatalog(selectedBackend, selectedModel, true)}
      >Retry</Button>
    </div>
  {/if}

  {#if catalog}
    <Button
      variant="quiet"
      data-employee-configuration-refresh
      disabled={catalogLoading}
      onclick={() => void loadCatalog(selectedBackend, selectedModel, true)}
    >Refresh</Button>
  {/if}

  {#if saveError}
    <div class="employee-configuration-problem" data-employee-configuration-save-error>
      <ErrorLine error={saveError} />
    </div>
  {/if}
</div>
