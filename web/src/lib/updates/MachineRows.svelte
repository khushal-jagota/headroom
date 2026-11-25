<script lang="ts">
  import type { MachineUpdateState } from "./contracts";
  import { machineUpdateLabel } from "./logic/presentation";

  let { machines, retry }: { machines: readonly MachineUpdateState[]; retry?: (machine_id: string) => void } = $props();
</script>

<ul class="machines" aria-label="Machine updates">
  {#each machines as machine (machine.machine_id)}
    <li data-machine-update={machine.machine_id} data-state={machine.state}>
      <span class="name">{machine.name}</span>
      <span class="state">{machineUpdateLabel(machine)}</span>
      {#if retry && machine.can_retry && machine.attempt_id}
        <button type="button" aria-label={`Try again for ${machine.name}`} onclick={() => retry?.(machine.machine_id)}>Try again</button>
      {/if}
      {#if machine.detail}<span class="detail">{machine.detail}</span>{/if}
    </li>
  {/each}
</ul>

<style>
  .machines { display: flex; flex-direction: column; gap: var(--space-2); margin: 0; padding: 0; list-style: none; }
  li { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-1) var(--space-2); }
  .name { flex: 1 1 auto; overflow-wrap: anywhere; }
  .detail { flex-basis: 100%; overflow-wrap: anywhere; color: var(--text-muted); }
  .state { color: var(--text-muted); }
  li[data-state="failed"] .state, li[data-state="unavailable"] .state { color: var(--accent-error); }
  button { padding: 0; border: 0; background: transparent; color: inherit; font: inherit; font-weight: var(--weight-small); cursor: pointer; }
  button:hover { text-decoration: underline; }
  button:focus-visible { outline: var(--focus-width) solid var(--focus-ring); outline-offset: 2px; }
</style>
