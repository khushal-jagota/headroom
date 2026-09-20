<script lang="ts">
  import {
    ceilingOptionsFor,
    preferredScopeCeilingFor,
    type Lifecycle
  } from "../lib/lifecycle";

  let {
    newStage,
    lifecycle = null,
    ceiling = $bindable<string | null>(null)
  }: {
    newStage: string | null;
    lifecycle?: Lifecycle | null;
    ceiling?: string | null;
  } = $props();

  let choice = $state("");

  let options = $derived([
    { value: "none", label: "No further" },
    ...ceilingOptionsFor(lifecycle, newStage || lifecycle?.ceilingRange[0] || "needs_success")
  ]);

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
  <span class="scope-word">until</span>
  <label class="scope-select">
    <select data-scope-ceiling bind:value={choice} aria-label="Approve until stage">
      <option value="" disabled hidden></option>
      {#each options as option}
        <option value={option.value}>{option.label}</option>
      {/each}
    </select>
  </label>
</div>
