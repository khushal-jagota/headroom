<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { resolveModelPicker } from "../lib/conversation/modelPicker";
  import {
    readBackends,
    type BackendSnapshot,
    type ConversationBackendKey
  } from "../lib/conversation/wire";
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

  let selected = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "claude",
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

  let backendKey = $derived(selected.employee_backend as ConversationBackendKey);
  let snapshot = $derived(
    backends.find((candidate) => candidate.backend_key === backendKey) ?? null
  );
  let picker = $derived(resolveModelPicker({
    backendKey,
    model: selected.employee_launch_model,
    reasoningEffort: selected.employee_launch_reasoning_effort,
    backends
  }));

  async function loadBackends(): Promise<void> {
    const generation = ++requestGeneration;
    backendsError = null;
    loading = true;
    try {
      const answer = await readBackends();
      if (generation === requestGeneration) backends = answer;
    } catch (error) {
      if (generation === requestGeneration) backendsError = error;
    } finally {
      if (generation === requestGeneration) loading = false;
    }
  }

  async function persist(next: EmployeeConfigurationSnapshot): Promise<void> {
    if (saving) return;
    const previous = selected;
    selected = { ...next };
    saving = true;
    saveError = null;
    try {
      selected = await onSave(next);
    } catch (error) {
      selected = previous;
      saveError = error;
    } finally {
      saving = false;
    }
  }

  $effect(() => {
    const signature = JSON.stringify([
      value.employee_backend,
      value.employee_launch_model,
      value.employee_launch_reasoning_effort
    ]);
    if (signature === incomingSignature || saving) return;
    incomingSignature = signature;
    selected = { ...value };
  });

  onMount(() => void loadBackends());
  onDestroy(() => { requestGeneration += 1; });
</script>

<section class="worker-launch-defaults" data-launch-defaults data-launch-label={label}>
  <header class="worker-launch-defaults-head">
    <h2>{label} launch defaults</h2>
    <span class="worker-launch-defaults-note">applies to future launches</span>
  </header>
  <div class="worker-launch-defaults-controls">
    <span class="worker-launch-defaults-key">launches on</span>
    <UnifiedModelPicker
      view={picker}
      bind:snapshots={backends}
      models={snapshot?.available_models ?? []}
      backendEffortOptions={snapshot?.reasoning_effort_options ?? []}
      below
      disabled={saving}
      keepOpenWhenDisabled
      attributes={{ "data-launch-defaults-picker": "" }}
      onChooseBackend={(backend, defaults) => void persist({
        employee_backend: backend,
        employee_launch_model: defaults.model,
        employee_launch_reasoning_effort: defaults.reasoningEffort
      })}
      onChooseModel={(model, reasoningEffort) => void persist({
        ...selected,
        employee_launch_model: model,
        employee_launch_reasoning_effort: reasoningEffort
      })}
      onChooseReasoningEffort={(reasoningEffort) => void persist({
        ...selected,
        employee_launch_model: selected.employee_launch_model ?? picker.defaultModel,
        employee_launch_reasoning_effort: reasoningEffort
      })}
    />
    {#if loading}<span class="worker-launch-defaults-state">loading models…</span>{/if}
    {#if backendsError}<ErrorLine error={backendsError} />{/if}
  </div>
  {#if saveError}<div class="worker-row-error" data-launch-defaults-error><ErrorLine error={saveError} /></div>{/if}
</section>

<style>
  .worker-launch-defaults-controls { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
  .worker-launch-defaults-key, .worker-launch-defaults-state {
    color: var(--text-faintest); font-family: var(--font-mono); font-size: var(--type-xs);
  }
</style>
