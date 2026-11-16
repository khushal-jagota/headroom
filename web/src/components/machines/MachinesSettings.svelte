<script lang="ts">
  import { createQuery, createQueries } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { mutateJson } from "../../lib/mutate";
  import { MACHINE_ICONS, MACHINE_COLORS, type Machine, type MachineIcon, type MachineColor } from "../../lib/machines/contracts";
  import MacStatus from "./MacStatus.svelte";
  import MachineSymbol from "./MachineSymbol.svelte";
  import Button from "../Button.svelte";
  import ErrorLine from "../ErrorLine.svelte";
  const machines = createQuery(() => queries.machines());
  const remoteMachines = $derived(machines.data?.machines.filter(machine => !machine.is_home) ?? []);
  const healthQueries = createQueries(() => ({queries: remoteMachines.map(machine => queries.executorHealth(machine.machine_id))}));
  const executorHealth = $derived(new Map(remoteMachines.map((machine, index) => [machine.machine_id, healthQueries[index]?.data])));
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
  function startAdding() { adding = true; newName = ""; addError = null; grant = null; copied = null; }
  function finishAdding() { adding = false; grant = null; }
  async function createJoinCommand() {
    creating = true; addError = null;
    try {
      grant = await mutateJson<JoinGrant>("/api/machines/join-command", {method: "POST", body: {name: newName.trim()}});
    } catch (cause) { addError = cause; }
    finally { creating = false; }
  }
  async function copy(label: string, text: string) {
    await navigator.clipboard.writeText(text);
    copied = label;
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
</script>
<section class="machines-settings" aria-label="Machines" data-machines-settings>
  <h2>Machines</h2>
  {#if machines.isPending}<p>Loading machines…</p>
  {:else if machines.isError}<ErrorLine error={machines.error} /><Button onclick={() => machines.refetch()}>Retry</Button>
  {:else}
    {#each machines.data?.machines ?? [] as machine (machine.machine_id)}
      <div class="machine-row" data-machine-id={machine.machine_id}>
        <div class="machine-summary">
          <span class="machine-mark" data-machine-color={machine.color}><MachineSymbol icon={machine.icon} /></span>
          <div class="machine-copy"><div class="machine-name">{machine.name}{#if machine.is_home}<span class="home-label">Home</span>{/if}</div>
            <p>{machine.available ? "Available" : "Away"} · {machine.running_workers === null ? "Running unknown" : `${machine.running_workers} ${machine.running_workers === 1 ? "Worker" : "Workers"} running`}{#if !machine.available} · {machine.waiting_tickets} {machine.waiting_tickets === 1 ? "Ticket waits" : "Tickets waiting"}{/if}</p>
          </div>
          {#if editing !== machine.machine_id}<Button variant="ghost" onclick={() => edit(machine)}>Edit</Button>{/if}
        </div>
        {#if !machine.is_home}<MacStatus machineId={machine.machine_id} health={executorHealth.get(machine.machine_id)} />{/if}
        {#if editing === machine.machine_id}
          <form onsubmit={(event) => {event.preventDefault(); void save();}}>
            <label>Name<input bind:value={name} required maxlength="80" disabled={saving} /></label>
            <label>Icon<select aria-label="Icon" bind:value={icon} disabled={saving}>{#each MACHINE_ICONS as option}<option value={option}>{option === "cloud" ? "Cloud machine" : option[0].toUpperCase() + option.slice(1)}</option>{/each}</select></label>
            <fieldset disabled={saving}><legend>Color</legend><div class="color-palette">{#each MACHINE_COLORS as option}<button type="button" class="color-option" data-machine-color={option} aria-label={option} aria-pressed={color === option} onclick={() => color = option}><span></span></button>{/each}</div></fieldset>
            {#if error}<ErrorLine {error} />{/if}
            <div class="edit-actions"><Button type="submit" disabled={saving || !name.trim()}>{saving ? "Saving…" : "Save"}</Button><Button variant="ghost" disabled={saving} onclick={() => editing = null}>Cancel</Button></div>
          </form>
        {/if}
      </div>
    {/each}
  {/if}
  <div class="add-machine" data-add-machine>
    {#if !adding}
      <Button onclick={startAdding}>Add a machine</Button>
    {:else if !grant}
      <form onsubmit={(event) => {event.preventDefault(); void createJoinCommand();}}>
        <label>New machine name<input bind:value={newName} required maxlength="80" disabled={creating} /></label>
        {#if addError}<ErrorLine error={addError} />{/if}
        <div class="edit-actions"><Button type="submit" disabled={creating || !newName.trim()}>{creating ? "Creating…" : "Create join command"}</Button><Button variant="ghost" disabled={creating} onclick={finishAdding}>Cancel</Button></div>
      </form>
    {:else}
      <div class="join-grant" data-join-grant>
        <p>Check that this is your home's address before you share anything: <strong>{grant.home_url}</strong></p>
        <p>1. On the new machine, signed in to the GitHub CLI, run:</p>
        <pre>{grant.command}</pre>
        <Button variant="ghost" onclick={() => copy("command", grant!.command)}>{copied === "command" ? "Copied" : "Copy command"}</Button>
        <p>2. When it asks for the credential, paste this:</p>
        <pre data-join-credential>{grant.credential}</pre>
        <Button variant="ghost" onclick={() => copy("credential", grant!.credential)}>{copied === "credential" ? "Copied" : "Copy credential"}</Button>
        <p class="warning">This credential is shown once and cannot be shown again. Keep it private; replacing the credential revokes it.</p>
        <p>3. After the command finishes, remove its temporary script: <code>rm -- "$panels_join_script"</code></p>
        <Button onclick={finishAdding}>Done</Button>
      </div>
    {/if}
  </div>
</section>
<style>
  .machines-settings { margin-top:var(--space-6); }
  h2 { font-size:var(--type-page-section); color:var(--tone-strong); margin:0 0 var(--space-4); }
  .machine-row { padding-block:var(--space-4); }
  .machine-summary { display:flex; gap:var(--space-3); align-items:center; }
  .machine-mark { color:var(--machine-color); display:flex; }
  .machine-copy { flex:1; min-width:0; }
  .machine-name { color:var(--tone-strong); font-size:var(--type-body); overflow-wrap:anywhere; }
  .home-label { margin-left:var(--space-2); color:var(--tone-tertiary); font-size:var(--type-sm); }
  p { margin:var(--space-1) 0 0; font-size:var(--type-sm); color:var(--tone-secondary); }
  form { margin-top:var(--space-4); margin-left:var(--space-6); max-width:var(--page-measure); display:flex; flex-direction:column; gap:var(--space-3); }
  label,legend { font-size:var(--type-sm); color:var(--tone-secondary); }
  label { display:flex; flex-direction:column; gap:var(--space-1); }
  input,select { background:var(--surface-input); border:1px solid var(--border-input); border-radius:var(--radius-sm); color:var(--tone-primary); padding:var(--space-2); font:inherit; }
  fieldset { border:0; margin:0; padding:0; }
  .add-machine { margin-top:var(--space-4); max-width:var(--page-measure); }
  .add-machine form { margin-left:0; }
  pre { margin:var(--space-2) 0; padding:var(--space-3); background:var(--surface-input); border:1px solid var(--border-input); border-radius:var(--radius-sm); white-space:pre-wrap; overflow-wrap:anywhere; font-size:var(--type-sm); color:var(--tone-primary); }
  .warning { color:var(--tone-strong); }
  .color-palette,.edit-actions { display:flex; gap:var(--space-2); flex-wrap:wrap; }
  .color-option { display:flex; padding:var(--space-2); background:none; border:1px solid transparent; border-radius:var(--radius-sm); cursor:pointer; }
  .color-option[aria-pressed="true"] { border-color:var(--tone-secondary); background:var(--interaction-hover); }
  .color-option span { width:var(--space-4); height:var(--space-4); border-radius:50%; background:var(--machine-color); }
</style>
