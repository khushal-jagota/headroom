<script lang="ts">
  import { navigationApplication } from "../../lib/navigation/application.svelte";
  import { ticketCommandImpact } from "../../lib/ticketNavigationImpact";
  const navigation=navigationApplication();
  import { fetchJson } from "../../lib/api";
  import { mutateJson } from "../../lib/mutate";
  import type { TicketDetail } from "../../lib/types";
  import { machineName, type TicketLocale } from "../../lib/ticketMachines";
  let { locale, ticketId }: { locale:TicketLocale; ticketId:string } = $props();
  let dialog = $state<HTMLDialogElement>();
  let busy = $state(false), error = $state("");
  let current = $state<TicketDetail | null>(null);
  let destination = $state<TicketLocale>("vps");
  function open(event: MouseEvent) { event.stopPropagation(); current=null; dialog?.showModal(); void review(); }
  async function review() {
    if (busy) return;
    current=null; busy=true; error=""; destination=locale === "macos" ? "vps" : "macos";
    try { current=await fetchJson<TicketDetail>(`/api/tickets?detail=full&id=${encodeURIComponent(ticketId)}`); }
    catch (e) { error=e instanceof Error ? e.message : "Cannot read the current Ticket."; }
    finally { busy=false; }
  }
  async function move() {
    if (busy || !current?.record_sha256) return;
    busy=true; error="";
    try { const body={locale:destination,expected_record_sha256:current.record_sha256};
      const write=(responseMetadata?:import("../../lib/api").FetchOptions["responseMetadata"])=>mutateJson(`/api/tickets/${encodeURIComponent(ticketId)}/move`, {method:"POST",body,responseMetadata});
      if(navigation) await navigation.runCommand(ticketCommandImpact(navigation.state.effective,ticketId,"move"),write,"move Ticket");
      else await write();
      dialog?.close(); }
    catch (e) { error=e instanceof Error ? e.message : "The move was refused."; current=null; }
    finally { busy=false; }
  }
</script>
<button type="button" class="text-action" data-ticket-move onclick={open}>Move to {machineName(locale === "macos" ? "vps" : "macos")}…</button>
<dialog bind:this={dialog} data-ticket-move-dialog aria-label="Move Ticket" oncancel={(event) => { if (busy) event.preventDefault(); }}>
    <h2>Move Ticket</h2>
    <p>{current?.title ?? "Review the current Ticket before moving."}</p>
    <label>Destination <select bind:value={destination} disabled={busy}><option value="macos">Mac</option><option value="vps">VPS</option></select></label>
    <p>Only the machine choice changes. Files and uncommitted work do not travel. The destination needs a fresh checkout. Access stays unchanged.</p>
    <p>The server refuses active turns, pending asks, unsent work, unknown source state, incompatible destinations, and changed preconditions. A move is never queued.</p>
    {#if error}<p role="alert">{error}</p><button type="button" class="button button--quiet" disabled={busy} onclick={review}>Review again</button>{/if}
    <button type="button" class="button button--quiet" disabled={busy || !current?.record_sha256 || destination === current?.locale} onclick={move}>Move Ticket</button>
  <button type="button" class="button button--quiet" disabled={busy} onclick={() => dialog?.close()}>Close</button>
</dialog>
<style>
.text-action { border:0; background:none; padding:var(--space-1) 0; min-height:var(--space-5); color:var(--text-faint); font:inherit; cursor:pointer; }.text-action:hover { color:var(--text-strong); }button:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }dialog { box-sizing:border-box; max-height:calc(100dvh - var(--space-4) * 2); overflow:auto; max-width:440px; width:calc(100% - 32px); padding:var(--space-5); background:var(--surface-2); color:var(--text-default); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); box-shadow:var(--shadow-float); }dialog::backdrop { background:var(--scrim); }h2 { font-size:var(--type-lg); }p { line-height:1.5; }select { min-height:var(--space-5); background:var(--surface-recessed); color:var(--text-default); } .button { margin:var(--space-2) var(--space-2) 0 0; }
</style>
