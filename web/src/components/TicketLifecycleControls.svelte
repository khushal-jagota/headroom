<script lang="ts">
  import Button from "./Button.svelte";
  import type { Snippet } from "svelte";
  import type { TicketDetail } from "../lib/types";
  import { mutateJson } from "../lib/mutate";
  import ErrorLine from "./ErrorLine.svelte";
  import { recheckWorkerCleanup, workerCleanupMessage } from "../lib/workerCleanup";

  /* The ticket's own page actions. They are ghosts on the ticket page, where the decision card
   * below carries the page's call to action. */
  let { ticket, terminal, leading, variant = "ghost" }: { ticket: TicketDetail; terminal: boolean; leading?: Snippet; variant?: "secondary" | "ghost" } = $props();
  let busy = $state(false);
  let error = $state<unknown>(null);
  let cleanupNote = $state<string | null>(null);

  async function recheckCleanup(): Promise<void> {
    if (busy) return;
    busy = true;
    error = null;
    cleanupNote = null;
    try {
      cleanupNote = workerCleanupMessage(await recheckWorkerCleanup(ticket.id));
    } catch (err) {
      error = err;
    } finally {
      busy = false;
    }
  }

  async function change(action: "backlog" | "resume"): Promise<void> {
    if (busy) return;
    busy = true;
    error = null;
    try {
      await mutateJson(`/api/tickets/${encodeURIComponent(ticket.id)}/${action}`, {
        method: "POST",
        body: {}
      });

    } catch (err) {
      error = err;
    } finally {
      busy = false;
    }
  }
</script>

<div class="ticket-lifecycle" data-ticket-lifecycle>
  <div class="actions">
    {#if !terminal}
      {@render leading?.()}
      {#if leading}<span class="separator" aria-hidden="true">·</span>{/if}
      {#if ticket.is_backlogged}<span class="state" data-ticket-backlogged>Backlogged</span>{/if}
      {#if ticket.is_backlogged}
        <Button variant="secondary" disabled={busy} data-ticket-backlog-action onclick={() => void change("resume")}>Resume ticket</Button>
      {:else}
        <Button {variant} class="text-action" disabled={busy} data-ticket-backlog-action onclick={() => void change("backlog")}>Backlog ticket</Button>
      {/if}
    {/if}
    <Button {variant} class="text-action" disabled={busy} data-worker-cleanup-recheck onclick={() => void recheckCleanup()}>Recheck Worker cleanup</Button>
  </div>
  {#if !terminal && ticket.is_backlogged}<p class="hint">Automatic work is paused. Messages and approvals remain available.</p>{/if}
  {#if cleanupNote}<p class="hint" role="status" data-worker-cleanup-result>{cleanupNote}</p>{/if}
  {#if error}<ErrorLine {error} />{/if}
</div>

<style>
  .ticket-lifecycle { font-size:var(--type-small); color:var(--tone-tertiary); }
  .actions { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); }
  .separator { display:none; }
  .state { color:var(--tone-secondary); font-size:var(--type-small); font-weight:var(--weight-small); margin-right:var(--space-1); }
  .hint { margin:var(--space-2) 0; line-height:18px; }
</style>
