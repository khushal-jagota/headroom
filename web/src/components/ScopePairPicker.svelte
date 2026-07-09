<script lang="ts">
  import { ceilingOptions } from "../lib/ui";

  type ScopePair = { next_ceiling: string; at_cap: string };

  let {
    newState,
    scope = $bindable<ScopePair | null>(null)
  }: { newState: string | null; scope?: ScopePair | null } = $props();

  let ceiling = $state("");
  let atCap = $state("");

  let options = $derived([
    { value: "none", label: "No further" },
    ...ceilingOptions(newState || "needs_success")
  ]);

  $effect(() => {
    const externalScope = scope;
    const nextDefault = newState || options[1]?.value || "none";
    let nextCeiling = externalScope === null ? nextDefault : ceiling;
    let nextAtCap = externalScope === null ? "propose" : atCap;
    if (externalScope && !nextCeiling) nextCeiling = externalScope.next_ceiling;
    if (externalScope && !nextAtCap) nextAtCap = externalScope.at_cap;
    if (!options.some((option) => option.value === nextCeiling)) {
      nextCeiling = nextDefault;
      ceiling = nextCeiling;
    } else if (ceiling !== nextCeiling) {
      ceiling = nextCeiling;
    }
    if (nextAtCap !== "stop" && nextAtCap !== "propose") {
      nextAtCap = "stop";
      atCap = nextAtCap;
    } else if (atCap !== nextAtCap) {
      atCap = nextAtCap;
    }
    const nextScope = nextCeiling && nextAtCap ? { next_ceiling: nextCeiling, at_cap: nextAtCap } : null;
    if (scope?.next_ceiling !== nextScope?.next_ceiling || scope?.at_cap !== nextScope?.at_cap) {
      scope = nextScope;
    }
  });
</script>

<div class="scope-picker">
  <span class="scope-word">until</span>
  <label class="scope-select">
    <select class="scope-ceiling" data-scope-ceiling bind:value={ceiling} aria-label="Approve until stage">
      <option value="" disabled hidden></option>
      {#each options as option}
        <option value={option.value}>{option.label}</option>
      {/each}
    </select>
  </label>
  <span class="scope-word">then</span>
  <span class="scope-atcap" data-scope-atcap>
    <label class="scope-select">
      <select bind:value={atCap} aria-label="At cap behavior">
        <option value="stop">stop</option>
        <option value="propose">propose</option>
      </select>
    </label>
  </span>
</div>
