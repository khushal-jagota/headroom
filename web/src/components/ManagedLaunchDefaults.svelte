<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { labelize } from "../lib/ui";
  import { effortOptionsFor } from "../lib/conversation/composer";
  import { readBackends, type BackendSnapshot } from "../lib/conversation/wire";
  import type { EmployeeConfigurationSnapshot } from "../lib/types";
  import ErrorLine from "./ErrorLine.svelte";
  import Button from "./Button.svelte";

  let {
    label,
    value,
    onSave
  }: {
    label: string;
    value: EmployeeConfigurationSnapshot;
    onSave: (next: EmployeeConfigurationSnapshot) => Promise<EmployeeConfigurationSnapshot>;
  } = $props();

  let selected = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null
  });
  let incomingSignature = $state("");
  let backends = $state<readonly BackendSnapshot[]>([]);
  let backendsError = $state<unknown>(null);
  let saveError = $state<unknown>(null);
  let loading = $state(false);
  let saving = $state(false);
  let requestGeneration = 0;

  let snapshot = $derived(
    backends.find((candidate) => candidate.backend_key === selected.employee_backend) ?? null
  );
  // Before the machine has answered, the only backend this control knows of is the one
  // already saved as the default — so that is what it shows, and nothing else is pickable.
  let backendOptions = $derived(
    backends.length > 0
      ? backends.map((candidate) => candidate.backend_key as string)
      : selected.employee_backend === ""
        ? []
        : [selected.employee_backend]
  );
  let modelOptions = $derived(
    (snapshot?.available_models ?? []).map((model) => ({
      value: model.model_id,
      label: model.display_name ?? model.model_id
    }))
  );
  // Effort belongs to the model that will actually run: a pinned model's own list when it
  // names one, the backend's own list when nothing is pinned.
  let reasoningOptions = $derived(
    effortOptionsFor(
      snapshot?.available_models ?? [],
      selected.employee_launch_model,
      snapshot?.reasoning_effort_options ?? []
    ).map((effort) => ({ value: effort, label: effort }))
  );

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
    loading = true;
    try {
      const answer = await readBackends(refresh);
      if (generation === requestGeneration) backends = answer;
    } catch (error) {
      if (generation === requestGeneration) backendsError = error;
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

  // A backend change brings the new backend's own model with it. The saved model belonged
  // to the old one and means nothing here, and saving no model at all would launch these
  // workers on whatever the backend picked for itself. A backend this machine reported no
  // model for has none to bring: the save goes out naming none and the server says so,
  // rather than this control quietly choosing something nobody can see.
  function selectBackend(event: Event): void {
    const backend = (event.currentTarget as HTMLSelectElement).value;
    if (backend === selected.employee_backend || saving) return;
    const itsOwn = backends.find((candidate) => candidate.backend_key === backend);
    void persist({
      employee_backend: backend,
      employee_launch_model: itsOwn?.default_model_id ?? null,
      employee_launch_reasoning_effort: null
    });
  }

  function selectModel(event: Event): void {
    const model = (event.currentTarget as HTMLSelectElement).value;
    if (model === selected.employee_launch_model || saving) return;
    void persist({ ...selected, employee_launch_model: model });
  }

  function selectReasoning(event: Event): void {
    const raw = (event.currentTarget as HTMLSelectElement).value;
    const reasoning = raw === (snapshot?.default_reasoning_effort ?? "") ? null : raw || null;
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

  onMount(() => {
    void loadBackends();
  });

  onDestroy(() => {
    requestGeneration += 1;
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
        {#each backendOptions as backend}<option value={backend}>{labelize(backend)}</option>{/each}
      </select>
    </label>
    {#if loading}<span class="worker-launch-defaults-state">loading models…</span>{:else if snapshot}
      <label>
        <span>Model</span>
        <select aria-label={`${label} model`} value={selected.employee_launch_model ?? snapshot.default_model_id ?? ""} disabled={saving} onchange={selectModel}>
          {#each withSavedUnavailable(modelOptions, selected.employee_launch_model) as option}<option value={option.value} disabled={option.unavailable}>{option.label}</option>{/each}
        </select>
      </label>
      {#if reasoningOptions.length > 0}
        <label>
          <span>Reasoning</span>
          <select aria-label={`${label} reasoning`} value={selected.employee_launch_reasoning_effort ?? snapshot.default_reasoning_effort ?? ""} disabled={saving} onchange={selectReasoning}>
          {#each withSavedUnavailable(reasoningOptions, selected.employee_launch_reasoning_effort) as option}<option value={option.value} disabled={option.unavailable}>{option.label}</option>{/each}
          </select>
        </label>
      {/if}
    {:else if backendsError}<ErrorLine error={backendsError} />{/if}
    {#if snapshot}
      <Button
        variant="quiet"
        data-launch-defaults-refresh
        disabled={loading || saving}
        onclick={() => void loadBackends(true)}
      >Refresh</Button>
    {/if}
  </div>
  {#if saveError}<div class="worker-row-error" data-launch-defaults-error><ErrorLine error={saveError} /></div>{/if}
</section>
