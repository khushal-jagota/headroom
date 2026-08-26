<script lang="ts">
  import { tick } from "svelte";
  import {
    backendSelectionDefaults,
    modelSelectionEffort,
    type ModelPickerChoice,
    type ModelPickerView
  } from "../../lib/conversation/modelPicker";
  import type { BackendModel, BackendSnapshot, ConversationBackendKey } from "../../lib/conversation/wire";
  import BackendMark from "./BackendMark.svelte";
  import UsageRings from "./UsageRings.svelte";

  let {
    view,
    snapshots,
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
    onChooseModel,
    onChooseReasoningEffort
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
    onChooseReasoningEffort: (reasoningEffort: string) => void;
  } = $props();

  const rowIdStem = $props.id();
  let open = $state(false);
  let showing = $state<"models" | "efforts">("models");
  let activeIndex = $state(0);
  let feedback = $state<string | null>(null);
  let root = $state<HTMLDivElement | null>(null);
  let trigger = $state<HTMLButtonElement | null>(null);
  let list = $state<HTMLDivElement | null>(null);

  let rows = $derived(showing === "models" ? view.models : view.efforts);
  let chosenValue = $derived(showing === "models" ? view.modelValue : view.reasoningEffort);
  let active = $derived(rows.length === 0 ? 0 : Math.min(activeIndex, rows.length - 1));
  let foot = $derived(feedback ?? view.staleModelReason);

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

  $effect(() => {
    if (!open) return;
    list?.focus();
  });

  $effect(() => {
    if (disabled && !keepOpenWhenDisabled) open = false;
  });

  $effect(() => {
    if (!open) return;
    function closeOutside(event: PointerEvent): void {
      if (event.target instanceof Node && root !== null && !root.contains(event.target)) {
        open = false;
      }
    }
    document.addEventListener("pointerdown", closeOutside, true);
    return () => document.removeEventListener("pointerdown", closeOutside, true);
  });

  $effect(() => {
    active;
    list
      ?.querySelector<HTMLElement>("[data-conversation-picker-active]")
      ?.scrollIntoView({ block: "nearest" });
  });

  function openPanel(): void {
    open = true;
    showing = "models";
    feedback = null;
    activeIndex = Math.max(0, view.models.findIndex((choice) => choice.value === view.modelValue));
  }

  function closeToTrigger(): void {
    open = false;
    trigger?.focus();
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
      onChooseModel(choice.value, effort);

      if (effort !== null) {
        showing = "efforts";
        open = true;
        void tick()
          .then(() => {
            activeIndex = Math.max(
              0,
              view.efforts.findIndex((option) => option.value === effort)
            );
            return tick();
          })
          .then(() => list?.focus());
        return;
      }
    } else {
      onChooseReasoningEffort(choice.value);
    }
    open = false;
    void tick().then(() => (afterChoose ? afterChoose() : trigger?.focus()));
  }

  function chooseBackend(key: ConversationBackendKey, unavailableReason: string | null): void {
    if (disabled) return;
    if (unavailableReason !== null) {
      feedback = unavailableReason;
      return;
    }
    feedback = null;
    showing = "models";
    activeIndex = 0;
    if (key === view.backendKey) {
      void tick().then(() => list?.focus());
      return;
    }
    onChooseBackend(key, backendSelectionDefaults(snapshots, key));
    void tick().then(() => list?.focus());
  }

  function showReasoning(): void {
    if (disabled) return;
    if (view.reasoningUnavailableReason !== null) {
      feedback = view.reasoningUnavailableReason;
      return;
    }
    feedback = null;
    showing = "efforts";
    activeIndex = Math.max(
      0,
      view.efforts.findIndex((choice) => choice.value === view.reasoningEffort)
    );
    void tick().then(() => list?.focus());
  }

  function onPanelKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      closeToTrigger();
      return;
    }
    if (event.target !== list) return;
    if (rows.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : rows.length - 1;
      activeIndex = (active + step) % rows.length;
      return;
    }
    const highlighted = rows[active];
    if (event.key === "Enter" && highlighted !== undefined) {
      event.preventDefault();
      take(highlighted);
    }
  }
</script>

