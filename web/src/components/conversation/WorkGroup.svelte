<script lang="ts">
  /** One unbroken run of tool calls, sitting where it happened.
   *
   * The run is what the agent did between two things it said, so it stays between them.
   * Closed — which is how it starts — it is one quiet line that says what the run did, or
   * the call that is running now. Opened, it is the run's rows, each still opening onto
   * what it printed. A run of one is its own row: a summary of one call says less than
   * the call's own line does.
   */
  import ToolCallRow from "./ToolCallRow.svelte";
  import ToolRunSummary from "./ToolRunSummary.svelte";
  import { summarizeToolRun } from "../../lib/conversation/toolCallPresentation";
  import type { ToolCallRow as ToolCallRowType } from "../../lib/conversation/transcript";
  import type { SvelteMap, SvelteSet } from "svelte/reactivity";

  let {
    entries,
    conversationId,
    hidden = false,
    activityUncertain = false,
    openRows,
    wholeDetails
  }: {
    entries: readonly ToolCallRowType[];
    /** Whose record these rows are, which is where a row's whole output is asked for. */
    conversationId: string;
    /** The reading does not show tool calls at all (Focus). */
    hidden?: boolean;
    activityUncertain?: boolean;
    /** Which rows are open, and what they fetched. Both belong to the thread. */
    openRows: SvelteSet<string>;
    wholeDetails: SvelteMap<string, string>;
  } = $props();

  let expanded = $state(false);
  let summary = $derived(summarizeToolRun(entries));
  let regionId = $derived(`c2-run-${entries[0]?.toolCallId ?? "empty"}`);
</script>

{#if !hidden && entries.length > 0}
  <div
    class="c2-run"
    data-conversation-work-group
    data-conversation-work-expanded={expanded ? "true" : "false"}
  >
    {#if entries.length > 1}
      <ToolRunSummary {summary} callCount={entries.length} open={expanded} controls={expanded ? regionId : undefined} {activityUncertain} onToggle={() => (expanded = !expanded)} />
    {/if}
    {#if entries.length === 1 || expanded}
      <div class="acp-steps c2-run-rows" id={regionId} data-conversation-work-entries>
        {#each entries as entry (entry.key)}
          <ToolCallRow row={entry} {conversationId} {openRows} {wholeDetails} {activityUncertain} />
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .c2-run { display: flex; flex-direction: column; min-width: 0; max-width: 100%; }
  /* Rows stack with no gap: each is 32 tall and its hover fill is its own edge. */
  .c2-run-rows { display: flex; flex-direction: column; gap: 0; min-width: 0; }
</style>
