<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { labelize } from "../lib/ui";
  import type {
    EmployeeConfigurationCatalog,
    EmployeeConfigurationSnapshot
  } from "../lib/types";
  import ErrorLine from "./ErrorLine.svelte";
  import Button from "./Button.svelte";

  let {
    label,
    employeeBackends,
    value,
    onSave
  }: {
    label: string;
    employeeBackends: string[];
    value: EmployeeConfigurationSnapshot;
    onSave: (next: EmployeeConfigurationSnapshot) => Promise<EmployeeConfigurationSnapshot>;
  } = $props();

  let selected = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null
  });
  let incomingSignature = $state("");
  let catalog = $state<EmployeeConfigurationCatalog | null>(null);
  let catalogError = $state<unknown>(null);
  let catalogKey = $state("");
  let saveError = $state<unknown>(null);
  let loading = $state(false);
  let saving = $state(false);
  let requestGeneration = 0;
  let requestController: AbortController | null = null;

  let modelOptions = $derived(catalog?.models ?? []);
  let reasoningOptions = $derived(catalog?.reasoning_efforts ?? []);

  function withSavedUnavailable(
    options: Array<{ value: string; label: string; description?: string | null; unavailable?: boolean }>,
    savedValue: string | null
  ): Array<{ value: string; label: string; description?: string | null; unavailable?: boolean }> {
    if (savedValue === null || options.some((option) => option.value === savedValue)) return options;
    return [
      ...options,
      { value: savedValue, label: `${savedValue} · unavailable`, unavailable: true }
    ];
  }

  function selectedSignature(): string {
    return JSON.stringify([selected.employee_backend, selected.employee_launch_model, selected.employee_launch_reasoning_effort]);
  }

  async function loadCatalog(forceRefresh = false): Promise<void> {
    requestController?.abort();
    const controller = new AbortController();
    requestController = controller;
    const generation = ++requestGeneration;
    catalog = null;
    catalogError = null;
    loading = true;
    const query = new URLSearchParams({ employee_backend: selected.employee_backend });
    if (selected.employee_launch_model !== null) query.set("candidate_model", selected.employee_launch_model);
    if (forceRefresh) query.set("force_refresh", "true");
    try {
      const response = await fetchJson<EmployeeConfigurationCatalog>(
        `/api/employee-configuration-catalog?${query.toString()}`,
        { signal: controller.signal }
      );
      if (generation === requestGeneration) catalog = response;
    } catch (error) {
      if (generation === requestGeneration) catalogError = error;
    } finally {
      if (generation === requestGeneration) loading = false;
    }
  }

  async function persist(next: EmployeeConfigurationSnapshot): Promise<void> {
    if (saving) return;
    saving = true;
    saveError = null;
    try {
      selected = await onSave(next);
    } catch (error) {
      saveError = error;
    } finally {
      saving = false;
    }
  }

  function selectBackend(event: Event): void {
    const backend = (event.currentTarget as HTMLSelectElement).value;
    if (backend === selected.employee_backend || saving) return;
    void persist({ employee_backend: backend, employee_launch_model: null, employee_launch_reasoning_effort: null });
  }

  function selectModel(event: Event): void {
    const raw = (event.currentTarget as HTMLSelectElement).value;
    // Choosing the native value stores null ("not pinned"); anything else pins.
    const model = raw === (catalog?.native_model ?? "") ? null : raw || null;
    if (model === selected.employee_launch_model || saving) return;
    void persist({ ...selected, employee_launch_model: model });
  }

  function selectReasoning(event: Event): void {
    const raw = (event.currentTarget as HTMLSelectElement).value;
    const reasoning = raw === (catalog?.native_reasoning_effort ?? "") ? null : raw || null;
    if (reasoning === selected.employee_launch_reasoning_effort || saving) return;
    void persist({ ...selected, employee_launch_reasoning_effort: reasoning });
  }

  $effect(() => {
    const nextSignature = JSON.stringify([value.employee_backend, value.employee_launch_model, value.employee_launch_reasoning_effort]);
    if (nextSignature !== incomingSignature && !saving) {
      incomingSignature = nextSignature;
      selected = { ...value };
    }
  });

  $effect(() => {
    const nextKey = selectedSignature();
    if (!selected.employee_backend || nextKey === catalogKey) return;
    catalogKey = nextKey;
    void loadCatalog();
  });

  onDestroy(() => {
    requestGeneration += 1;
    requestController?.abort();
  });
</script>

<section class="worker-launch-defaults" data-launch-defaults data-launch-label={label}>
  <header class="worker-launch-defaults-head">
    <h2>{label} launch defaults</h2>
    <span class="worker-launch-defaults-note">applies to future launches</span>
  </header>
  <div class="worker-launch-defaults-controls">
    <label>
      <span>Backend</span>
      <select aria-label={`${label} backend`} value={selected.employee_backend} disabled={saving} onchange={selectBackend}>
        {#each employeeBackends as backend}<option value={backend}>{labelize(backend)}</option>{/each}
      </select>
    </label>
    {#if loading}<span class="worker-launch-defaults-state">loading models…</span>{:else if catalog}
      <label>
        <span>Model</span>
        <select aria-label={`${label} model`} value={selected.employee_launch_model ?? catalog.native_model ?? ""} disabled={saving} onchange={selectModel}>
          {#each withSavedUnavailable(modelOptions, selected.employee_launch_model) as option}<option value={option.value} disabled={option.unavailable}>{option.label}</option>{/each}
        </select>
      </label>
      {#if catalog.reasoning_supported}
        <label>
          <span>Reasoning</span>
          <select aria-label={`${label} reasoning`} value={selected.employee_launch_reasoning_effort ?? catalog.native_reasoning_effort ?? ""} disabled={saving} onchange={selectReasoning}>
          {#each withSavedUnavailable(reasoningOptions, selected.employee_launch_reasoning_effort) as option}<option value={option.value} disabled={option.unavailable}>{option.label}</option>{/each}
          </select>
        </label>
      {/if}
    {:else if catalogError}<ErrorLine error={catalogError} />{/if}
    {#if catalog}
      <Button
        variant="quiet"
        data-launch-defaults-refresh
        disabled={loading || saving}
        onclick={() => void loadCatalog(true)}
      >Refresh</Button>
    {/if}
  </div>
  {#if saveError}<div class="worker-row-error" data-launch-defaults-error><ErrorLine error={saveError} /></div>{/if}
</section>
