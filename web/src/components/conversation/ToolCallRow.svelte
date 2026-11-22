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
  import { DiagnosticExpiredError, DIAGNOSTIC_EXPIRED_MESSAGE } from "../../lib/conversation/diagnosticExpiry";
  import { readToolCallDetail } from "../../lib/conversation/wire";

  let {
    row,
    conversationId,
    activityUncertain = false,
    openRows,
    wholeDetails
  }: {
    row: ToolCallRow;
    conversationId: string;
    activityUncertain?: boolean;
    /** Which rows the reader has opened, by tool call id. Owned by the thread. */
    openRows: SvelteSet<string>;
    /** The whole output of every row the reader opened, so a row that is drawn again
     *  does not ask the server for text it already has. */
    wholeDetails: SvelteMap<string, string>;
  } = $props();

  let detailFailed = $state(false);
  let detailExpired = $state(false);
  /** One request at a time, however often the effect below is woken. */
  let selectedDetail = $state<string|null>(null);

  let open = $derived(openRows.has(row.toolCallId));
  let presentation = $derived(presentToolCall(row));
  let regionId = $derived(`c2-tool-${row.toolCallId}`);
  let shownDetail = $derived(selectedDetail ?? wholeDetails.get(row.toolCallId) ?? presentation.detail);

  function openRow(): void {
    if (openRows.has(row.toolCallId)) {
      openRows.delete(row.toolCallId);
      return;
    }
    detailFailed = false;
    detailExpired = false;
    openRows.add(row.toolCallId);
  }

  // An open carries only the start of a long output. The rest is asked for when the row
  // is open and the thread does not hold it yet, which covers both the reader opening
  // this row and a row drawn again already open.
  $effect(() => {
    const at = row.cappedDetailSequence;
    const id = row.toolCallId;
    if (!open || at === null || wholeDetails.has(id) || detailFailed) return;
    const controller=new AbortController();
    void readToolCallDetail(conversationId,at,controller.signal).then(whole=>{
      if(controller.signal.aborted||whole===null)return;
      selectedDetail=whole;
      if(whole.length*2<=512*1024)wholeDetails.set(id,whole);
    }).catch(reason=>{if(!controller.signal.aborted){detailFailed=true;detailExpired=reason instanceof DiagnosticExpiredError;}});
    return()=>{controller.abort();selectedDetail=null;};
  });
</script>

