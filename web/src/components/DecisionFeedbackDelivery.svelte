<script lang="ts">
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import { mutateJsonAndWaitForScreen } from "../lib/mutate";
  import type { TicketDetail } from "../lib/types";

  let { ticket }: { ticket: TicketDetail } = $props();
  let confirming = $state(false);
  let busy = $state(false);
  let error = $state<unknown>(null);

  async function restart(): Promise<void> {
    busy = true;
    error = null;
    try {
      await mutateJsonAndWaitForScreen(`/api/tickets/${encodeURIComponent(ticket.id)}/restart-worker`, {
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
      <ErrorLine error={{ message: feedback.status === "uncertain" ? "Your note may not have reached the Worker." : "Your note did not reach the Worker." }} />
      {#if feedback.message}<blockquote>{feedback.message}</blockquote>{/if}
    {/each}
    {#if confirming}
      <ErrorLine error={{ message: "Restart stops the Worker and retries your note. The Worker may receive it twice." }} />
      <div class="delivery-actions">
        <Button variant="ghost" disabled={busy} onclick={() => confirming = false}>Cancel</Button>
        <Button variant="secondary" disabled={busy} onclick={() => void restart()}>Restart and retry</Button>
      </div>
    {:else}
      <button class="delivery-retry" type="button" onclick={() => confirming = true}>Recover feedback</button>
    {/if}
    {#if error}<ErrorLine {error} />{/if}
  </section>
{/if}

<style>
  .delivery { margin-top:var(--space-3); }
  .delivery blockquote { margin:var(--space-2) var(--space-3); padding-left:var(--space-3); border-left:1px solid var(--tone-secondary); color:var(--tone-secondary); white-space:pre-wrap; }
  .delivery-retry { border:0; padding:0; margin:var(--space-1) 0 0 var(--space-3); background:none; color:var(--tone-secondary); font:inherit; text-decoration:underline; text-underline-offset:2px; cursor:pointer; }
  .delivery-retry:hover { color:var(--tone-primary); }
  .delivery-actions { display:flex; justify-content:flex-end; gap:var(--space-2); margin-top:var(--space-2); }
</style>
