<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import type { WorkMoveOwner, WorkMoveOperation } from "../../../../server/work_moves/contracts";
  import { queries } from "../../lib/queryCatalogue";
  import {
    latestWorkMove, sameWorkMoveOwner, submitWorkMove, workMoveDuration, workMoveFinishedTitle, workMoveIdleTitle,
    workMoveItemMs, workMoveNextStep, workMoveRowOfPhase, workMoveRows, workMoveSummary, workMoveWaiting, WORK_MOVE_ROWS
  } from "../../lib/machines/workMoves";
  import { PAGE_STARTED_AT_MS, seenWorkMoves, workMoveSources } from "../../lib/machines/workMoveSeen.svelte";
  import { WORK_MOVE_SETTLE_MS, WORK_MOVE_TICK_MS } from "../../lib/machines/config";
  import MachineSymbol from "./MachineSymbol.svelte";
  import Button from "../Button.svelte";
  import ErrorLine from "../ErrorLine.svelte";

  let { owner, currentMachineId, targetMachineId, everything = false, triggerLabel = "Move machine" }: {
    owner?: WorkMoveOwner;
    currentMachineId?: string;
    targetMachineId?: string;
    everything?: boolean;
    triggerLabel?: string;
  } = $props();
  const machines = createQuery(() => queries.machines());
  const moves = createQuery(() => queries.workMoveOperations());
  let dialog = $state<HTMLDialogElement | null>(null);
  let list = $state<HTMLElement | null>(null);
  let dialogOpen = $state(false);
  let destination = $state("");
  let sending = $state(false);
  let error = $state<unknown>(null);
  let accepted = $state<WorkMoveOperation | null>(null);
  let now = $state(Date.now());
  let focusMove = $state(false);
  const id = $props.id();

  const allMachines = $derived(machines.data?.machines ?? []);
  const choices = $derived(allMachines.filter(machine => machine.machine_id !== currentMachineId));
  const operation = $derived(latestWorkMove([
    ...(moves.data?.operations ?? []), ...(accepted ? [accepted] : [])
  ], everything ? null : owner ?? null, targetMachineId));
  const items = $derived(operation?.items.filter(item => everything || (owner && sameWorkMoveOwner(item.owner, owner))) ?? []);
  const active = $derived(Boolean(operation && operation.finished_at_ms === null && items.some(item => item.result === null)));
  const finishedAt = $derived(operation?.finished_at_ms ?? now);
  const finished = $derived(Boolean(operation) && !active && items.length > 0);
  const unseen = $derived(finished && operation !== null && !seenWorkMoves.has(operation.operation_id) && finishedAt >= PAGE_STARTED_AT_MS);
  const summary = $derived(workMoveSummary(items));
  const settling = $derived(now - finishedAt < WORK_MOVE_SETTLE_MS);
  // A finished move shows until it is seen: a problem until the dialog closes, a success for a moment.
  const shown = $derived(active || (unseen && (!summary.ok || dialogOpen || settling)));
  const busy = $derived(active || sending);
  const single = $derived(everything ? null : items[0] ?? null);
  const destinationId = $derived(shown && operation ? operation.machine_id : destination);
  const target = $derived(allMachines.find(machine => machine.machine_id === destinationId));
  const progressTarget = $derived(target?.name ?? "the destination machine");
  const sourceId = $derived(everything ? undefined : shown && operation
    ? workMoveSources.get(operation.operation_id) ?? (operation.machine_id === currentMachineId ? undefined : currentMachineId)
    : currentMachineId);
  const source = $derived(allMachines.find(machine => machine.machine_id === sourceId));
  const locked = $derived(shown || sending || everything || Boolean(targetMachineId));
  const arrived = $derived(!everything && shown && finished && Boolean(single?.result?.moved) && !single?.result?.error);
  const completed = $derived(items.filter(item => item.result !== null).length);
  const activeIndex = $derived(items.findIndex(item => item.result === null));
  const failedIndex = $derived(items.findIndex(item => item.result?.error));
  const rows = $derived(single
    ? workMoveRows(single, now, operation ? workMoveWaiting(operation, single) : false)
    : WORK_MOVE_ROWS.map((_, index) => ({index, state: "pending" as const, ms: null, slow: false})));
  const totalMs = $derived(!operation ? 0 : everything
    ? Math.max(0, (operation.finished_at_ms ?? now) - operation.started_at_ms)
    : single ? workMoveItemMs(single, now) : 0);
  const title = $derived(!shown ? workMoveIdleTitle(everything ? null : owner?.kind ?? null)
    : active ? (everything ? `Moving everything to ${progressTarget}` : `Moving to ${progressTarget}`)
    : workMoveFinishedTitle(items, everything, progressTarget));
  const activeItem = $derived(activeIndex >= 0 ? items[activeIndex]! : null);
  const announcement = $derived(!shown ? "" : active
    ? `${everything && activeItem ? `${activeItem.title}: ` : ""}${currentRowLabel(activeItem ?? single)}`
    : title);
  const retryable = $derived(Boolean(single?.result?.error && !single.result.moved));

  function rowLabel(index: number | null): string {
    return index === null ? "Starting" : WORK_MOVE_ROWS[index]!.label(progressTarget);
  }
  function currentRowLabel(item: typeof items[number] | null): string {
    return item ? rowLabel(workMoveRowOfPhase(item.phase, item.conversation_id)) : "";
  }
  function rowMachine(side: "source" | "destination"): string | null {
    return side === "destination" ? progressTarget : source?.name ?? null;
  }

  // The clock ticks while a move runs or a success is settling on the page button.
  $effect(() => {
    if (!active && !(unseen && summary.ok && !dialogOpen && settling)) return;
    now = Date.now();
    const timer = setInterval(() => { now = Date.now(); }, WORK_MOVE_TICK_MS);
    return () => clearInterval(timer);
  });
  // A success that settled while the dialog was closed has been seen, so the dialog opens idle.
  $effect(() => {
    if (operation && unseen && summary.ok && !dialogOpen && !settling) seenWorkMoves.add(operation.operation_id);
  });
  // Move everything scrolls to the running row, then to the first failure.
  $effect(() => {
    const index = finished ? failedIndex : activeIndex;
    if (!dialogOpen || !shown || !list || index < 0) return;
    const row = list.querySelector<HTMLElement>(`[data-row="${index}"]`);
    if (!row) return;
    if (row.offsetTop < list.scrollTop) list.scrollTop = row.offsetTop;
    else if (row.offsetTop + row.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = row.offsetTop + row.offsetHeight - list.clientHeight;
  });

  // Move takes focus when the dialog opens, as soon as it can (the machines may still be loading).
  $effect(() => {
    if (!focusMove || !dialogOpen || shown || busy || !destination || machines.isPending || moves.isPending) return;
    focusMove = false;
    dialog?.querySelector<HTMLElement>("[data-move-submit]")?.focus();
  });

  function open() {
    destination = targetMachineId ?? choices.find(machine => machine.available)?.machine_id ?? choices[0]?.machine_id ?? "";
    error = null;
    now = Date.now();
    dialogOpen = true;
    focusMove = true;
    dialog?.showModal();
    void moves.refetch();
  }
  function closed() {
    dialogOpen = false;
    if (operation && finished) seenWorkMoves.add(operation.operation_id);
  }
  async function move(machineId = destination) {
    if (!machineId || (!everything && !owner) || busy) return;
    sending = true; error = null;
    try {
      const next = await submitWorkMove(everything ? null : owner!, machineId);
      if (sourceId) workMoveSources.set(next.operation_id, sourceId);
      accepted = next;
      now = Date.now();
    } catch (cause) { error = cause; }
    finally { sending = false; }
  }
</script>

<Button variant="ghost" onclick={open} data-work-move={everything ? "everything" : owner?.kind}>
  {#if active}
    <span class="composer-field-spinner" role="img" aria-label="Moving"></span>Moving · <span class="num">{workMoveDuration(totalMs)}</span>
  {:else if shown}
    <span class="mark sm" class:ok={summary.ok} class:bad={!summary.ok} aria-hidden="true">
      <svg viewBox="0 0 24 24">{#if summary.ok}<path d="M5 12.5l4.5 4.5L19 7.5" />{:else}<path d="M6 6l12 12M18 6L6 18" />{/if}</svg>
    </span>{summary.ok ? title : "Move needs attention"}
  {:else}{triggerLabel}{/if}
</Button>
<dialog bind:this={dialog} aria-labelledby={id} onclose={closed} data-work-move-dialog>
  <form onsubmit={event => { event.preventDefault(); void move(); }}>
    <div class="head">
      <h2 id={id}>{title}</h2>
      <span class="head-end">
        {#if everything && active}<span class="count num">{Math.min(completed + 1, items.length)} of {items.length}</span>{/if}
        {#if shown}<span class="timer num">{workMoveDuration(totalMs)}</span>{/if}
        {#if active}<span class="mark-slot"><span class="composer-field-spinner" role="img" aria-label="Working"></span></span>
        {:else if shown}
          <span class="mark-slot"><span class="mark lg" class:ok={summary.ok} class:bad={!summary.ok} aria-hidden="true">
            <svg viewBox="0 0 24 24">{#if summary.ok}<path d="M5 12.5l4.5 4.5L19 7.5" />{:else}<path d="M6 6l12 12M18 6L6 18" />{/if}</svg>
          </span></span>
        {/if}
      </span>
    </div>

    <div class="pair">
      <div class="col">
        <span class="lbl">From</span>
        <div class="field" class:left={arrived} data-machine-color={everything ? "stone" : source?.color}>
          <span class="m"><MachineSymbol icon={source?.icon} /></span>
          <span class="nm">{everything ? "Other machines" : source?.name ?? "Another machine"}</span>
        </div>
      </div>
      <div class="arrow" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M5 12h14m-5-5l5 5-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg></div>
      <div class="col">
        <span class="lbl">To</span>
        <label class="field pick" class:locked class:arrived data-machine-color={target?.color}>
          <span class="m"><MachineSymbol icon={target?.icon} /></span>
          <span class="nm">{target?.name ?? (machines.isPending ? "Loading…" : "No other machine")}</span>
          {#if arrived}<span class="arrive" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg></span>{/if}
          {#if !locked}<svg class="chev" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>{/if}
          <select aria-label="Destination machine" value={destinationId} disabled={locked || !choices.length} onchange={event => destination = event.currentTarget.value}>
            {#each locked ? allMachines : choices as machine (machine.machine_id)}
              <option value={machine.machine_id}>{machine.name}{machine.available ? "" : " · offline"}</option>
            {/each}
          </select>
        </label>
      </div>
    </div>

    {#if everything}
      <ol class="steps scroll" bind:this={list} aria-label="Work moves" data-work-move-progress>
        {#each items as item, index (item.owner.kind === "chief" ? "chief" : `${item.owner.kind}:${item.owner.id}`)}
          {@const isActive = shown && active && index === activeIndex}
          {@const row = isActive ? workMoveRows(item, now)[workMoveRowOfPhase(item.phase, item.conversation_id) ?? 0] : null}
          {@const state = !shown ? "pending" : item.result ? (item.result.error ? "failed" : "done") : isActive ? "active" : "pending"}
          <li class="step {state}" data-row={index} data-work-move-item>
            <span class="lead">
              {#if state === "active"}<span class="composer-field-spinner" role="img" aria-label="Working"></span>
              {:else if state === "done"}<svg class="tick" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
              {:else if state === "failed"}<svg class="cross" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
              {:else}<span class="ring"></span>{/if}
            </span>
            <span class="what">{item.title}{#if isActive}<span class="sub">{currentRowLabel(item)}</span>{/if}</span>
            <span class="time">
              {#if row?.slow}<span class="slow">longer than usual</span>{/if}
              <span class="num">{shown && (item.result || isActive) ? workMoveDuration(workMoveItemMs(item, now)) : ""}</span>
            </span>
            {#if item.result?.error}<span class="why">{item.result.moved ? "Moved, but not started. " : "Not moved. "}{item.result.error}</span>{/if}
          </li>
        {/each}
      </ol>
    {:else}
      <ol class="steps" aria-label="Move progress" data-work-move-progress>
        {#each rows as row (row.index)}
          {@const def = WORK_MOVE_ROWS[row.index]!}
          {@const machine = rowMachine(def.side)}
          <li class="step {row.state}" data-work-move-item>
            <span class="lead">
              {#if row.state === "active"}<span class="composer-field-spinner" role="img" aria-label="Working"></span>
              {:else if row.state === "done"}<svg class="tick" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
              {:else if row.state === "failed"}<svg class="cross" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
              {:else if row.state === "skipped"}<span class="dash"></span>
              {:else}<span class="ring"></span>{/if}
            </span>
            <span class="what">{def.label(progressTarget)}{#if machine && def.side === "source"}<span class="sub">{machine}</span>{/if}</span>
            <span class="time">
              {#if row.slow}<span class="slow">longer than usual</span>{/if}
              <span class="num">{row.ms !== null ? workMoveDuration(row.ms) : ""}</span>
            </span>
          </li>
        {/each}
      </ol>
    {/if}

    <div class="slot" class:two={everything}>
      {#if !shown}
        <p class="note">{everything ? "Unfinished Tickets, active Managers and the Chief start new conversations from their records." : "Starts a new conversation from its record."} Push work before the move.</p>
        {#if !machines.isPending && !choices.length && !targetMachineId}<p class="note">No other machine is registered.</p>{/if}
        {#if machines.error}<ErrorLine error={machines.error} />{/if}
        {#if moves.error}<ErrorLine error={moves.error} /><p class="note">Progress is unavailable. Reopen this dialog to check it again.</p>{/if}
        {#if error}<ErrorLine {error} />{/if}
      {:else if finished && !summary.ok}
        <div class="outcome">
          {#if single?.result?.error}
            <p class="reason">{single.result.error}</p>
            <p class="next">{workMoveNextStep(single, single.owner.kind, source?.name ?? null)}</p>
          {:else}
            <p class="next">Nothing was lost. Fix each problem, then try that move again or choose “New conversation” on it.</p>
          {/if}
        </div>
      {/if}
    </div>

    <div class="sr" role="status" aria-live="polite">{dialogOpen ? announcement : ""}</div>

    <footer>
      {#if finished && shown && !retryable}
        <Button variant="primary" onclick={() => dialog?.close()} data-move-submit>Done</Button>
      {:else}
        <Button variant="ghost" onclick={() => dialog?.close()}>Close</Button>
        {#if finished && shown}
          <Button variant="primary" onclick={() => void move(operation?.machine_id)} data-move-submit>Try again</Button>
        {:else}
          <Button type="submit" variant="primary" disabled={busy || !destination || machines.isPending || moves.isPending} data-move-submit>{busy ? "Moving…" : everything ? "Move everything" : "Move"}</Button>
        {/if}
      {/if}
    </footer>
  </form>
</dialog>

<style>
  dialog { max-width:calc(100% - var(--space-5)); max-height:calc(100% - var(--space-5)); overflow:auto; margin:var(--space-7) auto auto; padding:0; border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-lg); background:var(--surface-2); color:var(--tone-primary); box-shadow:var(--shadow-float); }
  dialog::backdrop { background:var(--scrim); }
  form { padding:var(--space-5); display:flex; flex-direction:column; gap:var(--space-4); width:calc(100vw - var(--space-5) * 3); max-width:var(--page-prose-measure); }
  p { color:var(--tone-secondary); line-height:1.55; margin:0; }
  .num { font-variant-numeric:tabular-nums; }
  .sr { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); white-space:nowrap; }

  /* header: title left; elapsed time and state mark right. Nothing here moves between states. */
  .head { display:flex; align-items:center; gap:var(--space-3); height:calc(var(--space-5) + var(--space-1)); }
  h2 { margin:0; font-size:var(--type-page-section); font-weight:var(--weight-title); color:var(--tone-strong); overflow-wrap:anywhere; }
  .head-end { margin-left:auto; display:flex; align-items:center; gap:var(--space-3); flex:none; }
  .timer { color:var(--tone-secondary); font-size:var(--type-row); white-space:nowrap; }
  .count { color:var(--tone-tertiary); font-size:var(--type-small); font-weight:var(--weight-small); white-space:nowrap; }
  .mark-slot { display:grid; place-items:center; min-width:calc(var(--space-5) + var(--space-1)); }

  .composer-field-spinner { flex:none; color:var(--tone-secondary); }
  /* the sent mark, as .composer-field-send.sent: a solid circle with dark ink */
  .mark { flex:none; border-radius:50%; display:grid; place-items:center; color:var(--surface-ink); }
  .mark svg { width:var(--button-icon); height:var(--button-icon); fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
  .mark.ok { background:var(--accent-done); }
  .mark.bad { background:var(--accent-error); }
  .mark.lg { width:calc(var(--space-5) + var(--space-1)); height:calc(var(--space-5) + var(--space-1)); animation:pop var(--motion-enter) var(--motion-ease); }
  .mark.sm { width:var(--space-4); height:var(--space-4); }
  .mark.sm svg { width:var(--space-3); height:var(--space-3); }
  .mark.ok path { stroke-dasharray:20; stroke-dashoffset:20; animation:draw var(--motion-enter) var(--motion-ease) forwards; }
  @keyframes draw { to { stroke-dashoffset:0; } }
  @keyframes pop { from { transform:scale(.6); opacity:0; } to { transform:none; opacity:1; } }

  /* From → To */
  .pair { display:grid; grid-template-columns:1fr var(--space-5) 1fr; align-items:end; gap:var(--space-2); }
  .col { display:flex; flex-direction:column; gap:var(--space-1); min-width:0; }
  .lbl { font-size:var(--type-small); font-weight:var(--weight-small); color:var(--tone-tertiary); }
  .arrow { display:flex; align-items:center; justify-content:center; color:var(--tone-faint); padding-block:var(--space-2); }
  .arrow svg { width:var(--button-icon); height:var(--button-icon); }
  .field { position:relative; display:flex; align-items:center; gap:var(--space-2); padding:var(--space-2) var(--space-3); border-radius:var(--radius-md); background:var(--surface-recessed); color:var(--tone-primary); font-size:var(--type-row); line-height:1.55; min-width:0; transition:background-color var(--motion-slow) var(--motion-ease), opacity var(--motion-slow) var(--motion-ease); }
  .m { display:flex; flex:none; color:var(--machine-color, var(--tone-tertiary)); }
  .m :global(svg) { width:var(--button-icon); height:var(--button-icon); }
  .nm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .pick { background:var(--surface-input); }
  .pick:hover:not(.locked) { background:var(--surface-input-hover); }
  .pick.locked { background:var(--surface-recessed); }
  .chev { margin-left:auto; width:var(--space-3); height:var(--space-3); color:var(--tone-tertiary); flex:none; }
  select { position:absolute; inset:0; width:100%; opacity:0; cursor:pointer; }
  select:disabled { cursor:default; }
  .field:has(select:focus-visible) { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--space-1); }
  .left { opacity:.5; }
  .arrived { background:color-mix(in srgb, var(--machine-color) 16%, var(--surface-recessed)); }
  .arrive { margin-left:auto; display:flex; }
  .arrive svg { width:var(--button-icon); height:var(--button-icon); fill:none; stroke:var(--accent-done); stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
  .arrive path { stroke-dasharray:20; stroke-dashoffset:20; animation:draw var(--motion-enter) var(--motion-ease) forwards; }

  /* the list: one row per phase (one work item) or per piece of work (Move everything) */
  .steps { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; }
  .steps.scroll { min-height:calc(var(--space-7) * 4); max-height:calc(var(--space-7) * 4); overflow-y:auto; scrollbar-width:none; mask-image:linear-gradient(to bottom, transparent, #000 var(--space-3), #000 calc(100% - var(--space-3)), transparent); }
  .steps.scroll::-webkit-scrollbar { display:none; }
  .step { display:grid; grid-template-columns:var(--space-5) 1fr auto; column-gap:var(--space-2); align-items:center; padding:var(--space-2) 0; font-size:var(--type-row); line-height:1.55; color:var(--tone-faint); }
  .lead { display:flex; justify-content:center; }
  .sub { color:var(--tone-faint); margin-left:var(--space-2); font-size:var(--type-small); font-weight:var(--weight-small); }
  .time { display:flex; gap:var(--space-3); align-items:baseline; font-variant-numeric:tabular-nums; }
  .slow { color:var(--tone-tertiary); font-size:var(--type-small); }
  .why { grid-column:2 / 4; color:var(--tone-tertiary); font-size:var(--type-small); font-weight:var(--weight-small); }
  .done { color:var(--tone-secondary); }
  .active, .failed { color:var(--tone-strong); }
  .active .sub, .done .sub, .failed .sub { color:var(--tone-tertiary); }
  .ring { width:var(--space-2); height:var(--space-2); border-radius:50%; border:var(--border-hairline) solid var(--tone-faint); }
  .dash { width:var(--space-2); border-top:var(--border-hairline) solid var(--tone-faint); }
  .tick, .cross { width:var(--button-icon); height:var(--button-icon); fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
  .done .tick { color:var(--accent-done); }
  .failed .cross { color:var(--accent-error); }
  .tick path { stroke-dasharray:20; stroke-dashoffset:20; animation:draw var(--motion-base) var(--motion-ease) forwards; }

  /* under the list: the note, then a failure's reason, in the same place. A success leaves it empty. */
  .slot { min-height:calc(var(--type-row) * 1.55); overflow:hidden; }
  .slot.two { min-height:calc(var(--type-row) * 1.55 * 2); }
  .note, .reason, .next { font-size:var(--type-row); }
  .outcome { display:flex; flex-direction:column; gap:var(--space-2); animation:fadein var(--motion-enter) var(--motion-ease); }
  .reason { color:var(--tone-primary); font-weight:var(--weight-title); }
  @keyframes fadein { from { opacity:0; transform:translateY(var(--space-1)); } to { opacity:1; transform:none; } }
  /* only the slot grows on a failure */
  .slot:has(.outcome) { animation:grow var(--motion-slow) var(--motion-ease); }
  @keyframes grow { from { max-height:calc(var(--type-row) * 1.55); } to { max-height:calc(var(--space-7) * 4); } }

  footer { display:flex; justify-content:flex-end; flex-wrap:wrap; gap:var(--space-2); }
  footer :global(.button--primary) { min-width:calc(var(--space-6) * 2 + var(--space-5)); }

  /* the page button carries the move while the dialog is closed */
  :global(.button) .composer-field-spinner { color:inherit; }

  @media (max-width:720px) {
    dialog { margin-top:var(--space-4); }
    form { padding:var(--space-4); width:calc(100vw - var(--space-5)); }
    .pair { grid-template-columns:1fr var(--space-4) 1fr; }
    .slot { min-height:calc(var(--type-row) * 1.55 * 2); }
    .slot.two { min-height:calc(var(--type-row) * 1.55 * 3); }
  }
  @media (prefers-reduced-motion:reduce) {
    *, *::before, *::after { animation-duration:1ms !important; transition-duration:1ms !important; }
    .composer-field-spinner { animation-duration:var(--motion-loop-spin) !important; }
  }
</style>
