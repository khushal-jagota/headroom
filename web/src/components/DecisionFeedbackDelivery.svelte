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
    <strong>Feedback delivery needs attention</strong>
    {#each ticket.unresolved_decision_feedback as feedback (feedback.decision_id)}
      <p>{feedback.status === "uncertain" ? "Delivery uncertain" : feedback.status === "refused" ? "Delivery refused" : "Delivery discarded"}: {feedback.message}</p>
    {/each}
    {#if confirming}
      <p>Restarting stops the current Worker and retries unresolved feedback. Uncertain feedback may already have been delivered, so it could be delivered twice.</p>
      <Button variant="secondary" disabled={busy} onclick={() => void restart()}>Restart and retry feedback</Button>
      <Button variant="ghost" disabled={busy} onclick={() => confirming = false}>Cancel</Button>
    {:else}
      <Button variant="secondary" onclick={() => confirming = true}>Recover feedback delivery</Button>
    {/if}
    {#if error}<ErrorLine {error} />{/if}
  </section>
{/if}

<style>
  .delivery { margin-block: var(--space-4); color: var(--tone-secondary); font-size: var(--type-small); }
  .delivery p { white-space: pre-wrap; }
</style>
