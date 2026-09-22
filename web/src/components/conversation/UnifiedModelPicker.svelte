<script lang="ts">
  import { tick } from "svelte";
  import {
    backendSelectionDefaults,
    modelSelectionEffort,
    type ModelPickerChoice,
    type ModelPickerView
  } from "../../lib/conversation/modelPicker";
  import { effortOptionsFor } from "../../lib/conversation/composer";
  import {
    backendRefreshControl,
    refreshBackendSnapshots
  } from "../../lib/conversation/backendRefresh";
  import type { BackendModel, BackendSnapshot, ConversationBackendKey } from "../../lib/conversation/wire";
  import BackendMark from "./BackendMark.svelte";
  import ListboxPicker, {
    type ListboxPickerController,
    type ListboxPickerItem
  } from "./ListboxPicker.svelte";
  import UsageRings from "./UsageRings.svelte";

  let {
    view,
    snapshots = $bindable(),
    models,
    backendEffortOptions,
    disabled = false,
    keepOpenWhenDisabled = false,
    showUsage = false,
    below = false,
    label = "Model",
    attributes = {},
    afterChoose,
    onChooseBackend,
    onChooseModel
  }: {
    view: ModelPickerView;
    snapshots: readonly BackendSnapshot[];
    models: readonly BackendModel[];
    backendEffortOptions: readonly string[];
    disabled?: boolean;
    /** Keep an open backend catalogue visible while a persisted selection saves. */
    keepOpenWhenDisabled?: boolean;
    /** Usage belongs only beside the run choice in the conversation composer. */
    showUsage?: boolean;
    below?: boolean;
    label?: string;
    attributes?: Record<string, string | undefined>;
    afterChoose?: () => void;
    onChooseBackend: (
      backend: ConversationBackendKey,
      defaults: { model: string | null; reasoningEffort: string | null }
    ) => void;
    onChooseModel: (model: string, reasoningEffort: string | null) => void;
  } = $props();

  let showing = $state<"models" | "efforts">("models");
  let pendingModel = $state<string | null>(null);
  let feedback = $state<string | null>(null);
  let refreshing = $state(false);
  let picker = $state<ListboxPickerController>(null!);

  let effortRows = $derived(
    effortOptionsFor(models, pendingModel ?? view.modelValue, backendEffortOptions)
      .map((effort) => ({ value: effort, name: effort }))
  );
  let rows = $derived((showing === "models" ? view.models : effortRows) as readonly ListboxPickerItem[]);
  let chosenValue = $derived(showing === "models" ? view.modelValue : view.reasoningEffort);
  let foot = $derived(feedback ?? view.staleModelReason);
  let pickerBusy = $derived(disabled || refreshing);
  let refreshControl = $derived(backendRefreshControl(refreshing));

  function snapshotFor(key: ConversationBackendKey): BackendSnapshot | null {
    return snapshots.find((snapshot) => snapshot.backend_key === key) ?? null;
  }

  function showsBackendUsage(key: ConversationBackendKey): boolean {
    return snapshotFor(key)?.identity?.status !== "unauthenticated";
  }

  function hasModelUsage(modelId: string): boolean {
    const snapshot = snapshotFor(view.backendKey ?? "claude");
    if (snapshot?.identity?.status === "unauthenticated") return false;
    return snapshot?.cached_usage?.windows.some(
      (window) => window.model_id === modelId
    ) ?? false;
  }

  function preparePanel(): void {
    showing = "models";
    pendingModel = null;
    feedback = null;
  }

  function take(choice: ModelPickerChoice): void {
    if (disabled) return;
    if (showing === "models") {
      const effort = modelSelectionEffort(
        models,
        backendEffortOptions,
        choice.value,
        view.reasoningEffort || null,
        view.defaultReasoningEffort
      );
      if (effort === null) {
        onChooseModel(choice.value, null);
        picker.close(!afterChoose);
        if (afterChoose) void tick().then(afterChoose);
        return;
      }
      pendingModel = choice.value;
      showing = "efforts";
      void tick().then(() => picker.setActiveValue(effort));
      return;
    } else {
      onChooseModel(pendingModel ?? view.modelValue, choice.value);
      pendingModel = null;
    }
    picker.close(!afterChoose);
    if (afterChoose) void tick().then(afterChoose);
  }

  function chooseBackend(key: ConversationBackendKey, unavailableReason: string | null): void {
    if (disabled) return;
    if (unavailableReason !== null) {
      feedback = unavailableReason;
      return;
    }
    feedback = null;
    showing = "models";
    pendingModel = null;
    if (key === view.backendKey) {
      picker.setActiveValue(view.modelValue);
      return;
    }
    onChooseBackend(key, backendSelectionDefaults(snapshots, key));
    void tick().then(() => picker.setActiveValue(view.modelValue));
  }

  function showReasoning(): void {
    if (disabled) return;
    if (view.reasoningUnavailableReason !== null) {
      feedback = view.reasoningUnavailableReason;
      return;
    }
    feedback = null;
    pendingModel = null;
    showing = "efforts";
    void tick().then(() => picker.setActiveValue(view.reasoningEffort));
  }

  function showModels(): void {
    if (disabled) return;
    showing = "models";
    pendingModel = null;
    void tick().then(() => picker.setActiveValue(view.modelValue));
  }

  async function runRefresh(): Promise<void> {
    if (refreshing) return;
    refreshing = true;
    feedback = null;
    const result = await refreshBackendSnapshots();
    if (result.snapshots !== null) snapshots = result.snapshots;
    feedback = result.error;
    refreshing = false;
    await tick();
    picker.focusList();
  }
