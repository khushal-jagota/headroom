<script lang="ts">
  import type { Snippet } from "svelte";
  import type { TicketDetail } from "../lib/types";
  import { mutateJson } from "../lib/mutate";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";

  let { ticket, terminal, leading }: { ticket: TicketDetail; terminal: boolean; leading?: Snippet } = $props();
  let busy = $state(false);
  let error = $state<unknown>(null);

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

<div class="ticket-lifecycle" data-ticket-lifecycle hidden={terminal}>
  {#if !terminal}
    <div class="actions">
      {#if !terminal}
      {@render leading?.()}
      {#if leading}<span class="separator" aria-hidden="true">·</span>{/if}
      {#if ticket.is_backlogged}<span class="state" data-ticket-backlogged>Backlogged</span>{/if}
      {#if ticket.is_backlogged}
        <Button disabled={busy} data-ticket-backlog-action onclick={() => void change("resume")}>Resume ticket</Button>
      {:else}
        <button type="button" class="text-action" disabled={busy} data-ticket-backlog-action onclick={() => void change("backlog")}>Backlog ticket</button>
      {/if}
      {/if}
    </div>
    {#if !terminal && ticket.is_backlogged}<p class="hint">Automatic work is paused. Messages and approvals remain available.</p>{/if}
  {/if}
  {#if error}<ErrorLine {error} />{/if}
</div>

<style>
  .ticket-lifecycle { margin-top:var(--space-4); font-size:13px; color:var(--text-muted); }
  .actions { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); }
  .text-action { border:0; background:none; padding:var(--space-1) 0; color:var(--text-muted); font:inherit; font-weight:590; cursor:pointer; text-underline-offset:3px; text-decoration-thickness:1px; }
  .text-action:hover { color:var(--text-strong); text-decoration-line:underline; text-decoration-color:var(--text-faintest); }
  .text-action:disabled { cursor:default; opacity:.5; }
  .separator { color:var(--text-faintest); }
  .state { color:var(--text-strong); font-weight:660; }
  .hint { margin:var(--space-2) 0; line-height:1.6; }
</style>
