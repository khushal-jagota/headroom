<script lang="ts">
  import {
    ceilingOptionsFor,
    preferredScopeCeilingFor,
    type Lifecycle
  } from "../lib/lifecycle";
  import type { AtCap } from "../lib/types";

  type ScopePair = { next_ceiling: string; at_cap: AtCap };

  let {
    newStage,
    lifecycle = null,
    scope = $bindable<ScopePair | null>(null)
  }: {
    newStage: string | null;
    lifecycle?: Lifecycle | null;
    scope?: ScopePair | null;
  } = $props();

  let ceiling = $state("");
  let atCap = $state<AtCap | "">("");

  let options = $derived([
    { value: "none", label: "No further" },
    ...ceilingOptionsFor(lifecycle, newStage || lifecycle?.ceilingRange[0] || "needs_success")
  ]);

  $effect(() => {
    if (!lifecycle) return;
    const externalScope = scope;
    const nextDefault = preferredScopeCeilingFor(lifecycle, newStage) || "none";
    let nextCeiling = externalScope === null ? nextDefault : ceiling;
    let nextAtCap: AtCap | "" = externalScope === null ? "propose" : atCap;
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
    const nextScope: ScopePair | null = nextCeiling && nextAtCap
      ? { next_ceiling: nextCeiling, at_cap: nextAtCap }
      : null;
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
        <option value="stop">Stop</option>
        <option value="propose">Propose</option>
      </select>
    </label>
  </span>
</div>
