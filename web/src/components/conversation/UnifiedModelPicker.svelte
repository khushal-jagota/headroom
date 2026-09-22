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
  import PickerRail from "./PickerRail.svelte";
  import PickerRailRow from "./PickerRailRow.svelte";
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
  kind="rail"
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
    <PickerRail label="Backend and reasoning" attributes={{ "data-conversation-backend-rail": "" }}>
      {#each view.backends as backend (backend.key)}
        <PickerRailRow
          held={backend.selected}
          on={backend.selected && showing === "models"}
          dim={backend.unavailableReason !== null}
          disabled={pickerBusy}
          label={backend.name}
          attributes={{
            "data-conversation-backend": backend.key,
            "data-conversation-backend-showing": backend.selected ? "true" : undefined,
            "aria-pressed": backend.selected && showing === "models" ? "true" : "false",
            "aria-disabled": backend.unavailableReason !== null ? "true" : undefined
          }}
          onclick={() => chooseBackend(backend.key, backend.unavailableReason)}
        >
          {#snippet icon()}<BackendMark backend={backend.key} />{/snippet}
          {#snippet trailing()}
            {#if showUsage && showsBackendUsage(backend.key)}
              <UsageRings windows={snapshotFor(backend.key)?.cached_usage?.windows ?? []} compact />
            {/if}
          {/snippet}
        </PickerRailRow>
      {/each}
      <div class="model-picker-tools">
        {#if showing === "efforts"}
          <PickerRailRow
            disabled={pickerBusy}
            label="Models"
            attributes={{ "data-conversation-picker-models": "", "aria-pressed": "false" }}
            onclick={showModels}
          >
            {#snippet icon()}<span aria-hidden="true">←</span>{/snippet}
          </PickerRailRow>
        {/if}
        <PickerRailRow
          disabled={pickerBusy}
          label={refreshControl.label}
          attributes={{
            "data-conversation-picker-refresh": "",
            "aria-busy": refreshControl.busy ? "true" : "false"
          }}
          onclick={() => void runRefresh()}
        >
          {#snippet icon()}
            <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" d="M19 8a8 8 0 1 0 1 7M19 3v5h-5" /></svg>
          {/snippet}
        </PickerRailRow>
        <PickerRailRow
          on={showing === "efforts"}
          dim={view.reasoningUnavailableReason !== null}
          disabled={pickerBusy}
          label="Reasoning"
          attributes={{
            "data-conversation-picker-reasoning": "",
            "aria-pressed": showing === "efforts" ? "true" : "false",
            "aria-disabled": view.reasoningUnavailableReason !== null ? "true" : undefined
          }}
          onclick={showReasoning}
        >
          {#snippet icon()}
            <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3.5 14h3.4v6.5H3.5Zm6.8-4h3.4v10.5h-3.4Zm6.8-6.5h3.4v17h-3.4Z" /></svg>
          {/snippet}
        </PickerRailRow>
      </div>
    </PickerRail>
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
  .model-picker-tools { margin-top: auto; padding-top: var(--space-1); border-top: var(--border-hairline) solid var(--border-color); }
  .model-picker-tools svg { width: 13px; height: 13px; }
  .model-picker-choice-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-foot {
    grid-column: 1 / -1; border-top: var(--border-hairline) solid var(--border-color);
    background: var(--surface-recessed); color: var(--text-faint); font-size: var(--type-xs);
    line-height: 1.45; padding: var(--space-2) var(--space-3);
  }
</style>
