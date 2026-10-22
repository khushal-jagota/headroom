<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchJson } from "../lib/api";
  import { interruptionHref, recoveryLabel } from "../lib/deploymentInterruptions";
  import type { InterruptionEntry } from "../lib/deploymentInterruptions";
  import { queries } from "../lib/queryCatalogue";
  import { queryClient } from "../lib/queryClient";

  let { conversationId = null }: { conversationId?: string | null } = $props();
  const summary = createQuery(() => queries.deploymentInterruptions(), () => queryClient);
  const accounts = $derived(summary.data?.accounts ?? []);
  const entries = $derived(accounts.flatMap(account => account.entries)
    .filter(entry => conversationId === null || entry.conversation_id === conversationId));
  const unavailable = $derived(accounts.filter(account => account.capture_state === "unavailable"));
  let pending = $state<string | null>(null);
  let actionMessage = $state<string | null>(null);
  async function restart(entry: InterruptionEntry) {
    pending = entry.interruption_id;
    actionMessage = null;
    try {
      const result = await fetchJson<{ started: boolean; delivery_fate: string | null; not_started_because: string | null }>(
        `/api/tickets/${encodeURIComponent(entry.owner_id!)}/restart-worker`, { method: "POST", body: {} });
      actionMessage = result.started ? "Restart requested. The account confirms when the agent resumes."
        : result.delivery_fate === "queued" ? "Restart queued. The agent has not resumed yet."
        : result.not_started_because ?? "The restart did not confirm resumption. Check the conversation.";
    } catch (error) {
      actionMessage = error instanceof Error ? error.message : "The restart failed.";
    } finally {
      pending = null;
      await queryClient.invalidateQueries({ queryKey: ["deployment-interruptions"] });
    }
  }
</script>

{#snippet entryList()}
  <ul>
    {#each entries as entry (entry.interruption_id)}
      <li data-interruption-entry>
        <strong>{entry.display_name}</strong>
        <span class="recovery-state">{recoveryLabel[entry.recovery_state]}</span>
        <p>{entry.detail}</p>
        <div class="actions">
          {#if interruptionHref(entry)}<a href={interruptionHref(entry)!}>{entry.recovery_state === "resumed" || entry.recovery_state === "closed" ? "Open workspace · conversation history" : "Open conversation"}</a>{/if}
          {#if entry.can_restart}
            <button type="button" disabled={pending !== null} onclick={() => restart(entry)}>
              {pending === entry.interruption_id ? "Restart requested…" : "Restart Worker"}
            </button>
          {/if}
        </div>
      </li>
    {/each}
  </ul>
{/snippet}

{#if conversationId !== null}
  {#if entries.length}
    <aside class="interruption-notice" aria-label="Deployment interruption" data-conversation-interruption>
      <p>A deploy interrupted this conversation.</p>
      {@render entryList()}
    </aside>
  {/if}
{:else if entries.length || unavailable.length || summary.isError}
  <details class="interruption-notice" data-deployment-interruptions>
    <summary>{entries.length ? `${entries.length} interrupted ${entries.length === 1 ? "session" : "sessions"}` : "Interruption account unavailable"}</summary>
    {#each unavailable as account}
      <p>Interruption account unavailable. {account.detail ?? "The deploy did not supply reliable evidence."}</p>
    {/each}
    {#if summary.isError}<p>Cannot read the interruption account.</p><button type="button" onclick={() => summary.refetch()}>Try again</button>{/if}
    {@render entryList()}
  </details>
{/if}
{#if actionMessage}<p class="action-message" role="status">{actionMessage}</p>{/if}

<style>
  .interruption-notice { margin-bottom: var(--space-2); padding: var(--space-2); border-radius: var(--radius-sm); background: var(--accent-surface); color: var(--text-strong); font-size: var(--type-xs); }
  summary { cursor: pointer; }
  ul { list-style: none; padding: 0; margin: var(--space-2) 0 0; max-height: 40vh; overflow-y: auto; }
  li + li { margin-top: var(--space-3); }
  strong, .recovery-state { display: block; }
  .recovery-state { color: var(--text-muted); }
  p { margin: var(--space-1) 0; }
  .actions { display: flex; gap: var(--space-2); align-items: center; flex-wrap: wrap; }
  button, a { font: inherit; color: var(--accent-text); }
  button { cursor: pointer; padding: var(--space-1) var(--space-2); border: 0; border-radius: var(--radius-sm); background: var(--accent-surface); }
  button:disabled { cursor: wait; opacity: .6; }
  .action-message { font-size: var(--type-xs); }
</style>
