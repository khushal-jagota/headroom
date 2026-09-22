<script lang="ts">
  import { tick } from "svelte";
  import ListboxPicker, {
    type ListboxPickerController,
    type ListboxPickerItem
  } from "./conversation/ListboxPicker.svelte";
  import { ceilingOptionsFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import { holderOptionsFor, holderValue, holderFromValue } from "../lib/ceilingHolder";
  import type { Principal } from "../lib/types";

  // The whole ceiling in one control: how far the Ticket may go, and who is asked when
  // it gets there. It reads "Until Approach · then me", and the approve row is where a
  // ceiling is set with both halves live. The Ticket page leash and the Backlog create
  // form are their own markup because they write on change rather than on a button, but
  // all three take their words and their options from `ceilingHolder.ts`, so a change
  // there lands on every one of them.
  let {
    newStage,
    lifecycle = null,
    ceiling = $bindable<string | null>(null),
    holder = $bindable<Principal | null>(null),
    sprintItem = null,
    stageLocked = false,
    onComplete
  }: {
    newStage: string | null;
    lifecycle?: Lifecycle | null;
    ceiling?: string | null;
    holder?: Principal | null;
    sprintItem?: { id: string; title: string } | null;
    stageLocked?: boolean;
    onComplete?: (ceiling: string, holder: Principal | null) => void;
  } = $props();

  let showing = $state<"ceiling" | "holder">("ceiling");
  let selectedCeiling = $state<string | null>(null);
  let picker = $state<ListboxPickerController>(null!);
  let ceilingOptions = $derived([
    { value: "none", name: "No further" },
    ...ceilingOptionsFor(lifecycle, newStage || lifecycle?.ceilingRange[0] || "needs_success_condition")
      .map((option) => ({ value: option.value, name: option.label }))
  ]);
  let holders = $derived(holderOptionsFor(sprintItem, holder).map((option) => ({
    value: option.value, name: option.label
  })));
  let rows = $derived((showing === "ceiling" ? ceilingOptions : holders) as readonly ListboxPickerItem[]);
  let selectedValue = $derived(showing === "ceiling" ? selectedCeiling : holderValue(holder));
  let ceilingName = $derived(ceilingOptions.find((option) => option.value === (ceiling ?? selectedCeiling))?.name ?? "Choose ceiling");
  let holderName = $derived(holders.find((option) => option.value === holderValue(holder))?.name ?? "Choose holder");

  $effect(() => {
    if (!lifecycle) return;
    if (selectedCeiling === null) selectedCeiling = ceiling ?? preferredScopeCeilingFor(lifecycle, newStage) ?? "none";
  });

  function reset(): void {
    selectedCeiling = ceiling ?? preferredScopeCeilingFor(lifecycle, newStage) ?? "none";
    showing = stageLocked ? "holder" : "ceiling";
  }

  function choose(value: string): void {
    if (showing === "ceiling") {
      selectedCeiling = value;
      showing = "holder";
      void tick().then(() => picker.setActiveValue(holderValue(holder)));
      return;
    }
    const nextCeiling = selectedCeiling ?? ceiling;
    if (nextCeiling === null) return;
    const nextHolder = holderFromValue(value, sprintItem);
    ceiling = nextCeiling;
    holder = nextHolder;
    onComplete?.(nextCeiling, nextHolder);
    picker.close(false);
  }

  function back(): void {
    if (stageLocked) return;
    showing = "ceiling";
    void tick().then(() => picker.setActiveValue(selectedCeiling));
  }
</script>

<ListboxPicker
  items={rows}
  {selectedValue}
  label={`Until ${ceilingName} · then ${holderName}`}
  listLabel={showing === "ceiling" ? "Ceiling stage" : "Who holds the ceiling"}
  kind="compact"
  attributes={{ "data-ceiling-picker": "" }}
  triggerAttributes={{ "data-scope-ceiling": "" }}
  panelAttributes={{ "data-ceiling-picker-panel": "" }}
  bind:controller={picker}
  onOpen={reset}
  onChoose={choose}
  optionAttributes={(choice) => ({
    "data-ceiling-picker-choice": choice.value,
    "data-scope-holder": showing === "holder" ? choice.value : undefined
  })}
>
  {#snippet triggerContent()}
    <span>Until {ceilingName} · then {holderName}</span>
  {/snippet}
  {#snippet beforeList()}
    {#if showing === "holder" && !stageLocked}
      <button type="button" class="ceiling-picker-back" data-ceiling-picker-back data-listbox-picker-action onmousedown={(event) => event.preventDefault()} onclick={back}>← Ceiling stage</button>
    {/if}
  {/snippet}
  {#snippet optionContent(choice, selected)}
    <span>{choice.name}</span>
  {/snippet}
</ListboxPicker>

<style>
  .ceiling-picker-back { width: 100%; border: 0; border-bottom: var(--border-hairline) solid var(--border-color); background: transparent; color: var(--text-muted); cursor: pointer; font: inherit; font-size: var(--type-xs); padding: var(--space-2) var(--space-3); text-align: left; }
  .ceiling-picker-back:hover { background-image: var(--interaction-hover); color: var(--text-strong); }
</style>
