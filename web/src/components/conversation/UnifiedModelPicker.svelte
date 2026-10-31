<script lang="ts">
  import { tick } from "svelte";
  import {
    type ModelPickerChoice,
    type ModelPickerView
  } from "../../lib/conversation/modelPicker";
  import { effortOptionsFor } from "../../lib/conversation/composer";
  import {
    backendRefreshControl,
    backendUsageOutcomeLabel,
    refreshBackendSnapshots
  } from "../../lib/conversation/backendRefresh";
  import { type BackendModel, type BackendSnapshot, type ConversationBackendKey } from "../../lib/conversation/wire";
  import BackendMark from "./BackendMark.svelte";
  import ListboxPicker, {
    type ListboxPickerController,
    type ListboxPickerItem
  } from "./ListboxPicker.svelte";
  import PickerRail from "./PickerRail.svelte";
  import PickerRailRow from "./PickerRailRow.svelte";
  import UsageRings from "./UsageRings.svelte";

  let {
    readError = null,
    onRetry,
    scrollPanel = false,
    view,
    snapshots = $bindable(),
    models,
    backendEffortOptions,
    disabled = false,
    keepOpenWhenDisabled = false,
    showUsage = false,
    below = false,
    chevron = "typed",
    label = "Model",
    attributes = {},
    afterChoose,
    onChooseBackend,
    onChooseModel
  }: {
    /** Scoped transport failure; it supplies no canonical launch judgment. */
    readError?: string | null;
    onRetry?: () => Promise<void>;
    scrollPanel?: boolean;
    view: ModelPickerView;
    snapshots: readonly BackendSnapshot[];
    models: readonly BackendModel[];
    backendEffortOptions: readonly string[];
    disabled?: boolean;
    /** Keep an open backend catalogue visible while a persisted selection saves. */
    keepOpenWhenDisabled?: boolean;
    /** Which chevron the trigger draws. The composer asks for the drawn one so its rail
     *  matches the icons beside it; the setup screens keep the typed caret they had. */
    chevron?: "typed" | "drawn" | "none";
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
  let olderExpanded = $state(false);
  let pendingModel = $state<string | null>(null);
  let feedback = $state<string | null>(null);
  let refreshing = $state(false);
  let picker = $state<ListboxPickerController>(null!);

  let displayed = $derived({
    backendKey: view.backendKey,
    model: view.modelValue,
    reasoningEffort: view.reasoningEffort || null
  });
  let currentEffort = $derived(displayed.reasoningEffort);
  let activeBackendKey = $derived(displayed.backendKey);
  let activeBackendName = $derived(activeBackendKey ? activeBackendKey[0].toUpperCase() + activeBackendKey.slice(1) : "");
  let effortRows = $derived(activeBackendKey === "hermes" ? [] :
    effortOptionsFor(models, pendingModel ?? displayed.model ?? "", backendEffortOptions)
      .map((effort) => ({ value: effort, name: effort })));
  const olderRow = "\0older-models";
  let modelRows = $derived([
    ...view.models,
    ...(view.olderModels.length ? [{value: olderRow, name: `Older models (${view.olderModels.length})`}] : []),
    ...(olderExpanded ? view.olderModels : [])
  ]);
  let rows = $derived((showing === "models" ? modelRows : effortRows) as readonly ListboxPickerItem[]);
  let chosenValue = $derived(showing === "models" ? displayed.model : currentEffort ?? "");
  let triggerFace = $derived(view.face);
  let foot = $derived(feedback ?? readError ?? (showing === "models" ? view.emptyReason : null));
  let pickerBusy = $derived(disabled);
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
    olderExpanded = view.olderModels.some(row => row.value === displayed.model);
  }

  function take(choice: ModelPickerChoice): void {
    if (disabled) return;
    if (showing === "models") {
      if (choice.value === olderRow) {
        olderExpanded = !olderExpanded;
        return;
      }
      const efforts = activeBackendKey === "hermes" ? [] : effortOptionsFor(models, choice.value, backendEffortOptions);
      if (!efforts.length) {
        finish(choice.value, null);
        return;
      }
      pendingModel = choice.value;
      showing = "efforts";
      void tick().then(() => picker.setActiveValue(currentEffort ?? ""));
      return;
    }
    finish(pendingModel ?? displayed.model ?? "", choice.value);
  }

  function finish(model: string, effort: string | null): void {
    onChooseModel(model, effort);
    pendingModel = null;
    picker.close(!afterChoose);
    if (afterChoose) void tick().then(afterChoose);
  }

  function chooseBackend(key: ConversationBackendKey, _unavailableReason: string | null): void {
    if (disabled) return;
    if (view.backendLocked && key !== activeBackendKey) {
      feedback = _unavailableReason;
      return;
    }
    feedback = null;
    olderExpanded = false;
    showing = "models";
    pendingModel = null;
    if (key !== activeBackendKey) onChooseBackend(key, {model: null, reasoningEffort: null});
    void tick().then(() => picker.setActiveValue(displayed.model));
  }

  function showReasoning(): void {
    if (disabled) return;
    feedback = null;
    pendingModel = null;
    showing = "efforts";
    void tick().then(() => picker.setActiveValue(currentEffort ?? ""));
  }

  function showModels(): void {
    if (disabled) return;
    showing = "models";
    pendingModel = null;
    void tick().then(() => picker.setActiveValue(displayed.model));
  }

  async function runRefresh(): Promise<void> {
    if (refreshing) return;
    refreshing = true;
    feedback = null;
    try {
      if (onRetry) {
        await onRetry();
      } else {
        const result = await refreshBackendSnapshots();
        if (result.snapshots !== null) snapshots = result.snapshots;
        feedback = result.error ?? (result.outcomes
          .filter(outcome => outcome.outcome !== "succeeded")
          .map(outcome => `${outcome.backend_key}: ${backendUsageOutcomeLabel(outcome.outcome)}`)
          .join(" · ") || null);
      }
    } catch (error) {
      feedback = error instanceof Error ? error.message : "The read failed. Check again.";
    } finally { refreshing = false; }
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
  label={`${label}: ${activeBackendName} ${triggerFace}`.trim()}
  listLabel={showing === "models" ? "Models" : "Reasoning efforts"}
  kind="rail"
  {chevron}
  {scrollPanel}
  {below}
  attributes={{ "data-conversation-model-picker": "", ...attributes }}
  panelBusy={pickerBusy}
  bind:controller={picker}
  triggerAttributes={{ "data-conversation-picker-trigger": "" }}
  panelAttributes={{ "data-conversation-picker-panel": "" }}
  optionAttributes={(choice, active, selected) => ({
    "data-conversation-picker-choice": choice.value === olderRow ? undefined : choice.value,
    "aria-expanded": choice.value === olderRow ? String(olderExpanded) : undefined,
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
    {#if activeBackendKey}<BackendMark backend={activeBackendKey} />{/if}
    {#if triggerFace}<span class="model-picker-face">{triggerFace}</span>{/if}
  {/snippet}
  {#snippet beforeList()}
    <PickerRail label="Backend and reasoning" attributes={{ "data-conversation-backend-rail": "" }}>
      {#each view.backends as backend (backend.key)}
        <PickerRailRow
          held={backend.key === activeBackendKey}
          on={backend.key === activeBackendKey && showing === "models"}
          dim={view.backendLocked && backend.key !== activeBackendKey}
          disabled={pickerBusy}
          label={backend.name}
          attributes={{
            "data-conversation-backend": backend.key,
            "data-conversation-backend-showing": backend.key === activeBackendKey ? "true" : undefined,
            "aria-pressed": backend.key === activeBackendKey && showing === "models" ? "true" : "false",
            "aria-disabled": view.backendLocked && backend.key !== activeBackendKey ? "true" : undefined
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
          disabled={pickerBusy || refreshing}
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
        {#if effortRows.length}
        <PickerRailRow
          on={showing === "efforts"}
          disabled={pickerBusy}
          label="Reasoning"
          attributes={{
            "data-conversation-picker-reasoning": "",
            "aria-pressed": showing === "efforts" ? "true" : "false",
          }}
          onclick={showReasoning}
        >
          {#snippet icon()}
            <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3.5 14h3.4v6.5H3.5Zm6.8-4h3.4v10.5h-3.4Zm6.8-6.5h3.4v17h-3.4Z" /></svg>
          {/snippet}
        </PickerRailRow>
        {/if}
      </div>
    </PickerRail>
  {/snippet}
  {#snippet optionContent(choice, selected)}
    <span class="model-picker-choice-name">{#if choice.value === olderRow}<span aria-hidden="true">{olderExpanded ? "▾" : "▸"} </span>{/if}{choice.name}
    </span>
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
      <div class="model-picker-foot" data-conversation-picker-feedback>
        <div class="model-picker-summary" aria-live="polite">{foot}</div>
      </div>
      {/if}
  {/snippet}
</ListboxPicker>

<style>
  .model-picker-face { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-tools { margin-top: auto; padding-top: var(--space-1); border-top: var(--border-hairline) solid var(--border-color); }
  .model-picker-tools svg { width: 13px; height: 13px; }
  .model-picker-choice-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-foot {
    grid-column: 1 / -1; border-top: var(--border-hairline) solid var(--border-color);
    background: var(--surface-recessed); color: var(--text-muted); font-size: var(--type-xs);
    line-height: 1.45; padding: var(--space-2) var(--space-3);
  }
</style>
