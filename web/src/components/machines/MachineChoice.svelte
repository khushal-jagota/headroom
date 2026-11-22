<script lang="ts">
  import type { Machine } from "../../lib/machines/contracts";
  import MachineSymbol from "./MachineSymbol.svelte";
  let { machines, value, disabled = false, onchange }: {
    machines: readonly Machine[];
    value: string;
    disabled?: boolean;
    onchange: (machineId: string) => void;
  } = $props();
  const selected = $derived(machines.find(machine => machine.machine_id === value));
</script>
<label class="machine-choice" data-machine-color={selected?.color}>
  <span class="machine-choice-mark"><MachineSymbol icon={selected?.icon} /></span>
  <span>Machine</span>
  <select aria-label="Machine" {value} {disabled} onchange={event => onchange(event.currentTarget.value)}>
    {#each machines as machine (machine.machine_id)}
      <option value={machine.machine_id}>{machine.name}</option>
    {/each}
  </select>
</label>
<style>
  .machine-choice { display:inline-flex; align-items:center; gap:var(--space-2); min-width:0; color:var(--tone-primary); }
  .machine-choice-mark { display:flex; color:var(--machine-color, var(--tone-tertiary)); }
  select { max-width:100%; min-width:0; padding:var(--space-2) var(--space-3); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); background:var(--surface-input); color:var(--tone-primary); font:inherit; font-size:var(--type-small); }
  select:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--space-1); }
</style>