{#snippet body()}
  <svg class="c2-tool-icon" viewBox="0 0 24 24" aria-hidden="true">
    {#each presentation.iconPaths as path (path)}<path d={path} />{/each}
  </svg>
  <!-- The verb never gives way; the subject takes whatever width is left and ends in an
       ellipsis inside the row, with the whole of it in its tooltip. -->
  <span class="acp-step-title c2-tool-line">
    <span class="c2-tool-verb">{presentation.title}</span>{#if presentation.summary}<span class="c2-tool-subject" data-conversation-tool-summary title={presentation.summaryInFull ?? presentation.summary}>{presentation.summary}</span>{/if}
  </span>
  <!-- How it went, in one column at the row's end, so a run of rows reads its outcomes
       straight down rather than wherever each line happened to stop. A word that says the
       outcome comes before its mark, so the marks themselves stay in the column. -->
  <span class="c2-tool-outcome">
    {#if presentation.outcome === "completed"}
      <svg class="c2-tool-mark c2-tool-mark--ok" viewBox="0 0 24 24" role="img" aria-label="Completed"><path d="m5 12.5 4.5 4.5L19 7.5" /></svg>
    {:else if presentation.outcome === "failed"}
      <span class="c2-tool-state c2-tool-state--failed">
        <span>Failed</span>
        <svg class="c2-tool-mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17" /></svg>
      </span>
    {:else if presentation.outcome === "no_matches"}
      <span class="c2-tool-state" data-conversation-tool-no-matches>No matches</span>
    {:else if activityUncertain}
      <span class="c2-tool-state">activity unknown</span>
    {:else}
      <span class="acp-spin c2-tool-spin" role="img" aria-label="Running"></span>
    {/if}
  </span>
  <!-- The chevron's column is kept on every row, open-able or not, so the outcomes above
       and below it stand in the same place. -->
  {#if presentation.canExpand}
    <svg class="c2-tool-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
  {:else}
    <span class="c2-tool-chevron-slot" aria-hidden="true"></span>
  {/if}
{/snippet}

{#if presentation.canExpand}
  <!-- The line and what it opens onto are one thing, not two. The wash sits out here so
       it covers both, and what opens sits inside the same shape rather than arriving as a
       second box below it. -->
  <div class="c2-tool" class:is-open={open} class:is-failed={presentation.outcome === "failed"}>
    <button
      type="button"
      class="acp-step acp-step--expandable"
      data-conversation-row="tool_call"
      data-conversation-tool={row.toolCallId}
      data-conversation-tool-status={row.status}
      data-conversation-tool-outcome={presentation.outcome}
      aria-expanded={open}
      aria-controls={regionId}
      onclick={openRow}
    >{@render body()}</button>
    {#if open}
      <div id={regionId} class="c2-tool-body">
        <pre class="c2-tool-output" data-conversation-tool-output>{shownDetail ?? ""}</pre>
        {#if detailFailed}
          <p class="c2-tool-detail-failed" data-conversation-tool-detail-failed>
            {detailExpired ? DIAGNOSTIC_EXPIRED_MESSAGE : "The rest of this output did not load."}
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
    data-conversation-tool-outcome={presentation.outcome}
  >{@render body()}</div>
{/if}

<style>
  /* One line: the kind's glyph, what was done in words, which call it was in the tool
     voice, and how it went. The words carry the reading; the mono subject is the exact
     thing, set one step quieter so a run of rows scans by its verbs. */
  .acp-step {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
    min-height: 26px;
    padding: var(--space-1) 0;
    color: var(--text-muted);
    font-family: var(--font-ui);
    font-size: var(--type-small);
    letter-spacing: normal;
    text-align: start;
  }
  .c2-tool-icon {
    flex: none;
    width: 14px;
    height: 14px;
    fill: none;
    stroke: var(--conversation-mark-quiet);
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .acp-step { max-width: 100%; }
  .c2-tool-line {
    flex: 0 1 auto;
    display: inline-flex;
    align-items: baseline;
    gap: 0.35em;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    /* The line takes what it needs and the outcome column takes the row's end. */
    margin-inline-end: auto;
    color: inherit;
    font-size: inherit;
    letter-spacing: normal;
  }
  .c2-tool-verb { flex: none; color: var(--text-muted); }
  .c2-tool-subject {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
  }
  .c2-tool-outcome {
    flex: none;
    display: inline-flex;
    align-items: center;
    justify-content: flex-end;
    min-inline-size: 14px;
  }
  .c2-tool-mark {
    flex: none;
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .c2-tool-mark--ok { color: var(--accent-done); }
  .c2-tool-state {
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    color: var(--text-faintest);
    font-size: var(--type-small);
    white-space: nowrap;
  }
  .c2-tool-state--failed { color: var(--accent-error); }
  .c2-tool-spin { margin: 0; }
  /* What opens the row, said where the line ends: there at rest in the quiet mark tone, so
     a row that opens says so before it is pointed at, and brighter when pointed at,
     focused or open. A row that does not open keeps the column empty. */
  .c2-tool-chevron,
  .c2-tool-chevron-slot {
    flex: none;
    width: 14px;
    height: 14px;
  }
  .c2-tool-chevron {
    fill: none;
    stroke: var(--conversation-mark-quiet);
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: stroke var(--motion-fast) var(--motion-ease), transform var(--motion-fast) var(--motion-ease);
  }
  .c2-tool:hover .c2-tool-chevron,
  .acp-step:focus-visible .c2-tool-chevron,
  .c2-tool.is-open .c2-tool-chevron { stroke: var(--text-muted); }
  .c2-tool.is-open .c2-tool-chevron { transform: rotate(90deg); }
  .acp-step--expandable { width: 100%; background: transparent; border: 0; cursor: pointer; }
  .acp-step--expandable:hover .c2-tool-verb { color: var(--text-strong); }
  .acp-step--expandable:hover .c2-tool-subject { color: var(--text-muted); }
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
  /* Pointed at, the row takes the app's one hover wash (the rest bar's and the utility
     buttons'), a step that reads on the dark page rather than a 6% tint that barely did. */
  .c2-tool:hover { background-color: var(--wash-hover); }
  /* The output sits in its own recessed well under the line, so it reads as what the call
     printed rather than as more of the conversation. */
  .c2-tool-body {
    padding: 0 0 var(--space-2) calc(14px + var(--space-2));
  }
  /* The cap is the point: an expanded row scrolls its own output rather than growing
     the thread by the length of whatever the tool happened to print. */
  .c2-tool-output {
    margin: 0;
    max-height: calc(var(--type-xs) * 24);
    overflow: auto;
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    background: var(--surface-recessed);
    border-inline-start: 2px solid var(--border-raised);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    line-height: 1.55;
    white-space: pre;
    overflow-wrap: normal;
  }
  /* A failure's output is the reason it failed: tinted and edged in the error colour so
     it reads as the problem, not as one more listing. */
  .c2-tool.is-failed .c2-tool-output {
    background: color-mix(in srgb, var(--error-surface) 55%, var(--surface-recessed));
    border-inline-start-color: var(--accent-error);
    color: var(--text-default);
  }
  .c2-tool-detail-failed {
    margin: var(--space-1) 0 0;
    color: var(--text-faintest);
    font-size: var(--type-xs);
  }
  @media (prefers-reduced-motion: reduce) {
    .c2-tool, .c2-tool-chevron { transition: none; }
  }
</style>
