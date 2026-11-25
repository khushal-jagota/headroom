<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import type { WorkMoveOwner, WorkMoveOperation } from "../../../../server/work_moves/contracts";
  import { queries } from "../../lib/queryCatalogue";
  import { latestWorkMove, sameWorkMoveOwner, submitWorkMove, workMoveDuration, workMoveNextAction, workMoveItemPhaseText, workMoveItemWaitText, workMovePhaseText, workMoveResultText } from "../../lib/machines/workMoves";
  import { WORK_MOVE_POLL_MS } from "../../lib/machines/config";
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
  const moves = createQuery(() => queries.workMoveOperations());
  let dialog = $state<HTMLDialogElement | null>(null);
  let dialogOpen = $state(false);
  let destination = $state("");
  let sending = $state(false);
  let error = $state<unknown>(null);
  let accepted = $state<WorkMoveOperation | null>(null);
  let now = $state(Date.now());
  const id = $props.id();
  const choices = $derived((machines.data?.machines ?? []).filter(machine => machine.machine_id !== currentMachineId));
  const operation = $derived(latestWorkMove([
    ...(moves.data?.operations ?? []), ...(accepted ? [accepted] : [])
  ], everything ? null : owner ?? null, targetMachineId));
  const items = $derived(operation?.items.filter(item => everything || (owner && sameWorkMoveOwner(item.owner, owner))) ?? []);
  const active = $derived(Boolean(operation && operation.finished_at_ms === null && items.some(item => item.result === null)));
  const selectedDestination = $derived(active && operation ? operation.machine_id : destination);
  const target = $derived(machines.data?.machines.find(machine => machine.machine_id === selectedDestination));
  const progressTarget = $derived(machines.data?.machines.find(machine => machine.machine_id === operation?.machine_id)?.name ?? "the destination machine");
  const completed = $derived(items.filter(item => item.result !== null).length);

  $effect(() => {
    if (!dialogOpen || !active) return;
    now = Date.now();
    const timer = setInterval(() => { now = Date.now(); }, WORK_MOVE_POLL_MS);
    return () => clearInterval(timer);
  });

  function open() {
    destination = targetMachineId ?? choices.find(machine => machine.available)?.machine_id ?? choices[0]?.machine_id ?? "";
    error = null;
    now = Date.now();
    dialogOpen = true;
    dialog?.showModal();
    void moves.refetch();
  }
  async function move() {
    if (!destination || (!everything && !owner) || active || sending) return;
    sending = true; error = null;
    try {
      accepted = await submitWorkMove(everything ? null : owner!, destination);
      now = Date.now();
    } catch (cause) { error = cause; }
    finally { sending = false; }
  }
</script>

