<script lang="ts">
  import { tick } from "svelte";
  import { createQuery, createQueries } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { mutateJson } from "../../lib/mutate";
  import { MACHINE_ICONS, MACHINE_COLORS, type Machine, type MachineIcon, type MachineColor } from "../../lib/machines/contracts";
  import MacStatus from "../machines/MacStatus.svelte";
  import MachineSymbol from "../machines/MachineSymbol.svelte";
  import ServerMoveDialog from "../machines/ServerMoveDialog.svelte";
  import { canMoveServerTo, serverMoveActive, serverMovePhaseText } from "../../lib/machines/logic/serverMove";
  import Button from "../Button.svelte";
  import ErrorLine from "../ErrorLine.svelte";
  import SettingsPage from "./SettingsPage.svelte";
  import SettingsRow from "./SettingsRow.svelte";
  import SettingsSection from "./SettingsSection.svelte";
  import { machineStatus } from "../../lib/settings/statusText";

  /* Machines: one row per machine with its mark, name and status, an inline edit form, and
     the Add a machine sheet that makes a join command. */
  const machines = createQuery(() => queries.machines());
  const remoteMachines = $derived(machines.data?.machines.filter(machine => !machine.is_home) ?? []);
  const healthQueries = createQueries(() => ({queries: remoteMachines.map(machine => queries.executorHealth(machine.machine_id))}));
  const executorHealth = $derived(new Map(remoteMachines.map((machine, index) => [machine.machine_id, healthQueries[index]?.data])));
  // The latest move of the server, shown on its target machine's row.
  const serverMove = createQuery(() => queries.serverMove());
  const move = $derived(serverMove.data?.move ?? null);
  let moveDialog = $state<ServerMoveDialog | null>(null);

  let editing = $state<string | null>(null);
  let name = $state("");
  let icon = $state<MachineIcon>("server");
  let color = $state<MachineColor>("jade");
  let saving = $state(false);
  let error = $state<string | null>(null);

  type JoinGrant = {machine_id: string; home_url: string; command: string; credential: string};
  let adding = $state(false);
  let newName = $state("");
  let creating = $state(false);
  let addError = $state<unknown>(null);
  let grant = $state<JoinGrant | null>(null);
  let copied = $state<string | null>(null);
  let copiedTimer: ReturnType<typeof setTimeout> | undefined;
  let addButton = $state<HTMLElement | null>(null);
  let sheet = $state<HTMLElement | null>(null);
  let nameField = $state<HTMLInputElement | null>(null);

  async function startAdding() {
    adding = true; newName = ""; addError = null; grant = null; copied = null;
    await tick();
    nameField?.focus();
  }
  function finishAdding() {
    adding = false; grant = null; copied = null;
    addButton?.querySelector("button")?.focus();
  }
  async function createJoinCommand() {
    creating = true; addError = null;
    try {
      grant = await mutateJson<JoinGrant>("/api/machines/join-command", {method: "POST", body: {name: newName.trim()}});
      await tick();
      sheet?.focus();
    } catch (cause) { addError = cause; }
    finally { creating = false; }
  }
  async function copy(label: string, text: string) {
    await navigator.clipboard.writeText(text);
    copied = label;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => { if (copied === label) copied = null; }, 1500);
  }
  /** The scrim closes the name sheet, never the join steps: the credential is shown once. */
  function scrimClick(event: MouseEvent) {
    if (event.target === event.currentTarget && !grant && !creating) finishAdding();
  }
  function windowKeydown(event: KeyboardEvent) {
    // Escape, like the scrim, never closes the join steps: the credential is shown once.
    if (adding && event.key === "Escape" && !creating && !grant) { event.preventDefault(); finishAdding(); }
  }
  /** Tab stays inside the open sheet. */
  function sheetKeydown(event: KeyboardEvent) {
    if (event.key !== "Tab" || !sheet) return;
    const focusable = [...sheet.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), a[href]")];
    if (focusable.length === 0) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === sheet)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function edit(machine: Machine) {
    editing = machine.machine_id; name = machine.name; icon = machine.icon; color = machine.color; error = null;
  }
  async function save() {
    saving = true; error = null;
    try {
      await mutateJson(`/api/machines/${encodeURIComponent(editing!)}`, {method: "PATCH", body: {name, icon, color}});
      editing = null;
    } catch (cause) { error = String(cause); }
    finally { saving = false; }
  }
  function colorKeydown(event: KeyboardEvent) {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (step === 0) return;
    event.preventDefault();
    const next = MACHINE_COLORS[(MACHINE_COLORS.indexOf(color) + step + MACHINE_COLORS.length) % MACHINE_COLORS.length];
    color = next;
    (event.currentTarget as HTMLElement).parentElement?.querySelector<HTMLElement>(`[data-machine-color="${next}"]`)?.focus();
  }
