<script lang="ts">
  /** One tool call, as a line rather than a log.
   *
   * Closed, it says what was done, to what, and how it went — three things wide enough
   * to read at a glance. Its output is behind it, because a thread that pastes a whole
   * directory listing into the conversation is a file the owner has to scroll past to
   * find the reply. Opened, the output is capped and scrolls in place, so no single row
   * can push the rest of the conversation off the screen.
   */
  import { stepIconPaths } from "../../lib/acp/stepIcons";
  import { readableDetail, toolGlyphKind } from "../../lib/conversation2/transcript";
  import type { ToolCallRow } from "../../lib/conversation2/transcript";

  let { row }: { row: ToolCallRow } = $props();

  let open = $state(false);

  let glyph = $derived(toolGlyphKind(row.toolKind));
  let iconPaths = $derived(stepIconPaths(glyph as never));
  let detail = $derived(readableDetail(row.progress ?? row.detail));
  // A one-line detail is the summary itself — the command that ran, the path that was
  // read — so it is shown beside the title rather than hidden behind it.
  let summary = $derived(
    detail !== null && !detail.includes("\n") && detail.length <= 120 ? detail : null
  );
  let expandable = $derived(detail !== null && summary === null);
  let regionId = $derived(`c2-tool-${row.toolCallId}`);
</script>

{#snippet body()}
  <span class="acp-step-icon" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      {#each iconPaths as path}<path d={path} />{/each}
    </svg>
  </span>
  <span class="acp-step-title">{row.title}</span>
  {#if summary}
    <span class="c2-tool-summary" data-conversation2-tool-summary>{summary}</span>
  {/if}
  {#if row.status === "completed"}
    <span class="acp-step-mark acp-mark-ok" role="img" aria-label="Completed">✓</span>
  {:else if row.status === "failed"}
    <span class="acp-step-mark acp-mark-fail" role="img" aria-label="Failed">✕</span>
  {:else}
    <span class="acp-spin" role="img" aria-label="Running"></span>
  {/if}
{/snippet}

{#if expandable}
  <button
    type="button"
    class="acp-step acp-step--expandable"
    data-conversation2-row="tool_call"
    data-conversation2-tool={row.toolCallId}
    data-conversation2-tool-status={row.status}
    aria-expanded={open}
    aria-controls={regionId}
    onclick={() => (open = !open)}
  >{@render body()}</button>
  {#if open}
    <div id={regionId} class="acp-step-detail">
      <pre class="c2-tool-output" data-conversation2-tool-output>{detail}</pre>
    </div>
  {/if}
{:else}
  <div
    class="acp-step"
    data-conversation2-row="tool_call"
    data-conversation2-tool={row.toolCallId}
    data-conversation2-tool-status={row.status}
  >{@render body()}</div>
{/if}

<style>
  .c2-tool-summary {
    min-width: 0;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
  /* The cap is the point: an expanded row scrolls its own output rather than growing
     the thread by the length of whatever the tool happened to print. */
  .c2-tool-output {
    margin: 0;
    max-height: calc(var(--type-xs) * 24);
    overflow: auto;
    padding: var(--space-2);
    border-radius: var(--radius-sm);
    background: var(--surface-sunken);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
