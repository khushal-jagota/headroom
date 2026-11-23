<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import type { WorkMoveOwner, WorkMoveResult } from "../../../../server/work_moves/contracts";
  import { queries } from "../../lib/queryCatalogue";
  import { submitWorkMove, workMoveResultText } from "../../lib/machines/workMoves";
  import MachineChoice from "./MachineChoice.svelte";
  import Button from "../Button.svelte";
  import ErrorLine from "../ErrorLine.svelte";

  let { owner, label, currentMachineId, targetMachineId, everything = false, triggerLabel = "Move machine" }: {
    owner?: WorkMoveOwner;
    label: string;
    currentMachineId?: string;
    targetMachineId?: string;
    everything?: boolean;
    triggerLabel?: string;
  } = $props();
  const machines = createQuery(() => queries.machines());
  let dialog = $state<HTMLDialogElement | null>(null);
  let destination = $state("");
  let sending = $state(false);
  let error = $state<unknown>(null);
  let results = $state<readonly WorkMoveResult[]>([]);
  const id = $props.id();
  const choices = $derived((machines.data?.machines ?? []).filter(machine => machine.machine_id !== currentMachineId));
  const target = $derived(machines.data?.machines.find(machine => machine.machine_id === destination));

  function open() {
    destination = targetMachineId ?? choices.find(machine => machine.available)?.machine_id ?? choices[0]?.machine_id ?? "";
    error = null; results = [];
    dialog?.showModal();
  }
  async function move() {
    if (!destination || (!everything && !owner)) return;
    sending = true; error = null; results = [];
    try {
      results = await submitWorkMove(everything ? null : owner!, destination);
      if (results.every(result => result.error === null)) dialog?.close();
    } catch (cause) { error = cause; }
    finally { sending = false; }
  }
  function cancel(event: Event) { if (sending) event.preventDefault(); }
</script>

<Button variant="ghost" onclick={open} data-work-move={everything ? "everything" : owner?.kind}>{triggerLabel}</Button>
<dialog bind:this={dialog} aria-labelledby={id} oncancel={cancel} data-work-move-dialog>
  <form onsubmit={event => { event.preventDefault(); void move(); }}>
    <h2 id={id}>{everything ? "Move everything" : `Move ${label}`}{targetMachineId ? ` to ${target?.name ?? "this machine"}?` : "?"}</h2>
    <p>{everything ? "Unfinished Tickets, active Managers and the Chief start new conversations from their records." : "Starts a new conversation from its record."} Push work before the move.</p>
    {#if choices.length || targetMachineId}
      <MachineChoice machines={targetMachineId ? machines.data?.machines ?? [] : choices} value={destination} disabled={sending || Boolean(targetMachineId)} onchange={value => destination = value} />
    {:else if machines.isPending}<p role="status">Loading machines…</p>
    {:else}<p>No other machine is registered.</p>{/if}
    {#if machines.error}<ErrorLine error={machines.error} />{/if}
    {#if error}<ErrorLine {error} />{/if}
    {#if results.some(result => result.error !== null)}
      <ul class="results" aria-label="Move results" aria-live="polite">
        {#each results as result}
          <li class:refused={result.error !== null}>{result.title}: {workMoveResultText(result)}</li>
        {/each}
      </ul>
    {/if}
    <footer>
      <Button variant="ghost" disabled={sending} onclick={() => dialog?.close()}>Not now</Button>
      <Button type="submit" variant="primary" disabled={sending || !destination || machines.isPending}>{sending ? "Moving…" : everything ? "Move everything" : "Move"}</Button>
    </footer>
  </form>
</dialog>

<style>
  dialog { max-width:calc(100% - var(--space-5)); margin:auto; padding:0; border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-lg); background:var(--surface-2); color:var(--tone-primary); box-shadow:var(--shadow-float); }
  dialog::backdrop { background:var(--scrim); }
  form { padding:var(--space-5); display:flex; flex-direction:column; gap:var(--space-4); max-width:var(--page-prose-measure); }
  h2 { font-size:var(--type-page-section); font-weight:var(--weight-title); color:var(--tone-strong); margin:0; overflow-wrap:anywhere; }
  p { color:var(--tone-secondary); line-height:1.55; margin:0; }
  .results { margin:0; padding-left:var(--space-4); font-size:var(--type-row); }
  .refused { color:var(--accent-error); }
  footer { display:flex; justify-content:flex-end; flex-wrap:wrap; gap:var(--space-2); }
</style>