</script>

<SettingsPage title="Machines">
  {#snippet actions()}
    <span bind:this={addButton}><Button variant="secondary" onclick={() => void startAdding()}>Add a machine</Button></span>
  {/snippet}
  {#if machines.isPending}
    <span class="skeleton-status" role="status">Loading machines…</span>
    <div class="machine-skeleton" aria-hidden="true">
      <SettingsSection first>
        {#each Array.from({ length: 2 }, (_, index) => index) as index}
          <div class="machine-skeleton-row">
            <span class="skeleton-line" style:width={`${20 + ((index * 17) % 25)}%`}></span>
            <span class="skeleton-line machine-skeleton-description" style:width={`${40 + ((index * 23) % 30)}%`}></span>
          </div>
        {/each}
      </SettingsSection>
    </div>
  {:else if machines.isError}
    <div class="machine-load-error"><ErrorLine error={machines.error} /><Button onclick={() => machines.refetch()}>Retry</Button></div>
  {:else}
    <SettingsSection first>
      {#each machines.data?.machines ?? [] as machine (machine.machine_id)}
        {@const status = machineStatus(machine)}
        <SettingsRow label={machine.name} tag={machine.is_home ? "Home" : undefined} rowId={`machines:${machine.machine_id}`}>
          {#snippet lead()}
            <span class="machine-mark" data-machine-color={machine.color} data-machine-id={machine.machine_id}><MachineSymbol icon={machine.icon} /></span>
          {/snippet}
          {#snippet description()}
            {#if status.away}<span class="machine-away">Away</span>{:else}Available{/if} · {status.rest}
          {/snippet}
          {#snippet control()}
            {#if editing !== machine.machine_id}
              {#if canMoveServerTo(machine, move)}<Button variant="ghost" onclick={() => void moveDialog?.openDialog(machine)} data-server-move={machine.machine_id}>Move the server here</Button>{/if}
              <Button variant="ghost" onclick={() => edit(machine)} aria-label={`Edit ${machine.name}`}>Edit</Button>
            {/if}
          {/snippet}
          {#snippet after()}
            {#if editing === machine.machine_id}
              <form class="machine-edit" onsubmit={(event) => { event.preventDefault(); void save(); }}>
                <label class="machine-field">Name<input class="machine-input" bind:value={name} required maxlength="80" disabled={saving} /></label>
                <label class="machine-field">Icon<select class="machine-input" bind:value={icon} disabled={saving}>{#each MACHINE_ICONS as option}<option value={option}>{option === "cloud" ? "Cloud machine" : option[0].toUpperCase() + option.slice(1)}</option>{/each}</select></label>
                <div class="machine-field">Color
                  <div class="machine-swatches" role="radiogroup" aria-label="Color">
                    {#each MACHINE_COLORS as option}
                      <button type="button" role="radio" data-machine-color={option} aria-label={option} aria-checked={color === option}
                        tabindex={color === option ? 0 : -1} disabled={saving} onclick={() => color = option} onkeydown={colorKeydown}><span></span></button>
                    {/each}
                  </div>
                </div>
                {#if error}<ErrorLine {error} />{/if}
                <div class="machine-actions">
                  <Button type="submit" variant="primary" disabled={saving || !name.trim()}>{saving ? "Saving…" : "Save"}</Button>
                  <Button variant="ghost" disabled={saving} onclick={() => editing = null}>Cancel</Button>
                </div>
              </form>
            {:else}
              {#if move?.target_machine_id === machine.machine_id}
                <div class="machine-detail machine-move" class:machine-move--failed={move.phase === "failed"} role="status"
                  data-server-move-phase={move.phase} aria-busy={serverMoveActive(move)}>{serverMovePhaseText(move)}</div>
              {/if}
              {#if !machine.is_home}<div class="machine-detail"><MacStatus machineId={machine.machine_id} health={executorHealth.get(machine.machine_id)} /></div>{/if}
            {/if}
          {/snippet}
        </SettingsRow>
      {/each}
    </SettingsSection>
  {/if}
</SettingsPage>

<svelte:window onkeydown={windowKeydown} />

<ServerMoveDialog bind:this={moveDialog} />

{#if adding}
  <!-- The scrim is a pointer target only; Escape and Cancel close the sheet from the keyboard. -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="machine-scrim" role="presentation" onclick={scrimClick} data-add-machine>
    {#if !grant}
      <div class="machine-sheet" role="dialog" aria-modal="true" aria-label="Add a machine" tabindex="-1" bind:this={sheet} onkeydown={sheetKeydown}>
        <form class="machine-sheet-form" onsubmit={(event) => { event.preventDefault(); void createJoinCommand(); }}>
          <h3>Add a machine</h3>
          <label class="machine-field">Name<input class="machine-input" bind:this={nameField} bind:value={newName} required maxlength="80" disabled={creating} placeholder="Mac 2" autocomplete="off" /></label>
          {#if addError}<ErrorLine error={addError} />{/if}
          <div class="machine-actions machine-actions--end">
            <Button variant="ghost" disabled={creating} onclick={finishAdding}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={creating || !newName.trim()}>{creating ? "Creating…" : "Create join command"}</Button>
          </div>
        </form>
      </div>
    {:else}
      <div class="machine-sheet" role="dialog" aria-modal="true" aria-label={`Join ${newName.trim()}`} tabindex="-1" bind:this={sheet}
        onkeydown={sheetKeydown} data-join-grant>
        <h3>Join {newName.trim()} to Panels</h3>
        <p>Check that this is your home’s address before you share anything: <strong>{grant.home_url}</strong></p>
        <div class="machine-step">
          <p>1. On {newName.trim()}, signed in to the GitHub CLI, run:</p>
          <pre class="machine-codebox">{grant.command}<Button onclick={() => copy("command", grant!.command)} aria-label="Copy command">{copied === "command" ? "Copied" : "Copy"}</Button></pre>
        </div>
        <div class="machine-step">
          <p>2. When it asks for the credential, paste this:</p>
          <pre class="machine-codebox" data-join-credential>{grant.credential}<Button onclick={() => copy("credential", grant!.credential)} aria-label="Copy credential">{copied === "credential" ? "Copied" : "Copy"}</Button></pre>
          <p class="machine-warning">This credential is shown once and cannot be shown again. Keep it private; replacing the credential revokes it.</p>
        </div>
        <div class="machine-step">
          <p>3. After the command finishes, remove its temporary script:</p>
          <pre class="machine-codebox">rm -- "$panels_join_script"<Button onclick={() => copy("cleanup", 'rm -- "$panels_join_script"')} aria-label="Copy cleanup command">{copied === "cleanup" ? "Copied" : "Copy"}</Button></pre>
        </div>
        <div class="machine-actions machine-actions--end"><Button variant="primary" onclick={finishAdding}>Done</Button></div>
      </div>
    {/if}
  </div>
{/if}

<style>
  .machine-mark { flex:none; display:grid; place-items:center; width:var(--space-5); color:var(--machine-color); }
  /* The rail icons' size (as `.utility svg` in app.css), not the inline text size MachineSymbol uses elsewhere. */
  .machine-mark :global(svg) { width:18px; height:18px; }
  .machine-away { color:var(--accent-attention); }
  .machine-detail { padding:0 0 var(--space-4) calc(var(--space-5) + var(--space-4)); }
  .machine-detail:empty { display:none; }
  .machine-move { padding-bottom:var(--space-2); font-size:var(--type-row); color:var(--tone-secondary); overflow-wrap:anywhere; }
  .machine-move--failed { color:var(--accent-error); }
  .machine-load-error { display:flex; align-items:center; gap:var(--space-3); margin-top:var(--page-block-gap); }

  .machine-edit { display:grid; gap:var(--space-3); max-width:420px; padding:0 0 var(--space-4) calc(var(--space-5) + var(--space-4)); }
  .machine-field { display:grid; gap:var(--space-1); font-size:var(--type-small); color:var(--tone-tertiary); }
  .machine-input { width:100%; min-height:32px; padding:0 var(--space-2); border:var(--border-hairline) solid var(--border-input); border-radius:var(--radius-md); background:var(--surface-input); color:var(--tone-primary); font:inherit; font-size:var(--type-row); }
  .machine-input::placeholder { color:var(--tone-faint); font-style:italic; }
  .machine-actions { display:flex; align-items:center; gap:var(--space-2); }
  .machine-actions--end { justify-content:flex-end; }
  .machine-swatches { display:flex; flex-wrap:wrap; gap:var(--space-1); }
  .machine-swatches button { display:grid; place-items:center; width:var(--button-height); height:var(--button-height); padding:0; border:var(--border-hairline) solid transparent; border-radius:var(--radius-md); background:none; cursor:pointer; }
  .machine-swatches button[aria-checked="true"] { border-color:var(--tone-secondary); background:var(--interaction-hover); }
  .machine-swatches button:disabled { cursor:default; }
  .machine-swatches span { width:var(--button-icon); height:var(--button-icon); border-radius:50%; background:var(--machine-color); }

  .machine-scrim { position:fixed; inset:0; z-index:70; display:grid; grid-template-columns:minmax(0, 1fr); place-items:center; padding:var(--space-4); background:var(--scrim); animation:machine-fade var(--motion-base) var(--motion-ease); }
  .machine-sheet { width:480px; max-width:100%; max-height:calc(100vh - var(--space-6)); overflow-y:auto; display:grid; gap:var(--space-4); padding:var(--space-5); background:var(--surface-raised); border:var(--border-hairline) solid var(--border-raised); border-radius:var(--radius-lg); box-shadow:var(--shadow-float); animation:machine-sheet-in var(--motion-base) var(--motion-ease); }
  .machine-sheet:focus { outline:0; }
  .machine-sheet-form { display:grid; gap:var(--space-4); }
  .machine-sheet h3 { margin:0; font-size:var(--type-page-section); font-weight:var(--weight-page-section); color:var(--tone-strong); }
  .machine-sheet p { margin:0; font-size:var(--type-row); color:var(--tone-secondary); overflow-wrap:anywhere; }
  .machine-sheet p strong { color:var(--tone-strong); font-weight:var(--weight-needs-you); }
  .machine-sheet .machine-warning { color:var(--tone-strong); }
  .machine-step { display:grid; gap:var(--space-2); }
  .machine-codebox { position:relative; margin:0; padding:var(--space-3); padding-right:calc(var(--space-7) + var(--space-6)); background:var(--surface-recessed); border-radius:var(--radius-md); font:var(--type-code)/1.6 var(--font-mono); color:var(--tone-primary); white-space:pre-wrap; overflow-wrap:anywhere; }
  .machine-codebox :global(.button) { position:absolute; top:var(--space-2); right:var(--space-2); font-family:var(--font-ui); }
  @keyframes machine-fade { from { opacity:0; } }
  @keyframes machine-sheet-in { from { opacity:0; transform:translateY(var(--space-1)); } to { opacity:1; transform:none; } }
  .machine-skeleton { animation:machine-skeleton-in var(--skeleton-fade) ease-in-out var(--skeleton-delay) both; }
  .machine-skeleton-row { display:flex; flex-direction:column; justify-content:center; min-height:52px; }
  .machine-skeleton-description { height:8px; margin-top:var(--space-2); }
  @keyframes machine-skeleton-in { from { opacity:0; } to { opacity:1; } }
  @media (max-width: 720px) { .machine-edit, .machine-detail { padding-left:0; } }
  @media (prefers-reduced-motion: reduce) { .machine-scrim, .machine-sheet, .machine-skeleton { animation:none; } }
</style>
