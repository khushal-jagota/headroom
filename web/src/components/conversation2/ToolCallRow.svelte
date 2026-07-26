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
  import {
    lineShowsWholeDetail,
    readableDetail,
    toolCallLine,
    toolGlyphKind
  } from "../../lib/conversation2/transcript";
  import type { ToolCallRow } from "../../lib/conversation2/transcript";

  let { row }: { row: ToolCallRow } = $props();

  let open = $state(false);

  let glyph = $derived(toolGlyphKind(row.toolKind));
  let iconPaths = $derived(stepIconPaths(glyph as never));
  // What happened, and which call it was. Both are read out of what the row already
  // holds, so a conversation recorded before this existed reads the same way.
  let line = $derived(toolCallLine(row));
  let detail = $derived(readableDetail(row.progress ?? row.detail));
  let expandable = $derived(detail !== null && !lineShowsWholeDetail(line, detail));
  let regionId = $derived(`c2-tool-${row.toolCallId}`);
</script>

{#snippet body()}
  <span class="acp-step-icon" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      {#each iconPaths as path}<path d={path} />{/each}
    </svg>
  </span>
  <span class="acp-step-title">{line.title}</span>
  {#if line.summary}
    <span class="c2-tool-summary" data-conversation2-tool-summary>{line.summary}</span>
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
  <!-- The line and what it opens onto are one thing, not two. The wash sits out here so
       it covers both, and what opens sits inside the same shape rather than arriving as a
       second box below it. -->
  <div class="c2-tool" class:is-open={open}>
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
      <div id={regionId} class="c2-tool-body">
        <pre class="c2-tool-output" data-conversation2-tool-output>{detail}</pre>
      </div>
    {/if}
  </div>
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
  /* One shape for the line and what it opens onto, so opening a row grows it rather than
     putting a second thing underneath it. The wash is on the whole shape, including the
     part that is only there when it is open. */
  .c2-tool {
    border-radius: var(--radius-sm);
    /* The wash reaches past the text to the edges the thread's other rows sit at. */
    margin-inline: calc(var(--space-2) * -1);
    padding-inline: var(--space-2);
    transition: background var(--motion-fast) var(--motion-ease);
  }
  .c2-tool:hover { background: var(--surface-overlay); }
  /* What happened is the brighter half of the line; which call it was is the quieter one. */
  .c2-tool :global(.acp-step-title) { color: var(--text-default); }
  /* Indented past the icon so it hangs off the line it belongs to, behind a rule rather
     than in a box — the box is what made it read as a separate thing. */
  .c2-tool-body {
    margin-inline-start: calc(var(--space-4) + var(--space-2));
    padding: 0 0 var(--space-2) var(--space-3);
    border-inline-start: var(--border-hairline) solid var(--border-color);
  }
  /* The cap is the point: an expanded row scrolls its own output rather than growing
     the thread by the length of whatever the tool happened to print. */
  .c2-tool-output {
    margin: 0;
    max-height: calc(var(--type-xs) * 24);
    overflow: auto;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  @media (prefers-reduced-motion: reduce) {
    .c2-tool { transition: none; }
  }
</style>
