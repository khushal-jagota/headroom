<script lang="ts">
  import { tick } from "svelte";
  import ListboxPicker, {
    type ListboxPickerController,
    type ListboxPickerItem
  } from "./conversation/ListboxPicker.svelte";
  import PickerRail from "./conversation/PickerRail.svelte";
  import PickerRailRow from "./conversation/PickerRailRow.svelte";
  import { ceilingOptionsFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import { holderOptionsFor, holderValue, holderFromValue } from "../lib/ceilingHolder";
  import type { Principal } from "../lib/types";

  // The whole ceiling in one control: how far the Ticket may go, and who is asked when
  // it gets there. It reads "Until Approach · then me", and the approve row is where a
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
    sprintItem = null,
    stageLocked = false,
    below = false,
    onComplete
  }: {
    newStage: string | null;
    lifecycle?: Lifecycle | null;
    ceiling?: string | null;
    holder?: Principal | null;
    sprintItem?: { id: string; title: string } | null;
    stageLocked?: boolean;
    /** Open the panel downward, for a host that sits under the page header. */
    below?: boolean;
    /** Only the halves the user set. A reviewer chosen on its own carries no ceiling. */
    onComplete?: (change: { ceiling?: string; holder: Principal | null }) => void;
  } = $props();

  let showing = $state<"ceiling" | "holder">("ceiling");
  let selectedCeiling = $state<string | null>(null);
  let ceilingTouched = $state(false);
  let holderTouched = $state(false);
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
  // What the control reports right now: the host's value until the user picks another.
  let shownCeiling = $derived(ceilingTouched ? selectedCeiling : ceiling);
  let ceilingName = $derived(ceilingOptions.find((option) => option.value === shownCeiling)?.name ?? null);
  let holderName = $derived(holders.find((option) => option.value === holderValue(holder))?.name ?? "Choose holder");

  $effect(() => {
    if (!lifecycle) return;
    if (selectedCeiling === null) selectedCeiling = ceiling ?? preferredScopeCeilingFor(lifecycle, newStage) ?? "none";
  });

  function reset(): void {
    selectedCeiling = ceiling ?? preferredScopeCeilingFor(lifecycle, newStage) ?? "none";
    ceilingTouched = false;
    holderTouched = false;
    showing = stageLocked ? "holder" : "ceiling";
  }

  function show(part: "ceiling" | "holder"): void {
    if (part === "ceiling" && stageLocked) return;
    showing = part;
    void tick().then(() => picker.setActiveValue(part === "ceiling" ? selectedCeiling : holderValue(holder)));
  }

  // One save, carrying only what the user answered in this visit to the panel.
  function commit(): void {
    if (ceilingTouched && selectedCeiling !== null) ceiling = selectedCeiling;
    onComplete?.({
      ...(ceilingTouched && selectedCeiling !== null ? { ceiling: selectedCeiling } : {}),
      holder
    });
    picker.close(false);
  }

  function choose(value: string): void {
    if (showing === "ceiling") {
      selectedCeiling = value;
      ceilingTouched = true;
      if (holderTouched) return commit();
      return show("holder");
    }
    holder = holderFromValue(value, sprintItem);
    holderTouched = true;
    // The approve row starts with no ceiling at all and cannot approve without one, so
    // the unanswered half is asked for rather than guessed.
    if (!ceilingTouched && ceiling === null) return show("ceiling");
    commit();
  }
</script>

<ListboxPicker
  items={rows}
  {selectedValue}
  label={`Until ${ceilingName ?? "Choose ceiling"} · then ${holderName}`}
  listLabel={showing === "ceiling" ? "Ceiling stage" : "Who holds the ceiling"}
  kind="rail"
  {below}
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
    <span>Until {ceilingName ?? "Choose ceiling"} · then {holderName}</span>
  {/snippet}
  {#snippet beforeList()}
    <PickerRail label="Parts of the ceiling" attributes={{ "data-ceiling-rail": "" }}>
      <!-- The rail names the halves. What each one is set to is in the trigger right
           above the panel, and on the ticked row in the list. -->
      <PickerRailRow
        on={showing === "ceiling"}
        dim={stageLocked}
        disabled={stageLocked}
        label="How far"
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
        attributes={{
          "data-ceiling-part": "holder",
          "aria-pressed": showing === "holder" ? "true" : "false"
        }}
        onclick={() => show("holder")}
      />
    </PickerRail>
  {/snippet}
  {#snippet optionContent(choice, selected)}
    <span>{choice.name}</span>
  {/snippet}
  {#snippet afterList()}
    {#if stageLocked}
      <div class="ceiling-picker-foot" data-ceiling-picker-locked>A parked proposal holds the stage.</div>
    {/if}
  {/snippet}
</ListboxPicker>

<style>
  .ceiling-picker-foot {
    grid-column: 1 / -1; border-top: var(--border-hairline) solid var(--border-color);
    background: var(--surface-recessed); color: var(--text-faint); font-size: var(--type-xs);
    line-height: 1.45; padding: var(--space-2) var(--space-3);
  }
</style>
