<script lang="ts">
  import {
    ceilingOptionsFor,
    preferredScopeCeilingFor,
    type Lifecycle
  } from "../lib/lifecycle";
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
    sprintItem = null
  }: {
    newStage: string | null;
    lifecycle?: Lifecycle | null;
    ceiling?: string | null;
    holder?: Principal | null;
    sprintItem?: { id: string; title: string } | null;
  } = $props();

  let choice = $state("");

  let options = $derived([
    { value: "none", label: "No further" },
    ...ceilingOptionsFor(lifecycle, newStage || lifecycle?.ceilingRange[0] || "needs_success")
  ]);

  let whoOptions = $derived(holderOptionsFor(sprintItem, holder));
  let who = $derived(holderValue(holder));

  function chooseWho(value: string): void {
    holder = holderFromValue(value, sprintItem);
  }

  $effect(() => {
    if (!lifecycle) return;
    const nextDefault = preferredScopeCeilingFor(lifecycle, newStage) || "none";
    let next = ceiling === null ? nextDefault : choice || ceiling;
    if (!options.some((option) => option.value === next)) next = nextDefault;
    if (choice !== next) choice = next;
    if (ceiling !== next) ceiling = next;
  });
</script>

<div class="scope-picker">
  <span class="scope-word">Until</span>
  <label class="scope-select">
    <select data-scope-ceiling bind:value={choice} aria-label="Ceiling stage">
      <option value="" disabled hidden></option>
      {#each options as option}
        <option value={option.value}>{option.label}</option>
      {/each}
    </select>
  </label>
  <span class="scope-word" aria-hidden="true">·</span>
  <span class="scope-word">then</span>
  <label class="scope-select">
    <select
      data-scope-holder
      value={who}
      onchange={(event) => chooseWho(event.currentTarget.value)}
      aria-label="Who holds the ceiling"
    >
      {#each whoOptions as option}
        <option value={option.value}>{option.label}</option>
      {/each}
    </select>
  </label>
</div>
