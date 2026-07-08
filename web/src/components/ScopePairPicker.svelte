<script lang="ts">
  import { ceilingOptions } from "../lib/ui";

  type ScopePair = { next_ceiling: string; at_cap: string };

  let {
    newState,
    scope = $bindable<ScopePair | null>(null)
  }: { newState: string | null; scope?: ScopePair | null } = $props();

  let ceiling = $state("");
  let atCap = $state("");
  let groupName = `scope-${Math.random().toString(36).slice(2)}`;

  let options = $derived([
    { value: "none", label: "No further" },
    ...ceilingOptions(newState || "needs_success")
  ]);

  $effect(() => {
    scope = ceiling && atCap ? { next_ceiling: ceiling, at_cap: atCap } : null;
  });
</script>

<div class="scope-picker">
  <label class="scope-label">
    <span>how far</span>
    <select class="scope-ceiling" data-scope-ceiling bind:value={ceiling}>
      <option value="" disabled hidden></option>
      {#each options as option}
        <option value={option.value}>{option.label}</option>
      {/each}
    </select>
  </label>
  <span class="scope-atcap" data-scope-atcap>
    <label><input type="radio" name={groupName} value="stop" bind:group={atCap} /> <span>Stop</span></label>
    <label><input type="radio" name={groupName} value="propose" bind:group={atCap} /> <span>Propose</span></label>
  </span>
</div>