</script>

<ListboxPicker
  items={rows}
  selectedValue={chosenValue}
  {disabled}
  selectionDisabled={pickerBusy}
  {keepOpenWhenDisabled}
  label={`${label}: ${view.backendName} ${view.face}`.trim()}
  listLabel={showing === "models" ? "Models" : "Reasoning efforts"}
  kind="model"
  {below}
  attributes={{ "data-conversation-model-picker": "", ...attributes }}
  panelBusy={pickerBusy}
  bind:controller={picker}
  triggerAttributes={{ "data-conversation-picker-trigger": "" }}
  panelAttributes={{ "data-conversation-picker-panel": "" }}
  optionAttributes={(choice, active, selected) => ({
    "data-conversation-picker-choice": choice.value,
    "data-conversation-picker-active": active ? "true" : undefined,
    "data-conversation-picker-chosen": selected ? "true" : undefined
  })}
  onOpen={preparePanel}
  onChoose={(value) => {
    const choice = rows.find((row) => row.value === value);
    if (choice) take(choice as ModelPickerChoice);
  }}
>
  {#snippet triggerContent(open)}
    {#if view.backendKey}<BackendMark backend={view.backendKey} />{/if}
    {#if view.face}<span class="model-picker-face">{view.face}</span>{/if}
  {/snippet}
  {#snippet beforeList()}
    <div class="model-picker-rail" data-conversation-backend-rail role="group" aria-label="Backend and reasoning">
        {#each view.backends as backend (backend.key)}
          <button
            type="button"
            class="model-picker-rail-row"
            class:held={backend.selected}
            class:on={backend.selected && showing === "models"}
            class:dim={backend.unavailableReason !== null}
            data-conversation-backend={backend.key}
            data-conversation-backend-showing={backend.selected ? "true" : undefined}
            aria-pressed={backend.selected && showing === "models"}
            aria-disabled={backend.unavailableReason !== null}
            disabled={pickerBusy}
            tabindex="-1"
            data-listbox-picker-action
            onmousedown={(event) => event.preventDefault()}
            onclick={() => chooseBackend(backend.key, backend.unavailableReason)}
          >
            <BackendMark backend={backend.key} />
            <span>{backend.name}</span>
            {#if showUsage && showsBackendUsage(backend.key)}
              <UsageRings windows={snapshotFor(backend.key)?.cached_usage?.windows ?? []} compact />
            {/if}
          </button>
        {/each}
        <div class="model-picker-tools">
          {#if showing === "efforts"}
            <button
              type="button"
              class="model-picker-rail-row"
              data-conversation-picker-models
              aria-pressed={false}
              disabled={pickerBusy}
              tabindex="-1"
              data-listbox-picker-action
              onmousedown={(event) => event.preventDefault()}
              onclick={showModels}
            >
              <span aria-hidden="true">←</span>
              <span>Models</span>
            </button>
          {/if}
          <button
            type="button"
            class="model-picker-rail-row"
            data-conversation-picker-refresh
            aria-busy={refreshControl.busy}
            disabled={pickerBusy}
            tabindex="-1"
            data-listbox-picker-action
            onmousedown={(event) => event.preventDefault()}
            onclick={() => void runRefresh()}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" d="M19 8a8 8 0 1 0 1 7M19 3v5h-5" /></svg>
            <span>{refreshControl.label}</span>
          </button>
          <button
            type="button"
            class="model-picker-rail-row"
            class:on={showing === "efforts"}
            class:dim={view.reasoningUnavailableReason !== null}
            data-conversation-picker-reasoning
            aria-pressed={showing === "efforts"}
            aria-disabled={view.reasoningUnavailableReason !== null}
            disabled={pickerBusy}
            tabindex="-1"
            data-listbox-picker-action
            onmousedown={(event) => event.preventDefault()}
            onclick={showReasoning}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3.5 14h3.4v6.5H3.5Zm6.8-4h3.4v10.5h-3.4Zm6.8-6.5h3.4v17h-3.4Z" /></svg>
            <span>Reasoning</span>
          </button>
        </div>
    </div>
  {/snippet}
  {#snippet optionContent(choice, selected)}
    <span class="model-picker-choice-name">{choice.name}</span>
    {#if showUsage && showing === "models" && hasModelUsage(choice.value)}
      <UsageRings
        windows={snapshotFor(view.backendKey ?? "claude")?.cached_usage?.windows ?? []}
        modelId={choice.value}
        label={`Usage allowances for ${choice.name}`}
        compact
      />
    {/if}
  {/snippet}
  {#snippet afterList()}
    {#if foot}
      <div class="model-picker-foot" data-conversation-picker-feedback aria-live="polite">{foot}</div>
      {/if}
  {/snippet}
</ListboxPicker>

<style>
  .model-picker-face { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-rail { display: flex; flex-direction: column; border-inline-end: var(--border-hairline) solid var(--border-color); padding-block: var(--space-1); }
  .model-picker-rail-row {
    display: flex; align-items: center; gap: var(--space-2); width: 100%; background: transparent;
    border: 0; color: var(--text-faint); cursor: pointer; font-family: var(--font-mono);
    font-size: var(--type-xs); padding: var(--space-2) var(--space-3); text-align: left; white-space: nowrap;
  }
  .model-picker-rail-row > span:nth-child(2) { flex: 1; }
  .model-picker-rail-row:hover { background-image: var(--interaction-hover); color: var(--text-muted); }
  .model-picker-rail-row.held { color: var(--text-strong); }
  .model-picker-rail-row.on { background: var(--surface-recessed); color: var(--text-strong); }
  .model-picker-rail-row.dim { color: var(--text-faintest); opacity: .45; }
  .model-picker-rail-row.dim:hover { background: transparent; color: var(--text-faintest); }
  .model-picker-rail-row.dim :global(.model-picker-mark:not(.hermes)) { filter: grayscale(1); }
  :global(.listbox-picker-panel[aria-busy="true"]) .model-picker-rail-row { cursor: progress; opacity: .55; }
  .model-picker-tools { margin-top: auto; padding-top: var(--space-1); border-top: var(--border-hairline) solid var(--border-color); }
  .model-picker-tools svg { width: 13px; height: 13px; }
  .model-picker-choice-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-foot {
    grid-column: 1 / -1; border-top: var(--border-hairline) solid var(--border-color);
    background: var(--surface-recessed); color: var(--text-faint); font-size: var(--type-xs);
    line-height: 1.45; padding: var(--space-2) var(--space-3);
  }
</style>
