<script lang="ts">
  import type { TicketDetail } from "../lib/types";
  import { mutateJson } from "../lib/mutate";
  import { retirementReason } from "../lib/ticketRetirement";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";

  let { ticket, terminal }: { ticket: TicketDetail; terminal: boolean } = $props();
  let confirmingRetirement = $state(false);
  let actionsOpen = $state(false);
  let reason = $state("");
  let busy = $state(false);
  let error = $state<unknown>(null);
  let savedReason = $derived(retirementReason(ticket));

  async function change(action: "backlog" | "resume" | "retire"): Promise<void> {
    if (busy || (action === "retire" && !reason.trim())) return;
    busy = true;
    error = null;
    try {
      await mutateJson(`/api/tickets/${encodeURIComponent(ticket.id)}/${action}`, {
        method: "POST",
        body: {
          ...(ticket.record_sha256 ? { expected_record_sha256: ticket.record_sha256 } : {}),
          ...(action === "retire" ? { reason: reason.trim() } : {})
        }
      });
      confirmingRetirement = false;
      reason = "";
    } catch (err) {
      error = err;
    } finally {
      busy = false;
    }
  }
</script>

<div class="ticket-lifecycle" data-ticket-lifecycle>
  {#if ticket.stage === "cancelled"}
    <p class="state" data-ticket-retired>Retired without success</p>
    {#if savedReason}<p class="reason" data-retirement-reason>{savedReason}</p>{/if}
  {:else if !terminal}
    <div class="actions">
      {#if ticket.is_backlogged}<span class="state" data-ticket-backlogged>Backlogged</span>{/if}
      {#if ticket.is_backlogged}
        <Button disabled={busy} data-ticket-backlog-action onclick={() => void change("resume")}>Resume ticket</Button>
      {:else}
        <button type="button" class="text-action" disabled={busy} data-ticket-backlog-action onclick={() => void change("backlog")}>Backlog ticket</button>
      {/if}
      <details class="secondary-actions" bind:open={actionsOpen}>
        <summary aria-label="Ticket actions" data-ticket-actions>More</summary>
        <div class="action-menu">
          <button type="button" class="text-action" disabled={busy} data-ticket-retire-action onclick={() => { confirmingRetirement = true; actionsOpen = false; }}>Retire ticket</button>
        </div>
      </details>
    </div>
    {#if ticket.is_backlogged}<p class="hint">Automatic work is paused. Messages and approvals remain available.</p>{/if}
    {#if confirmingRetirement}
      <form class="retirement" onsubmit={(event) => { event.preventDefault(); void change("retire"); }}>
        <label for={`retirement-reason-${ticket.id}`}>Reason for retirement</label>
        <textarea id={`retirement-reason-${ticket.id}`} class="in" rows="2" bind:value={reason} required data-retirement-input></textarea>
        <p class="hint">This ends the ticket without success. Its steps, proposals, and conversation remain in its history.</p>
        <div class="actions">
          <button type="submit" class="button button--primary" disabled={busy || !reason.trim()} data-confirm-retirement>Retire without success</button>
          <Button disabled={busy} onclick={() => confirmingRetirement = false}>Cancel</Button>
        </div>
      </form>
    {/if}
  {/if}
  {#if error}<ErrorLine {error} />{/if}
</div>

<style>
  .ticket-lifecycle { margin-top:var(--space-4); font-size:13px; color:var(--text-muted); }
  .actions { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); }
  .text-action, .secondary-actions summary { border:0; background:none; padding:var(--space-1) 0; color:var(--text-faint); font:inherit; cursor:pointer; }
  .text-action:hover, .secondary-actions summary:hover { color:var(--text-strong); }
  .text-action:disabled { cursor:default; opacity:.5; }
  .secondary-actions { position:relative; margin-left:var(--space-2); }
  .secondary-actions summary { list-style:none; }
  .secondary-actions summary::-webkit-details-marker { display:none; }
  .action-menu { position:absolute; z-index:2; top:100%; left:0; min-width:max-content; padding:var(--space-2) var(--space-3); background:var(--surface-2); border:1px solid var(--border-color); border-radius:var(--radius-sm); }
  .state { color:var(--text-strong); font-weight:600; }
  .hint, .reason { margin:var(--space-2) 0; line-height:1.6; }
  .reason { white-space:pre-wrap; }
  .retirement { margin-top:var(--space-3); padding:var(--space-4); background:var(--surface-2); border-radius:var(--radius-sm); }
  .retirement label { display:block; margin-bottom:var(--space-2); }
  .retirement textarea { width:100%; box-sizing:border-box; }
</style>
