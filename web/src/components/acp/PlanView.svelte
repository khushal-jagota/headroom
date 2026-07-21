<script lang="ts">
  import type { PlanEntry } from "@agentclientprotocol/sdk";
  import type { DeepReadonly } from "../../lib/acp/conversationState";

  let { entries }: { entries: readonly DeepReadonly<PlanEntry>[] } = $props();
</script>

<section class="acp-plan" aria-label="Current plan" data-acp-plan>
  <ol>
    {#each entries as entry}
      <li>
        <span class="acp-plan-state">{entry.status} · {entry.priority}</span>
        <span>{entry.content}</span>
      </li>
    {/each}
  </ol>
</section>

<style>
  .acp-plan {
    border-left: var(--border-hairline) solid var(--border-color);
    color: var(--text-muted);
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    padding-left: var(--space-3);
  }
  ol { display: grid; gap: var(--space-2); margin: 0; padding-left: var(--space-4); }
  li { padding-left: var(--space-1); }
  .acp-plan-state {
    color: var(--text-faint);
    display: block;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
</style>
