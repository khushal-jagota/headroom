<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { mutateJson } from "../../lib/mutate";
  import { macActivityKnown, machineName, type TicketCreationDraft, type TicketLocale } from "../../lib/ticketMachines";
  let { onCreated }: { onCreated:(id:string) => void } = $props();
  const workers = createQuery(() => queries.workerTypeManifests());
  const projects = createQuery(() => queries.projects());
  const managers = createQuery(() => queries.managers());
  let manager=$state("chief");
  let title=$state(""), kickoff=$state(""), worker=$state(""), project=$state(""), locale=$state<TicketLocale | "">("");
  let error=$state(""), creating=$state(false);
  let body=$derived<TicketCreationDraft>({manager:manager === "chief" ? {kind:"chief",id:"chief"} : {kind:"manager",id:manager},worker_type:worker, ...(project ? {project_id:project} : {}), ...(locale ? {locale} : {})});
  const projection = createQuery(() => ({...queries.ticketCreationContext(body), enabled:Boolean(worker)}));
  let context=$derived(!projection.isError && !projection.isFetching ? projection.data : undefined);
  let reading=$derived(projection.isFetching);
  const health = createQuery(() => ({...queries.executorHealth(),enabled:locale === "macos" || context?.resolved_locale === "macos"}));
  $effect(() => {
    if (!worker && workers.data?.worker_types.length) worker=workers.data.worker_types[0].worker_type;
  });
  let compatible=$derived(Boolean(context?.locale_choices.find(choice => choice.locale === context?.resolved_locale)?.compatible));
  async function create() {
    if (creating || projection.isFetching || projection.isError || !context || !compatible) return;
    creating=true; error="";
    try { const result=await mutateJson<{id:string}>("/api/tickets",{method:"POST",body:{...body,title,brief:kickoff}}); onCreated(result.id); }
    catch(e) {error=e instanceof Error ? e.message : "Ticket creation failed.";}
    finally {creating=false;}
  }
</script>
<form class="ticket-creation" onsubmit={event => {event.preventDefault(); void create();}} aria-label="Create a Ticket">
  <h2>Create a Ticket</h2>
  <label>Title<input bind:value={title} required /></label>
  <label>Brief<textarea bind:value={kickoff} required></textarea></label>
  <label>Worker<select aria-label="Worker" bind:value={worker}>{#each workers.data?.worker_types ?? [] as option}<option value={option.worker_type}>{option.label}</option>{/each}</select></label>
  <label>Manager<select aria-label="Manager" bind:value={manager}><option value="chief">Chief of Staff</option>{#each managers.data?.managers ?? [] as option}<option value={option.id}>{option.title}</option>{/each}</select></label>
  <label>Project<select aria-label="Project" bind:value={project}><option value="">No project</option>{#each projects.data?.projects ?? [] as option}<option value={option.id}>{option.name}</option>{/each}</select></label>
  <label>Run on<select aria-label="Run on" bind:value={locale}><option value="">Use the work requirement, otherwise VPS</option><option value="macos">Mac</option><option value="vps">VPS</option></select></label>
  {#if reading}<p role="status">Resolving machine…</p>{/if}
  {#if context}<p>Resolved machine: {machineName(context.resolved_locale)}.</p>{#each context.locale_choices.filter(choice => !choice.compatible) as choice}<p>{machineName(choice.locale)}: {choice.reason}</p>{/each}
    {#if context.resolved_locale === "macos" && !macActivityKnown(health.data)}<p>The Mac is unreachable or its current activity is unknown. Creation is allowed; work waits for executor-confirmed current activity.</p>{/if}
  {/if}
  {#if projection.isError}<p role="alert">{projection.error instanceof Error ? projection.error.message : "Cannot resolve the machine."}</p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
  <button class="button button--quiet" type="submit" disabled={creating || reading || projection.isError || !compatible || !title.trim() || !kickoff.trim()}>Create Ticket</button>
</form>
<style>.ticket-creation { padding:var(--space-4); background:var(--surface-2); overflow:auto; flex:none; max-height:65vh; }h2 {font-size:var(--type-lg);}label {display:grid; gap:var(--space-2); margin-bottom:var(--space-3);}input,textarea,select {font:inherit; padding:var(--space-2); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); background:var(--surface-recessed); color:var(--text-default);}p {font-size:var(--type-sm);}</style>