<div class="model-picker" bind:this={root} data-conversation-model-picker {...attributes}>
  <button
    type="button"
    class="model-picker-trigger"
    class:is-open={open}
    bind:this={trigger}
    data-conversation-picker-trigger
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-label={`${label}: ${view.backendName} ${view.face}`.trim()}
    {disabled}
    onclick={() => (open ? closeToTrigger() : openPanel())}
  >
    {#if view.backendKey}<BackendMark backend={view.backendKey} />{/if}
    {#if view.face}<span class="model-picker-face">{view.face}</span>{/if}
    <span class="model-picker-chevron" aria-hidden="true">{open ? "⌃" : "⌄"}</span>
  </button>

  {#if open}
    <div
      class="model-picker-panel"
      class:below
      data-conversation-picker-panel
      aria-busy={disabled}
      role="presentation"
      onkeydown={onPanelKeydown}
      onfocusout={(event) => {
        if (!(event.relatedTarget instanceof Node) || !root?.contains(event.relatedTarget)) {
          open = false;
        }
      }}
    >
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
            disabled={disabled}
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
        <div class="model-picker-reasoning">
          <button
            type="button"
            class="model-picker-rail-row"
            class:on={showing === "efforts"}
            class:dim={view.reasoningUnavailableReason !== null}
            data-conversation-picker-reasoning
            aria-pressed={showing === "efforts"}
            aria-disabled={view.reasoningUnavailableReason !== null}
            disabled={disabled}
            onmousedown={(event) => event.preventDefault()}
            onclick={showReasoning}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3.5 14h3.4v6.5H3.5Zm6.8-4h3.4v10.5h-3.4Zm6.8-6.5h3.4v17h-3.4Z" /></svg>
            <span>Reasoning</span>
          </button>
        </div>
      </div>

      <div
        class="model-picker-list"
        id={rowIdStem}
        bind:this={list}
        role="listbox"
        aria-label={showing === "models" ? "Models" : "Reasoning efforts"}
        aria-activedescendant={rows.length === 0 ? undefined : `${rowIdStem}-${active}`}
        tabindex="-1"
      >
        {#each rows as choice, index (choice.value)}
          <button
            type="button"
            class="model-picker-choice"
            class:cursor={index === active}
            class:on={choice.value === chosenValue}
            id={`${rowIdStem}-${index}`}
            role="option"
            aria-selected={choice.value === chosenValue}
            data-conversation-picker-choice={choice.value}
            data-conversation-picker-active={index === active ? "true" : undefined}
            data-conversation-picker-chosen={choice.value === chosenValue ? "true" : undefined}
            disabled={disabled}
            onmouseenter={() => (activeIndex = index)}
            onmousedown={(event) => event.preventDefault()}
            onclick={() => take(choice)}
          >
            <span class="model-picker-choice-name">{choice.name}</span>
            {#if showUsage && showing === "models" && hasModelUsage(choice.value)}
              <UsageRings
                windows={snapshotFor(view.backendKey ?? "claude")?.cached_usage?.windows ?? []}
                modelId={choice.value}
                label={`Usage allowances for ${choice.name}`}
                compact
              />
            {/if}
            <span class="model-picker-tick" aria-hidden="true">{choice.value === chosenValue ? "✓" : ""}</span>
          </button>
        {/each}
      </div>

      {#if foot}
        <div class="model-picker-foot" data-conversation-picker-feedback aria-live="polite">{foot}</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .model-picker { position: relative; display: inline-flex; min-width: 0; }
  .model-picker-trigger {
    display: inline-flex; align-items: center; gap: var(--space-2); min-width: 0; max-width: 100%;
    background: transparent; border: var(--border-hairline) solid transparent;
    border-radius: var(--radius-pill); color: var(--text-muted); cursor: pointer;
    font-family: var(--font-mono); font-size: var(--type-xs); line-height: 1.2;
    padding: var(--space-1) var(--space-2);
  }
  .model-picker-trigger:hover { border-color: var(--border-color); color: var(--text-strong); }
  .model-picker-trigger.is-open { background: var(--surface-2); border-color: var(--border-color); color: var(--text-strong); }
  .model-picker-trigger:disabled { cursor: default; opacity: .5; }
  .model-picker-face { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-chevron { color: var(--text-faintest); flex: none; }
  .model-picker-panel {
    position: absolute; z-index: 40; bottom: calc(100% + var(--space-1)); left: 0;
    display: grid; grid-template-columns: auto minmax(0, 1fr); width: 296px;
    max-width: calc(100vw - var(--space-4) * 2); background: var(--surface-2);
    border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-lg);
    overflow: hidden;
  }
  .model-picker-panel.below { top: calc(100% + var(--space-1)); bottom: auto; }
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
  .model-picker-panel[aria-busy="true"] .model-picker-rail-row,
  .model-picker-panel[aria-busy="true"] .model-picker-choice { cursor: progress; opacity: .55; }
  .model-picker-reasoning { margin-top: auto; padding-top: var(--space-1); border-top: var(--border-hairline) solid var(--border-color); }
  .model-picker-reasoning svg { width: 13px; height: 13px; }
  .model-picker-list { min-width: 0; padding-block: var(--space-1); outline: none; }
  .model-picker-choice {
    display: flex; align-items: center; justify-content: space-between; gap: var(--space-3);
    width: 100%; background: transparent; border: 0; color: var(--text-default);
    cursor: pointer; font: inherit; font-size: var(--type-sm); padding: var(--space-2) var(--space-3); text-align: left;
  }
  .model-picker-choice:hover { background-image: var(--interaction-hover); color: var(--text-strong); }
  .model-picker-choice.cursor { background: var(--surface-recessed); color: var(--text-strong); }
  .model-picker-choice.on { color: var(--text-strong); }
  .model-picker-choice-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .model-picker-tick { flex: none; font-family: var(--font-mono); font-size: var(--type-xs); }
  .model-picker-foot {
    grid-column: 1 / -1; border-top: var(--border-hairline) solid var(--border-color);
    background: var(--surface-recessed); color: var(--text-faint); font-size: var(--type-xs);
    line-height: 1.45; padding: var(--space-2) var(--space-3);
  }
  @media (max-width: 620px) { .model-picker-panel { width: min(274px, calc(100vw - var(--space-4) * 2)); } }
</style>
