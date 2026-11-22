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

  /** How tall an opened output stands before it scrolls in its own block. */
  const OUTPUT_HEIGHT_CAP_LINES = 17;
  /** Outputs longer than this are drawn as one text run, not line by line. */
  const MARKED_LINES_MAXIMUM = 2_000;
  /** A line that says something broke leads with that word in the danger tone. */
  const PROBLEM_WORD = /\b(?:error|Error|ERROR|FAIL|FAILED|Failed|panic|fatal)\b/;

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
  let showAll = $state(false);
  let copied = $state(false);
  let outputLines = $derived((shownDetail ?? "").replace(/\n$/, "").split("\n"));
  let exitCode = $derived(presentation.outcome === "failed" ? /exit (?:code|status)[:\s]+(\d+)/i.exec(shownDetail ?? "")?.[1] ?? null : null);
  let outputLabel = $derived(
    exitCode !== null ? `Exit code ${exitCode}`
      : presentation.outcome === "failed" ? `Failed · ${lineCount(outputLines.length)}`
      : `Output · ${lineCount(outputLines.length)}`
  );
  let tall = $derived(outputLines.length > OUTPUT_HEIGHT_CAP_LINES);
  let markedLines = $derived(outputLines.length <= MARKED_LINES_MAXIMUM ? outputLines.map(markProblem) : null);

  function lineCount(count: number): string {
    return `${count} ${count === 1 ? "line" : "lines"}`;
  }

  function markProblem(line: string): { before: string; word: string; after: string } {
    const found = PROBLEM_WORD.exec(line);
    return found === null
      ? { before: line, word: "", after: "" }
      : { before: line.slice(0, found.index), word: found[0], after: line.slice(found.index + found[0].length) };
  }

  async function copyOutput(): Promise<void> {
    try {
      await navigator.clipboard.writeText(shownDetail ?? "");
      copied = true;
      setTimeout(() => (copied = false), 1_500);
    } catch {
      copied = false;
    }
  }

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
  <!-- v9 (1): a running call keeps its static icon and says "Running" in words; the
       Working line carries the one spinner in every view. -->
  <svg class="c2-tool-icon" viewBox="0 0 24 24" aria-hidden="true">
    {#each presentation.iconPaths as path (path)}<path d={path} />{/each}
  </svg>
  <!-- v4.1: the chevron stands right after the label; the exact subject (a command, a path)
       follows the chevron and takes whatever width is left, ending in an ellipsis inside the
       row, with the whole of it in its tooltip. -->
  <span class="acp-step-title c2-tool-line">
    <span class="c2-tool-verb">{presentation.title}</span>
  </span>
  <svg class="c2-tool-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>
  <span class="c2-tool-subject-slot">{#if presentation.summary}<span class="c2-tool-subject" class:c2-tool-subject--literal={presentation.summaryIsLiteral} data-conversation-tool-summary title={presentation.summaryInFull ?? presentation.summary}>{presentation.summary}</span>{/if}</span>
  <!-- How it went, in one column at the row's end, so a run of rows reads its outcomes
       straight down. A call that passed says nothing: passing is the ordinary case. -->
  <span class="c2-tool-outcome">
    {#if presentation.outcome === "failed"}
      <span class="c2-tool-state c2-tool-state--failed">Failed</span>
    {:else if presentation.outcome === "no_matches"}
      <span class="c2-tool-state" data-conversation-tool-no-matches>No matches</span>
    {:else if presentation.outcome === "completed"}
      <span class="c2-tool-state" role="img" aria-label="Completed"></span>
    {:else if activityUncertain}
      <span class="c2-tool-state">Unknown</span>
    {:else}
      <!-- The verb ("Running", #c4c4c4 500) says it runs; the Working line holds the spinner. -->
      <span class="c2-tool-state c2-tool-state--running" role="img" aria-label="Running"></span>
    {/if}
  </span>
{/snippet}

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
      {#if presentation.summaryInFull}<pre class="c2-tool-call" data-conversation-tool-call-in-full>{presentation.summaryInFull}</pre>{/if}
      {#if shownDetail || row.cappedDetailSequence !== null}
        <div class="c2-tool-block">
          <div class="c2-tool-block-head">
            <span class="c2-tool-block-label" class:c2-tool-block-label--failed={presentation.outcome === "failed"} data-conversation-tool-output-label>{outputLabel}</span>
            {#if tall}
              <button type="button" class="c2-tool-block-action" aria-pressed={showAll} onclick={() => (showAll = !showAll)}>{showAll ? "Show less" : "Show all"}</button>
            {/if}
            <button type="button" class="c2-tool-block-action" onclick={() => void copyOutput()} data-conversation-tool-copy>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                {#if copied}<path d="m5 12.5 4.5 4.5L19 7.5" />{:else}<rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />{/if}
              </svg>
              <span>{copied ? "Copied" : "Copy"}</span>
            </button>
          </div>
          <pre class="c2-tool-output" class:is-whole={showAll} data-conversation-tool-output>{#if markedLines !== null}{#each markedLines as line, index (index)}{line.before}{#if line.word}<span class="c2-tool-problem">{line.word}</span>{/if}{line.after}{index < markedLines.length - 1 ? "\n" : ""}{/each}{:else}{shownDetail ?? ""}{/if}</pre>
        </div>
      {:else}
        <p class="c2-tool-empty" data-conversation-tool-no-output>{presentation.outcome === "running" ? "No output yet." : "No output."}</p>
      {/if}
      {#if detailFailed}
        <p class="c2-tool-detail-failed" data-conversation-tool-detail-failed>
          {detailExpired ? DIAGNOSTIC_EXPIRED_MESSAGE : "The rest of this output did not load."}
        </p>
      {/if}
    </div>
  {/if}
</div>

<style>
  /* One line, 32 tall: the kind's glyph, the verb, the chevron right after it (v4.1), the
     exact subject, and how it went at the row's end. */
  .acp-step {
    display: grid;
    grid-template-columns: 16px minmax(0, max-content) 16px minmax(0, 1fr) minmax(72px, auto);
    align-items: center;
    column-gap: var(--cv-s2);
    width: 100%;
    min-width: 0;
    height: 32px;
    padding: 0 var(--cv-s2);
    border: 0;
    /* v7: a status line, 13/20: detail-only words after a 16px icon. */
    border-radius: 999px;
    background: transparent;
    color: var(--cv-detail-ink, #b0b0b0);
    font-family: var(--cv-font);
    font-size: 13px;
    line-height: 20px;
    letter-spacing: normal;
    text-align: start;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .acp-step:hover { background-color: #2a2a2a; }
  .acp-step:focus-visible {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: calc(var(--cv-focus-width) * -1);
  }
  /* v4.2: tool rows are quieter than prose: the icon #8c8c8c, the label #a3a3a3. */
  .c2-tool-icon {
    width: 16px;
    height: 16px;
    fill: none;
    stroke: var(--cv-quiet-ink, #bdbdbd);
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .c2-tool-line {
    display: flex;
    align-items: baseline;
    gap: 6px;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    color: inherit;
    font-size: inherit;
    letter-spacing: normal;
  }
  .c2-tool-verb { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--cv-detail-ink, #b0b0b0); font-weight: var(--cv-weight-body); }
  .c2-tool-subject-slot { display: flex; min-width: 0; overflow: hidden; white-space: nowrap; }
  .c2-tool-subject {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--cv-t3);
  }
  /* Only the exact thing the call acted on (a command, a path, a pattern) is set as code.
     Words about the call ("0 of 5 done") stay in the thread's own face. */
  /* Code in the row is set at the row's own size, so the line reads as one size. */
  .c2-tool-subject--literal { font-family: var(--cv-mono); font-size: var(--cv-type-meta); }
  .acp-step:hover .c2-tool-verb, .acp-step:focus-visible .c2-tool-verb { color: var(--cv-ink, #ececec); }
  /* p13b: a running call says so in words ("Running"-style verb, #ececec 500), not a spinner. */
  .acp-step[data-conversation-tool-status="running"] .c2-tool-verb { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }
  /* v14 (6): the live row's "Running" is #c4c4c4 500, not white, so only the Working line reads
     as the live status. Under the pointer it lights as every row does. */
  .acp-step[data-conversation-tool-status="running"] .c2-tool-verb { color: #c4c4c4; }
  .acp-step[data-conversation-tool-status="running"]:is(:hover, :focus-visible) .c2-tool-verb { color: var(--cv-ink, #ececec); }
  .c2-tool-outcome { display: flex; justify-content: flex-end; min-width: 0; }
  .c2-tool-state {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--cv-t4);
    font-size: var(--cv-type-caption);
    line-height: var(--cv-leading-caption);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .c2-tool-state--failed { color: var(--cv-danger); font-weight: var(--cv-weight-label); }
  .c2-tool-chevron {
    width: 16px;
    height: 16px;
    fill: none;
    stroke: var(--cv-quiet-ink, #bdbdbd);
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: stroke var(--cv-dur-fast) var(--cv-ease-out), transform var(--cv-dur-expand) var(--cv-ease-out);
  }
  .acp-step:hover .c2-tool-chevron,
  .acp-step:focus-visible .c2-tool-chevron,
  .c2-tool.is-open .c2-tool-chevron { stroke: var(--cv-ink, #ececec); }
  .c2-tool.is-open .c2-tool-chevron { transform: rotate(90deg); }
  /* The hover fill reaches past the text to the column's edge. */
  .c2-tool { min-width: 0; margin-inline: calc(var(--cv-s2) * -1); }
  /* What it printed: a block of its own, starting under the verb. */
  .c2-tool-body { padding: var(--cv-s1) var(--cv-s2) var(--cv-s2) 34px; }
  .c2-tool-block {
    overflow: hidden;
    border: 1px solid var(--cv-line);
    border-radius: var(--cv-r-lg);
    background: var(--cv-well);
  }
  .c2-tool-block-head {
    display: flex;
    align-items: center;
    gap: var(--cv-s1);
    height: 32px;
    padding: 0 var(--cv-s1) 0 14px;
  }
  .c2-tool-block-label {
    flex: 1;
    min-width: 0;
    color: var(--cv-t3);
    font-size: var(--cv-type-caption);
    line-height: var(--cv-leading-caption);
    font-weight: var(--cv-weight-label);
    font-variant-numeric: tabular-nums;
  }
  /* A failure's output says so in its header's words and tone, not with an edge. */
  .c2-tool-block-label--failed { color: var(--cv-danger); }
  .c2-tool-block-action {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 24px;
    padding: 0 var(--cv-s2);
    border: 0;
    border-radius: var(--cv-r-md);
    background: transparent;
    color: var(--cv-t3);
    font-family: var(--cv-font);
    font-size: var(--cv-type-caption);
    font-weight: var(--cv-weight-label);
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-tool-block-action:hover { background: var(--cv-control); color: var(--cv-t1); }
  .c2-tool-block-action:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: var(--cv-focus-offset); }
  .c2-tool-block-action svg {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  /* The cap is the point: an opened output scrolls inside its block rather than growing
     the thread by the length of whatever the tool happened to print. */
  .c2-tool-output {
    margin: 0;
    max-height: calc(var(--cv-leading-output) * 17 + 20px);
    overflow: auto;
    padding: 10px 14px;
    color: var(--cv-t2);
    font-family: var(--cv-mono);
    font-size: var(--cv-type-output);
    line-height: var(--cv-leading-output);
    white-space: pre;
    overflow-wrap: normal;
    scrollbar-width: thin;
    scrollbar-color: transparent transparent;
  }
  .c2-tool-output:hover { scrollbar-color: var(--cv-t4) transparent; }
  .c2-tool-output.is-whole { max-height: none; }
  .c2-tool-problem { color: var(--cv-danger); }
  /* The whole call, when its line had to shorten it: the exact thing, over what it printed. */
  .c2-tool-call {
    margin: 0 0 var(--cv-s2);
    color: var(--cv-t2);
    font-family: var(--cv-mono);
    font-size: var(--cv-type-output);
    line-height: var(--cv-leading-output);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .c2-tool-empty {
    margin: 0;
    color: var(--cv-t3);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
  }
  .c2-tool-detail-failed {
    margin: var(--cv-s1) 0 0;
    color: var(--cv-t3);
    font-size: var(--cv-type-caption);
  }
  @media (prefers-reduced-motion: reduce) {
    .acp-step, .c2-tool-chevron, .c2-tool-block-action { transition: none; }
  }
</style>
