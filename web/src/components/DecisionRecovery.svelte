<script lang="ts">
  import { onMount } from "svelte";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import { decisionRecovery, dismissDecisionRecovery, DecisionRefusedError, recoverReviewDecision, savedReviewDecisions } from "../lib/review/decision";
  import type { SavedDecision } from "../lib/review/contracts";
  import { workspaceAddress } from "../lib/workspaceAddress";
  let error = $state<unknown>(null);
  async function recover(saved: SavedDecision): Promise<void> {
    error = null;
    try { await recoverReviewDecision(saved); }
    catch (failure) { if (!(failure instanceof DecisionRefusedError)) error = failure; }
  }
  onMount(() => {
    try { for (const saved of savedReviewDecisions()) void recover(saved); }
    catch (failure) { error = failure; }
  });
</script>

{#if error || $decisionRecovery.length > 0}
  <aside class="decision-recovery" aria-live="polite" data-decision-recovery>
    {#if error}<ErrorLine {error} />
      <Button variant="ghost" onclick={() => { error = null; }}>Dismiss</Button>
    {/if}
    {#each $decisionRecovery as entry (entry.saved.operationId)}
      <div class="decision-recovery-actions">
        {#if entry.terminal}<ErrorLine error={entry.error} />
        {:else}<span>{entry.busy ? "Checking decision…" : "Decision unconfirmed."}</span>{/if}
        <Button variant="ghost" href={workspaceAddress({ kind: "ticket", id: entry.saved.ticketId })}>Open Ticket</Button>
        {#if entry.terminal}<Button variant="ghost" onclick={() => dismissDecisionRecovery(entry.saved)}>Dismiss</Button>
        {:else if !entry.busy}<Button variant="secondary" onclick={() => void recover(entry.saved)}>Check decision</Button>{/if}
      </div>
    {/each}
  </aside>
{/if}

<style>
  .decision-recovery { padding:var(--space-3); background:var(--surface-2); }
  .decision-recovery-actions { display:flex; align-items:center; gap:var(--space-2); }
</style>
