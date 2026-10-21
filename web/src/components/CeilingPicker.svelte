<script lang="ts">
  import { tick } from "svelte";
  import ListboxPicker, {
    type ListboxPickerController,
    type ListboxPickerItem
  } from "./conversation/ListboxPicker.svelte";
  import PickerRail from "./conversation/PickerRail.svelte";
  import PickerRailRow from "./conversation/PickerRailRow.svelte";
  import { ceilingOptionsFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import { holderOptionsFor, holderValue, holderFromValue, holderLabelFor } from "../lib/ceilingHolder";
  import { pendingCeilingApproves, type PendingCeilingIntent } from "../lib/pendingCeiling";
  import { ceilingLabelFor } from "../lib/ceilingPresentation";
  import type { Principal } from "../lib/types";

  // The whole ceiling in one control: how far the Ticket may go, and who is asked when
  // it gets there. It reads "Until Approach · then User", and the approve row is where a
  // ceiling is set with both halves live. The two halves sit in a rail on the left, the
  // same shape as the model picker, so either one can be edited on its own — changing
  // the reviewer never touches the ceiling. The Backlog create form is its own markup
  // because it writes on change and offers no ceiling, but it takes its words and its
  // options from `ceilingHolder.ts`, so a change there lands on both.
  let {
    newStage,
    lifecycle = null,
    ceiling = $bindable<string | null>(null),
    holder = $bindable<Principal | null>(null),
    manager = null,
    stageLocked = false,
    below = false,
    document = false,
    triggerLabel = null,
    holderLabel = null,
    pendingStage = null,
    approvalCeiling = null,
    externalTrigger = false,
    controller = $bindable(),
    onComplete
  }: {
    newStage: string | null;
    pendingStage?: string | null;
    approvalCeiling?: string | null;
    externalTrigger?: boolean;
    document?: boolean;
    controller?: { openApproval: (anchor?: HTMLElement) => void; openReviewer: (anchor?: HTMLElement) => void; readonly expanded: boolean; readonly controls: string | undefined };
    triggerLabel?: string | null;
    holderLabel?: ((holder: Principal | null) => string) | null;
    lifecycle?: Lifecycle | null;
    ceiling?: string | null;
    holder?: Principal | null;
    manager?: { id: string; title: string } | null;
    stageLocked?: boolean;
    /** Open the panel downward, for a host that sits under the page header. */
    below?: boolean;
    /** Only the halves the user set. A reviewer chosen on its own carries no ceiling. */
    onComplete?: (change: { ceiling?: string; holder: Principal | null; approval?: boolean }) => void;
  } = $props();

  // Everything the reader is part-way through is a draft. Nothing reaches the bound
  // values or `onComplete` until both halves are answered, so a dismissed panel leaves
  // the Ticket exactly as it found it and the closed trigger never shows an unsaved
  // choice.
  let requestedApproval = false;
  let intent = $state<PendingCeilingIntent>("scope");
  let approvalAnchor = $state<HTMLElement | null>(null);
  let requestedPart: "ceiling" | "holder" | null = null;
  function openAt(part: "ceiling" | "holder", approval: boolean, anchor?: HTMLElement): void {
    picker.close(false);
    approvalAnchor = anchor ?? null;
    requestedApproval = approval;
    requestedPart = part;
    picker.open();
  }
  controller = {
    openApproval: (anchor?: HTMLElement) => openAt("ceiling",true,anchor),
    openReviewer: (anchor?: HTMLElement) => openAt("holder",false,anchor),
    get expanded() { return picker?.expanded ?? false; },
    get controls() { return picker?.controls; }
  };
  let showing = $state<"ceiling" | "holder">("ceiling");
  let draftCeiling = $state<string | null>(null);
  let draftHolder = $state<Principal | null>(null);
  let ceilingTouched = $state(false);
  let holderTouched = $state(false);
  let picker = $state<ListboxPickerController>(null!);
  let ceilingOptions = $derived([
    ...(!pendingStage ? [{ value:"none", name:"No further" }] : []),
    ...ceilingOptionsFor(lifecycle, pendingStage ? lifecycle?.stageOrder[0] || pendingStage : newStage || lifecycle?.ceilingRange[0] || "needs_success_condition")
      .map((option) => ({ value:option.value, name:option.label }))
  ]);
  let holders = $derived(holderOptionsFor(manager, holder).map((option) => ({
    value: option.value, name: pendingStage ? (holderLabel ? holderLabel(holderFromValue(option.value,manager)) : option.label) : `${option.label}${option.detail ? ` · ${option.detail}` : ""}`, detail:pendingStage ? undefined : option.detail
  })));
  let rows = $derived((showing === "ceiling" ? ceilingOptions : holders) as readonly ListboxPickerItem[]);
  let draftHolderValue = $derived(holderValue(draftHolder ?? holder));
  let selectedValue = $derived(showing === "ceiling" ? draftCeiling : draftHolderValue);
  // The trigger reports the saved ceiling, never a draft.
  let ceilingName = $derived(ceilingLabelFor(lifecycle,ceiling));
  let holderName = $derived(holderLabel ? holderLabel(holder) : holderLabelFor(manager, holder));
  let draftCeilingName = $derived(ceilingLabelFor(lifecycle,draftCeiling));
  let draftHolderName = $derived(holderLabel ? holderLabel(draftHolder ?? holder) : holderLabelFor(manager,draftHolder ?? holder));
  let resolvedTriggerLabel = $derived(triggerLabel ?? (stageLocked
    ? `then ${holderName}`
    : `Until ${ceilingName ?? "Choose ceiling"} · then ${holderName}`));

  function preferredCeiling(): string | null {
    return ceiling ?? preferredScopeCeilingFor(lifecycle, newStage) ?? "none";
  }

  // Drafts start and end with the panel, so a dismissal discards them.
  function reset(): void {
    if (requestedPart === null) approvalAnchor = null;
    intent = requestedApproval ? "approve" : "scope";
    requestedApproval = false;
    draftCeiling = intent === "approve" && approvalCeiling ? approvalCeiling : preferredCeiling();
    draftHolder = null;
    ceilingTouched = false;
    holderTouched = false;
    showing = requestedPart ?? (stageLocked ? "holder" : "ceiling");
    requestedPart = null;
  }

  function show(part: "ceiling" | "holder"): void {
    if (part === "ceiling" && stageLocked) return;
    showing = part;
    void tick().then(() => picker.setActiveValue(part === "ceiling" ? draftCeiling : draftHolderValue));
  }

  // The one place a draft is published. A host that saves owns the value: it is told
  // once and the control reads back whatever the Ticket then has, so a refused save
  // cannot leave this trigger claiming a ceiling the Ticket never took. A host that only
  // binds, like the approve row, has nowhere else to keep it, so it is written here.
  function commit(): void {
    const nextHolder = draftHolder ?? holder;
    const nextCeiling = ceilingTouched ? draftCeiling : null;
    if (onComplete) {
      const approves = ceilingTouched && pendingStage !== null && pendingCeilingApproves(lifecycle,pendingStage,ceiling,draftCeiling,ceilingTouched,intent);
      onComplete({ ...(nextCeiling !== null ? { ceiling:nextCeiling } : approves && draftCeiling ? { ceiling:draftCeiling } : {}), holder:nextHolder,
        ...(pendingStage ? { approval:approves } : {}) });
    } else {
      if (nextCeiling !== null) ceiling = nextCeiling;
      holder = nextHolder;
    }
    picker.close();
  }

  function choose(value: string): void {
    if (showing === "ceiling") {
      draftCeiling = value;
      ceilingTouched = true;
      if (holderTouched) return commit();
      return show("holder");
    }
    draftHolder = holderFromValue(value, manager);
    holderTouched = true;
    // The approve row starts with no ceiling at all and cannot approve without one, so
    // the unanswered half is asked for rather than guessed. A held stage cannot be
    // asked for, so there the reviewer alone completes the choice.
    if (!ceilingTouched && ceiling === null && !stageLocked) return show("ceiling");
    commit();
  }
</script>

<div class:document>
<ListboxPicker
  items={rows}
  {selectedValue}
  label={resolvedTriggerLabel}
  listLabel={showing === "ceiling" ? "Ceiling stage" : "Who holds the ceiling"}
  kind="rail"
  hideTrigger={externalTrigger}
  anchor={approvalAnchor}
  {below}
  attributes={{ "data-ceiling-picker": "" }}
  triggerAttributes={{ "data-scope-ceiling": "" }}
  panelAttributes={{ "data-ceiling-picker-panel": "" }}
  bind:controller={picker}
  onOpen={reset}
  onClose={() => { requestedApproval = false; reset(); }}
  onChoose={choose}
  optionAttributes={(choice) => ({
    "data-ceiling-picker-choice": choice.value,
    "data-scope-holder": showing === "holder" ? choice.value : undefined
  })}
>
  {#snippet triggerContent()}
    {#if document}
      <span>Approved until <b>{ceilingName ?? "Choose ceiling"}</b>, then <b>{holderName}</b> reviews</span>
    {:else}<span>{resolvedTriggerLabel}</span>{/if}
  {/snippet}
  {#snippet beforeList()}
    <PickerRail label="Parts of the ceiling" attributes={{ "data-ceiling-rail": "" }}>
      <!-- Each rail row keeps its current choice visible while the other half's list
           is open. The active half is also ticked in the list. -->
      <PickerRailRow
        on={showing === "ceiling"}
        dim={stageLocked}
        disabled={stageLocked}
        label="How far"
        detail={draftCeilingName}
        attributes={{
          "data-ceiling-part": "ceiling",
          "aria-pressed": showing === "ceiling" ? "true" : "false",
          "aria-disabled": stageLocked ? "true" : undefined
        }}
        onclick={() => show("ceiling")}
      />
      <PickerRailRow
        on={showing === "holder"}
        label="Who reviews"
        detail={draftHolderName}
        attributes={{
          "data-ceiling-part": "holder",
          "aria-pressed": showing === "holder" ? "true" : "false"
        }}
        onclick={() => show("holder")}
      />
    </PickerRail>
    {#if document}<div class="document-list-label">{showing === "ceiling" ? "Ceiling stage" : "Who holds the ceiling"}</div>{/if}
  {/snippet}
  {#snippet optionContent(choice)}
    {#if showing === "holder"}
      {@const option = holders.find((item) => item.value === choice.value)}
      <span>{pendingStage ? choice.name : holderOptionsFor(manager,holder).find((item) => item.value === choice.value)?.label ?? choice.name}</span>
      {#if option?.detail}<small class="holder-detail">{option.detail}</small>{/if}
    {:else}<span>{choice.name}</span>{/if}
  {/snippet}
</ListboxPicker>
</div>

<style>
  .document { width:100%; }
  .document :global(.listbox-picker) { display:block; width:100%; }
  .document :global([data-scope-ceiling]) { display:block; width:fit-content; max-width:calc(100% + 24px); }
  .document :global(.listbox-picker-chevron) { display:inline-block; vertical-align:middle; width:6px; height:6px; margin-left:4px; font-size:0; border-right:1.5px solid currentColor; border-bottom:1.5px solid currentColor; transform:translateY(-2px) rotate(45deg); }
  .document :global([data-scope-ceiling] b) { font-weight:600; color:var(--text-strong); }
  .document :global(.listbox-picker-panel.rail) { width:404px; max-width:calc(100vw - 48px); grid-template-columns:168px minmax(0,1fr); border:0; border-radius:var(--radius-md); box-shadow:var(--shadow-float); background:var(--surface-2); }
  .document :global(.picker-rail) { grid-column:1; grid-row:1 / span 3; background:var(--surface-recessed); border:0; padding:var(--space-2); }
  .document :global(.picker-rail-row) { font-family:var(--font-ui); padding:8px var(--space-2); border-radius:var(--radius-sm); white-space:normal; }
  .document :global(.picker-rail-row-label) { font-size:11px; color:var(--text-faintest); }
  .document :global(.picker-rail-row-detail) { max-width:none; font-size:13px; color:var(--text-default); white-space:normal; overflow:visible; }
  .document :global(.picker-rail-row.on) { background:var(--surface-2); }
  .document-list-label { grid-column:2; grid-row:1; padding:14px 16px 4px; font-family:var(--font-ui); font-size:11px; font-weight:600; line-height:1.6; letter-spacing:.1em; text-transform:uppercase; color:var(--text-faintest); }
  .document :global(.listbox-picker-list) { grid-column:2; grid-row:2; padding:0 var(--space-2); }
  .document :global(.listbox-picker-option) { font-family:var(--font-ui); font-size:13px; line-height:1.6; padding:7px var(--space-2); gap:var(--space-2); border-radius:var(--radius-sm); color:var(--text-faint); }
  .document :global(.listbox-picker-option.on) { font-weight:600; color:var(--text-strong); }
  .document :global(.listbox-picker-option.cursor) { background:transparent; background-image:var(--interaction-hover); color:var(--text-strong); }
  @media (max-width:480px) { .document :global(.listbox-picker-panel.rail) { grid-template-columns:40% minmax(0,1fr); } }
  .holder-detail { color:var(--text-faintest); font-size:var(--type-xs); overflow-wrap:anywhere; }
</style>
