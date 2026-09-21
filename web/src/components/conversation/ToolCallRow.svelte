<script lang="ts">
  /** One tool call, as a line rather than a log.
   *
   * Closed, it says what was done, to what, and how it went — three things wide enough
   * to read at a glance. Its output is behind it, because a thread that pastes a whole
   * directory listing into the conversation is a file the owner has to scroll past to
   * find the reply. Opened, the output is capped and scrolls in place, so no single row
   * can push the rest of the conversation off the screen.
   *
   * Whether this row is open is not this row's to keep. A turn that ends puts its work
   * behind a fold, which destroys these rows, so the answer belongs to the thread and is
   * handed back when the row is drawn again.
   */
  import { presentToolCall } from "../../lib/conversation/toolCallPresentation";
  import type { ToolCallRow } from "../../lib/conversation/transcript";
  import type { SvelteMap, SvelteSet } from "svelte/reactivity";
  import { readToolCallDetail } from "../../lib/conversation/wire";

  let {
    row,
    conversationId,
    openRows,
    wholeDetails
  }: {
    row: ToolCallRow;
    conversationId: string;
    /** Which rows the reader has opened, by tool call id. Owned by the thread. */
    openRows: SvelteSet<string>;
    /** The whole output of every row the reader opened, so a row that is drawn again
     *  does not ask the server for text it already has. */
    wholeDetails: SvelteMap<string, string>;
  } = $props();

  let detailFailed = $state(false);
  /** One request at a time, however often the effect below is woken. */
  let asking = false;

  let open = $derived(openRows.has(row.toolCallId));
  let presentation = $derived(presentToolCall(row));
  let regionId = $derived(`c2-tool-${row.toolCallId}`);
  let shownDetail = $derived(wholeDetails.get(row.toolCallId) ?? presentation.detail);

  function openRow(): void {
    if (openRows.has(row.toolCallId)) {
      openRows.delete(row.toolCallId);
      return;
    }
    detailFailed = false;
    openRows.add(row.toolCallId);
  }

  // An open carries only the start of a long output. The rest is asked for when the row
  // is open and the thread does not hold it yet, which covers both the reader opening
  // this row and a row drawn again already open.
  $effect(() => {
    const at = row.cappedDetailSequence;
    const id = row.toolCallId;
    if (!open || at === null || wholeDetails.has(id) || detailFailed || asking) return;
    asking = true;
    void (async () => {
      try {
        const whole = await readToolCallDetail(conversationId, at);
        if (whole !== null) wholeDetails.set(id, whole);
      } catch {
        // The start of the output is still true and stays on screen. The row says only
        // that the rest of it did not arrive.
        detailFailed = true;
      } finally {
        asking = false;
      }
    })();
  });
</script>

{#snippet body()}
  <span class="acp-step-icon" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      {#each presentation.iconPaths as path}<path d={path} />{/each}
    </svg>
  </span>
  <span class="acp-step-title">{presentation.title}</span>
  {#if presentation.summary}
    <span class="c2-tool-summary" data-conversation-tool-summary>{presentation.summary}</span>
  {/if}
  {#if row.status === "completed"}
    <span class="acp-step-mark acp-mark-ok" role="img" aria-label="Completed">✓</span>
  {:else if row.status === "failed"}
    <span class="acp-step-mark acp-mark-fail" role="img" aria-label="Failed">✕</span>
  {:else}
    <span class="acp-spin" role="img" aria-label="Running"></span>
  {/if}
{/snippet}

{#if presentation.canExpand}
  <!-- The line and what it opens onto are one thing, not two. The wash sits out here so
       it covers both, and what opens sits inside the same shape rather than arriving as a
       second box below it. -->
  <div class="c2-tool" class:is-open={open}>
    <button
      type="button"
      class="acp-step acp-step--expandable"
      data-conversation-row="tool_call"
      data-conversation-tool={row.toolCallId}
      data-conversation-tool-status={row.status}
      aria-expanded={open}
      aria-controls={regionId}
      onclick={openRow}
    >{@render body()}</button>
    {#if open}
      <div id={regionId} class="c2-tool-body">
        <pre class="c2-tool-output" data-conversation-tool-output>{shownDetail ?? ""}</pre>
        {#if detailFailed}
          <p class="c2-tool-detail-failed" data-conversation-tool-detail-failed>
            The rest of this output did not load.
          </p>
        {/if}
      </div>
    {/if}
  </div>
{:else}
  <div
    class="acp-step"
    data-conversation-row="tool_call"
    data-conversation-tool={row.toolCallId}
    data-conversation-tool-status={row.status}
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
  .c2-tool:hover { background-image: var(--interaction-hover); }
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
    white-space: pre;
    overflow-wrap: normal;
  }
  .c2-tool-detail-failed {
    margin: var(--space-1) 0 0;
    color: var(--text-faintest);
    font-size: var(--type-xs);
  }
  @media (prefers-reduced-motion: reduce) {
    .c2-tool { transition: none; }
  }
</style>