<Button variant="ghost" onclick={open} data-work-move={everything ? "everything" : owner?.kind}>{active ? "Move progress" : triggerLabel}</Button>
<dialog bind:this={dialog} aria-labelledby={id} onclose={() => { dialogOpen = false; }} data-work-move-dialog>
  <form onsubmit={event => { event.preventDefault(); void move(); }}>
    <h2 id={id}>{everything ? "Move everything" : `Move ${label}`}{targetMachineId ? ` to ${target?.name ?? "this machine"}?` : ""}</h2>
    {#if !active}<p>{everything ? "Unfinished Tickets, active Managers and the Chief start new conversations from their records." : "Starts a new conversation from its record."} Push work before the move.</p>{/if}
    {#if choices.length || targetMachineId || active}
      <MachineChoice machines={targetMachineId || active ? machines.data?.machines ?? [] : choices} value={selectedDestination} disabled={sending || active || Boolean(targetMachineId)} onchange={value => destination = value} />
    {:else if machines.isPending}<p role="status">Loading machines…</p>
    {:else}<p>No other machine is registered.</p>{/if}
    {#if machines.error}<ErrorLine error={machines.error} />{/if}
    {#if moves.error}<ErrorLine error={moves.error} /><p>Progress is unavailable. Reopen this dialog to check it again.</p>{/if}
    {#if error}<ErrorLine {error} />{/if}
    {#if sending}<p role="status">Wait for acceptance… You can close this dialog.</p>{/if}
    {#if operation}
      <section class="progress" aria-label="Move progress" data-work-move-progress>
        <div class="progress-heading">
          <strong>{active ? "Move in progress" : items.some(item => item.result?.error) ? "Move needs attention" : "Move complete"}</strong>
          <span class="duration">{workMoveDuration(operation.started_at_ms, operation.finished_at_ms, now)}</span>
        </div>
        <p class="destination">To {progressTarget}{everything ? ` · ${completed} of ${items.length} complete` : ""}</p>
        {#if everything}<progress max={Math.max(1, items.length)} value={completed} aria-label="Completed work moves"></progress>{/if}
        <ul class="results" aria-label="Work moves">
          {#each items as item}
            <li class:refused={item.result?.error !== null && item.result !== null} data-work-move-item>
              {#if everything}<strong class="item-title">{item.title}</strong>{/if}
              <div class="phase" role="status">{item.result ? workMoveResultText(item.result) : workMoveItemPhaseText(item)}</div>
              {#if !item.result}<p class="wait">{workMoveItemWaitText(item)}</p>{/if}
              {#if item.result?.error}<p class="next-action">{workMoveNextAction(item)}</p>{/if}
              {#if item.phases.length}
                <details>
                  <summary>Phase times</summary>
                  <dl class="phase-times">
                    {#each item.phases.filter(phase => phase.phase !== "complete" && phase.phase !== "failed") as phase}
                      <div class:current={phase.finished_at_ms === null && !item.result}>
                        <dt>{workMovePhaseText[phase.phase]}</dt>
                        <dd>{workMoveDuration(phase.started_at_ms, phase.finished_at_ms, now)}{phase.finished_at_ms === null && !item.result ? " · active" : ""}</dd>
                      </div>
                    {/each}
                  </dl>
                </details>
              {/if}
            </li>
          {/each}
        </ul>
        {#if active}<p class="leave-note">You can close this dialog and come back. The move continues.</p>{/if}
      </section>
    {/if}
    <footer>
      <Button variant="ghost" onclick={() => dialog?.close()}>Close</Button>
      <Button type="submit" variant="primary" disabled={sending || active || !destination || machines.isPending || moves.isPending}>{sending ? "Accepting…" : active ? "Move in progress" : everything ? "Move everything" : "Move"}</Button>
    </footer>
  </form>
</dialog>

<style>
  dialog { max-width:calc(100% - var(--space-5)); max-height:calc(100% - var(--space-5)); overflow:auto; margin:auto; padding:0; border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-lg); background:var(--surface-2); color:var(--tone-primary); box-shadow:var(--shadow-float); }
  dialog::backdrop { background:var(--scrim); }
  form { padding:var(--space-5); display:flex; flex-direction:column; gap:var(--space-4); width:calc(100vw - var(--space-5) * 3); max-width:var(--page-prose-measure); }
  h2 { font-size:var(--type-page-section); font-weight:var(--weight-title); color:var(--tone-strong); margin:0; overflow-wrap:anywhere; }
  p { color:var(--tone-secondary); line-height:1.55; margin:0; }
  .progress { display:flex; flex-direction:column; gap:var(--space-2); border-top:var(--border-hairline) solid var(--border-color); padding-top:var(--space-4); }
  .progress-heading { display:flex; justify-content:space-between; align-items:baseline; gap:var(--space-3); }
  .duration, .phase-times dd { font-variant-numeric:tabular-nums; white-space:nowrap; color:var(--tone-secondary); }
  .destination, .wait, .next-action, .leave-note, details { font-size:var(--type-row); }
  .results { display:flex; flex-direction:column; gap:var(--space-3); list-style:none; margin:0; padding:0; }
  .results li { border-top:var(--border-hairline) solid var(--border-color); padding-top:var(--space-3); }
  .item-title { display:block; margin-bottom:var(--space-1); overflow-wrap:anywhere; }
  .phase { color:var(--tone-strong); }
  .refused .phase { color:var(--accent-error); }
  .wait, .next-action { margin-top:var(--space-1); }
  details { margin-top:var(--space-2); color:var(--tone-secondary); }
  summary { cursor:pointer; }
  .phase-times { margin:var(--space-2) 0 0; }
  .phase-times > div { display:flex; justify-content:space-between; gap:var(--space-3); padding:var(--space-1) 0; }
  .phase-times dd { margin:0; }
  .current dt { color:var(--tone-strong); }
  progress { width:100%; height:var(--space-1); accent-color:var(--tone-strong); }
  .leave-note { margin-top:var(--space-2); }
  footer { display:flex; justify-content:flex-end; flex-wrap:wrap; gap:var(--space-2); }
</style>
