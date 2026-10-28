<script lang="ts">
  import type { PickerRow, CanonicalModelValidation } from "../../lib/conversation/modelResolutionWire";
  import { pickerRowName, pickerRowDetail, validationSummary, validationDetail } from "../../lib/conversation/modelPickerFacts";
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
    canonicalRows,
    validation,
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
    canonicalRows?: readonly PickerRow[];
    validation?: CanonicalModelValidation | null;
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
    onChooseModel: (model: string, reasoningEffort: string | null, row?: PickerRow) => void;
  } = $props();

  let showing = $state<"models" | "efforts">("models");
  let whyOpen = $state(false);
  let pendingCanonicalRow = $state<PickerRow | null>(null);
  let pendingModel = $state<string | null>(null);
  let feedback = $state<string | null>(null);
  let refreshing = $state(false);
  let picker = $state<ListboxPickerController>(null!);

  let effortRows = $derived(
    ((pendingCanonicalRow ?? canonicalSelection) ? (pendingCanonicalRow ?? canonicalSelection)?.supported_reasoning_efforts ?? [] : effortOptionsFor(models, pendingModel ?? view.modelValue, backendEffortOptions))
      .map((effort) => ({ value: effort, name: effort }))
  );
  let factualRows = $derived(canonicalRows?.map((row, index) => ({value: `canonical-${index}`, name: pickerRowName(row)})));
  let rows = $derived((showing === "models" ? factualRows ?? view.models : effortRows) as readonly ListboxPickerItem[]);
  let chosenValue = $derived(showing === "models" ? canonicalRows ? currentRows.length === 1 ? `canonical-${canonicalRows.indexOf(currentRows[0])}` : null : view.modelValue : currentEffort);
  let currentRows = $derived(canonicalRows?.filter(row => row.current_selection) ?? []);
  let canonicalSelection = $derived(currentRows.length === 1 ? currentRows[0] : undefined);
  let effectiveValidation = $derived(validation ?? canonicalSelection?.validation ?? null);
  let currentEffort = $derived(canonicalRows && effectiveValidation ? effectiveValidation.selection.reasoning_effort : view.reasoningEffort || null);
  let triggerFace = $derived(canonicalRows ? currentRows.length > 1 ? "Multiple current selection facts" : canonicalSelection ? pickerRowName(canonicalSelection) : effectiveValidation?.selection.submitted_token ?? "Selection unobserved" : view.face);
  let foot = $derived(feedback ?? (effectiveValidation && effectiveValidation.state !== "compatible" ? validationSummary(effectiveValidation) : null) ?? (canonicalRows ? null : view.staleModelReason ?? view.catalogueWarning));
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
    pendingCanonicalRow = null;
    whyOpen = false;
    feedback = null;
  }

  function take(choice: ModelPickerChoice): void {
    if (disabled) return;
    if (showing === "models") {
      const row = canonicalRows?.[Number(choice.value.replace("canonical-", ""))];
      const model = row?.submitted_token ?? choice.value;
      if (row && (row.supported_reasoning_efforts === null || row.supported_reasoning_efforts.length === 0)) {
        onChooseModel(model, currentEffort, row);
        picker.close(!afterChoose);
        if (afterChoose) void tick().then(afterChoose);
        return;
      }
      pendingCanonicalRow = row ?? null;
      const effort = row ? (currentEffort !== null && row.supported_reasoning_efforts?.includes(currentEffort) ? currentEffort : row.supported_reasoning_efforts?.[0] ?? null) : modelSelectionEffort(
        models,
        backendEffortOptions,
        choice.value,
        view.reasoningEffort || null,
        view.defaultReasoningEffort
      );
      if (effort === null) {
        onChooseModel(model, null);
        picker.close(!afterChoose);
        if (afterChoose) void tick().then(afterChoose);
        return;
      }
      pendingModel = model;
      showing = "efforts";
      void tick().then(() => picker.setActiveValue(effort));
      return;
    } else {
      onChooseModel(pendingModel ?? view.modelValue, choice.value, pendingCanonicalRow ?? canonicalSelection);
      pendingModel = null;
    }
    picker.close(!afterChoose);
    if (afterChoose) void tick().then(afterChoose);
  }

  function chooseBackend(key: ConversationBackendKey, unavailableReason: string | null): void {
    if (disabled) return;
    if (!canonicalRows && unavailableReason !== null) {
      feedback = unavailableReason;
      return;
    }
    feedback = null;
    showing = "models";
    pendingModel = null;
    pendingCanonicalRow = null;
    if (key === view.backendKey) {
      picker.setActiveValue(canonicalRows ? chosenValue : view.modelValue);
      return;
    }
    onChooseBackend(key, canonicalRows ? {model: view.modelValue || null, reasoningEffort: view.reasoningEffort || null} : backendSelectionDefaults(snapshots, key));
    void tick().then(() => picker.setActiveValue(canonicalRows ? chosenValue : view.modelValue));
  }

  function showReasoning(): void {
    if (disabled) return;
    if (canonicalSelection && !canonicalSelection.supported_reasoning_efforts?.length) {
      feedback = canonicalSelection.supported_reasoning_efforts === null ? "Reasoning effort support is unknown." : "This catalog row lists no reasoning efforts.";
      return;
    }
    if (!canonicalRows && view.reasoningUnavailableReason !== null) {
      feedback = view.reasoningUnavailableReason;
      return;
    }
    feedback = null;
    pendingModel = null;
    pendingCanonicalRow = null;
    showing = "efforts";
    void tick().then(() => picker.setActiveValue(view.reasoningEffort));
  }

  function showModels(): void {
    if (disabled) return;
    showing = "models";
    pendingModel = null;
    pendingCanonicalRow = null;
    void tick().then(() => picker.setActiveValue(canonicalRows ? chosenValue : view.modelValue));
  }

  async function runRefresh(): Promise<void> {
    if (refreshing) return;
    refreshing = true;
    feedback = null;
    try {
      if (onRetry) await onRetry();
      else if (canonicalRows) return;
      else {
        const result = await refreshBackendSnapshots();
        if (result.snapshots !== null) snapshots = result.snapshots;
        feedback = result.error;
      }
    } catch { feedback = "The read failed. Check again."; }
    finally { refreshing = false; }
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
  label={`${label}: ${view.backendName} ${triggerFace}`.trim()}
  listLabel={showing === "models" ? "Models" : "Reasoning efforts"}
  kind="rail"
  {chevron}
  {scrollPanel}
  {below}
  attributes={{ "data-conversation-model-picker": "", "data-canonical-model-picker": canonicalRows ? "" : undefined, ...attributes }}
  panelBusy={pickerBusy}
  bind:controller={picker}
  triggerAttributes={{ "data-conversation-picker-trigger": "" }}
  panelAttributes={{ "data-conversation-picker-panel": "" }}
  optionAttributes={(choice, active, selected) => ({
    "aria-label": canonicalRows && showing === "models" ? pickerRowDetail(canonicalRows[Number(choice.value.replace("canonical-", ""))]) : undefined,
    "title": canonicalRows && showing === "models" ? pickerRowDetail(canonicalRows[Number(choice.value.replace("canonical-", ""))]) : undefined,
    "data-conversation-picker-choice": canonicalRows && showing === "models" ? canonicalRows[Number(choice.value.replace("canonical-", ""))]?.submitted_token : choice.value,
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
    {#if triggerFace}<span class="model-picker-face">{triggerFace}</span>{/if}
  {/snippet}
  {#snippet beforeList()}
    <PickerRail label="Backend and reasoning" attributes={{ "data-conversation-backend-rail": "" }}>
      {#each view.backends as backend (backend.key)}
        <PickerRailRow
          held={backend.selected}
          on={backend.selected && showing === "models"}
          dim={!canonicalRows && backend.unavailableReason !== null}
          disabled={pickerBusy}
          label={backend.name}
          attributes={{
            "data-conversation-backend": backend.key,
            "data-conversation-backend-showing": backend.selected ? "true" : undefined,
            "aria-pressed": backend.selected && showing === "models" ? "true" : "false",
            "aria-disabled": !canonicalRows && backend.unavailableReason !== null ? "true" : undefined
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
          disabled={pickerBusy || (canonicalRows !== undefined && !onRetry)}
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
    <span class="model-picker-choice-name">{choice.name}
      {#if canonicalRows && showing === "models"}
        {@const row = canonicalRows[Number(choice.value.replace("canonical-", ""))]}
        {#if row}<span class="model-picker-choice-detail">Effort: {row.supported_reasoning_efforts === null ? "unknown" : row.supported_reasoning_efforts.join(", ") || "none"} · {row.validation.state}</span>{/if}
      {/if}
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
        <div class="model-picker-warning-actions">
          <button type="button" data-listbox-picker-action data-model-picker-why aria-expanded={whyOpen} onclick={() => whyOpen = !whyOpen}>Why</button>
          <button type="button" data-listbox-picker-action data-model-picker-check disabled={canonicalRows !== undefined && !onRetry} aria-disabled={refreshing} aria-busy={refreshing} onmousedown={(event) => event.preventDefault()} onclick={() => void runRefresh()}>Check again</button>
        </div>
        {#if whyOpen}
          <div class="model-picker-detail">{effectiveValidation ? validationDetail(effectiveValidation) : `${foot} Observation context and corrective action are unavailable in this legacy read. This does not establish a native destination failure. Check again repeats the same read.`}</div>
        {/if}
      </div>
      {/if}
  {/snippet}
</ListboxPicker>

<style>
  :global([data-canonical-model-picker] .picker-rail-row.dim) { color: var(--text-muted); opacity: 1; }
  .model-picker-face { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-tools { margin-top: auto; padding-top: var(--space-1); border-top: var(--border-hairline) solid var(--border-color); }
  .model-picker-tools svg { width: 13px; height: 13px; }
  .model-picker-choice-name { min-width: 0; overflow-wrap: anywhere; }
  .model-picker-choice-detail { display: block; font-size: var(--type-xs); color: var(--text-muted); }
  .model-picker-summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-warning-actions { display: flex; gap: var(--space-2); }
  .model-picker-warning-actions button { min-height: 24px; min-width: 24px; color: var(--text-default); background: transparent; border: 0; cursor: pointer; font: inherit; }
  .model-picker-detail { overflow-wrap: anywhere; margin-top: var(--space-2); }
  .model-picker-foot {
    grid-column: 1 / -1; border-top: var(--border-hairline) solid var(--border-color);
    background: var(--surface-recessed); color: var(--text-muted); font-size: var(--type-xs);
    line-height: 1.45; padding: var(--space-2) var(--space-3);
  }
</style>
