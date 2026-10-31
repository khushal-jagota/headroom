import { onDestroy } from "svelte";
import { createQuery } from "@tanstack/svelte-query";
import { queries } from "../queryCatalogue";
import { queryClient } from "../queryClient";
import type { EmployeeConfigurationSnapshot } from "../types";
import { resolveModelPicker } from "./modelPicker";
import { type BackendSnapshot, type ConversationBackendKey } from "./wire";

/** Instance-local selection and persistence shared by Ticket setup and launch defaults. */
export function createEmployeeConfigurationEditor(
  incoming: () => EmployeeConfigurationSnapshot,
  save: (next: EmployeeConfigurationSnapshot) => Promise<EmployeeConfigurationSnapshot>,
  read: (refresh?: boolean) => ReturnType<typeof queries.backends> = queries.backends
) {
  let selected = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "claude",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null
  });
  let lastIncomingSignature = "";
  const providerChoices = new Map<string, EmployeeConfigurationSnapshot>();
  const catalogue = createQuery(() => read(), () => queryClient);
  let backends = $derived(catalogue.data ?? []);
  let saving = $state(false);
  let saveError = $state<unknown>(null);
  let disposed = false;
  let backendKey = $derived(selected.employee_backend as ConversationBackendKey);
  let snapshot = $derived(backends.find((candidate) => candidate.backend_key === backendKey) ?? null);
  let picker = $derived(resolveModelPicker({
    backendKey,
    model: selected.employee_launch_model,
    reasoningEffort: selected.employee_launch_reasoning_effort,
    backends
  }));

  $effect(() => {
    const value = incoming();
    const signature = JSON.stringify([
      value.employee_backend, value.employee_launch_model, value.employee_launch_reasoning_effort
    ]);
    if (disposed || saving || signature === lastIncomingSignature) return;
    lastIncomingSignature = signature;
    selected = { ...value };
  });

  async function loadBackends(): Promise<void> {
    await queryClient.fetchQuery({...read(true), staleTime: 0});
  }

  async function persist(next: EmployeeConfigurationSnapshot): Promise<void> {
    if (disposed || saving) return;
    const previous = selected;
    providerChoices.set(previous.employee_backend, {...previous});
    selected = { ...next };
    saving = true;
    saveError = null;
    try {
      const saved = await save(next);
      if (!disposed) { selected = {
        employee_backend: saved.employee_backend,
        employee_launch_model: saved.employee_launch_model,
        employee_launch_reasoning_effort: saved.employee_launch_reasoning_effort
      }; providerChoices.set(selected.employee_backend, {...selected}); }
    } catch (error) {
      if (!disposed) {
        selected = previous;
        saveError = error;
      }
    } finally {
      if (!disposed) saving = false;
    }
  }

  onDestroy(() => { disposed = true; });

  return {
    get selected() { return selected; },
    get backends() { return backends; },
    set backends(value: readonly BackendSnapshot[]) { if (!disposed) queryClient.setQueryData(read().queryKey, [...value]); },
    get snapshot() { return snapshot; },
    get picker() { return picker; },
    get loading() { return catalogue.isFetching; },
    get backendsError() { return catalogue.error; },
    get saving() { return saving; },
    get saveError() { return saveError; },
    loadBackends,
    chooseBackend(backend: ConversationBackendKey, defaults: { model: string | null; reasoningEffort: string | null }) {
      providerChoices.set(selected.employee_backend, {...selected});
      const remembered = providerChoices.get(backend);
      if (remembered) void persist(remembered);
      else selected = {
        employee_backend: backend, employee_launch_model: null,
        employee_launch_reasoning_effort: null
      };
    },
    chooseModel(model: string, reasoningEffort: string | null) {
      void persist({
        employee_backend: selected.employee_backend,
        employee_launch_model: model,
        employee_launch_reasoning_effort: reasoningEffort
      });
    }
  };
}
