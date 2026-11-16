<script lang="ts">
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import { mutateJson } from "../lib/mutate";
  import type { TicketDetail } from "../lib/types";

  let { ticket }: { ticket: TicketDetail } = $props();
  let confirming = $state(false);
  let busy = $state(false);
  let error = $state<unknown>(null);

  async function restart(): Promise<void> {
    busy = true;
    error = null;
    try {
      await mutateJson(`/api/tickets/${encodeURIComponent(ticket.id)}/restart-worker`, {
        method: "POST", body: {}
      });
      confirming = false;
    } catch (err) {
      error = err;
    } finally {
      busy = false;
    }
  }
</script>

{#if ticket.unresolved_decision_feedback?.length}
  <section class="delivery" aria-label="Feedback delivery" data-decision-feedback-delivery>
    {#each ticket.unresolved_decision_feedback as feedback (feedback.decision_id)}
      <p class="delivery-line"><span class="delivery-dot" aria-hidden="true"></span><span>{feedback.status === "uncertain" ? "Feedback may not have been delivered" : feedback.status === "refused" ? "Feedback was not delivered" : "Feedback delivery was discarded"}: {feedback.message}</span></p>
    {/each}
    {#if confirming}
      <p class="delivery-explanation">Restarting stops the current Worker and retries the feedback. If it was already delivered, the Worker may receive it twice.</p>
      <div class="delivery-actions">
        <Button variant="ghost" disabled={busy} onclick={() => void restart()}>Restart and retry</Button>
        <Button variant="ghost" disabled={busy} onclick={() => confirming = false}>Cancel</Button>
      </div>
    {:else}
      <button class="delivery-retry" type="button" onclick={() => confirming = true}>Recover feedback</button>
    {/if}
    {#if error}<ErrorLine {error} />{/if}
  </section>
{/if}

<style>
  .delivery { margin-top:var(--space-3); color:var(--tone-secondary); font-size:var(--type-small); line-height:18px; }
  .delivery-line { display:flex; align-items:baseline; gap:var(--space-2); margin:0; white-space:pre-wrap; }
  .delivery-dot { width:6px; height:6px; flex:none; border-radius:50%; background:var(--accent-error); align-self:baseline; }
  .delivery-explanation { margin:var(--space-2) 0 0; max-width:42rem; }
  .delivery-retry { border:0; padding:0; margin:var(--space-1) 0 0 calc(6px + var(--space-2)); background:none; color:var(--tone-secondary); font:inherit; text-decoration:underline; text-underline-offset:2px; cursor:pointer; }
  .delivery-retry:hover { color:var(--tone-primary); }
  .delivery-actions { display:flex; gap:var(--space-1); margin-left:calc(-1 * var(--space-3)); }
</style>
