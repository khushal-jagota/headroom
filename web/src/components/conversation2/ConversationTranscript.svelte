<script lang="ts">
  /** The thread: what was said, and — folded down to its size — what was done between.
   *
   * The prompt is the only bubble, the agent is bubble-less prose, and the work in
   * between is a work log rather than a run of lines. Text still arriving is drawn at the
   * end and disappears the moment its finished row lands.
   */
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import WorkLog from "./WorkLog.svelte";
  import type { ThreadItem, TranscriptRow } from "../../lib/conversation2/transcript";
  import {
    askDeadSentence,
    promptLabelFor,
    readableDetail,
    threadItems,
    turnEndingSentence,
    TURN_STOPPED_SENTENCE
  } from "../../lib/conversation2/transcript";
  import { modelDisplayName } from "../../lib/conversation2/composer";
  import type { BackendModel } from "../../lib/conversation2/wire";

  let {
    rows,
    models = [],
    ownSenderLabel = null
  }: {
    rows: readonly TranscriptRow[];
    models?: readonly BackendModel[];
    /** The label this pane sends under. Messages carrying it are yours, and yours are
     *  not labelled — you know who wrote them. Everyone else's still are. */
    ownSenderLabel?: string | null;
  } = $props();

  let items = $derived<ThreadItem[]>(threadItems(rows));

  function modeChip(mode: string): string | null {
    if (mode === "send_now") return "sent now";
    if (mode === "steer") return "steered";
    return null;
  }

  function runValuesLine(model: string | null, reasoningEffort: string | null): string {
    const parts = [modelDisplayName(models, model) ?? "the backend's own model"];
    if (reasoningEffort) parts.push(reasoningEffort);
    return `now running on ${parts.join(" · ")}`;
  }

  function askStateLine(row: Extract<TranscriptRow, { kind: "permission_ask" }>): string {
    if (row.state === "answered") return `answered · ${row.answeredOptionLabel ?? ""}`;
    if (row.state === "dead") return askDeadSentence(row.deadReason);
    return "waiting for you";
  }
</script>

<div class="c2-transcript" data-conversation2-transcript>
  {#each items as item (item.key)}
    {#if item.kind === "work"}
      <WorkLog
        entries={item.entries}
        settled={item.settled}
        durationSeconds={item.durationSeconds}
      />
    {:else if item.row.kind === "prompt"}
      {@const label = promptLabelFor(item.row.senderLabel, ownSenderLabel)}
      {@const chip = modeChip(item.row.mode)}
      <article class="chat-u" data-conversation2-row="prompt">
        {#if label || chip}
          <div class="c2-label" data-conversation2-prompt-label>
            {label ?? ""}{#if chip}<span class="c2-chip">{chip}</span>{/if}
          </div>
        {/if}
        {item.row.text}
      </article>
    {:else if item.row.kind === "prompt_refused"}
      <article class="chat-system c2-refused" data-conversation2-row="prompt_refused">
        <div class="c2-label">
          {promptLabelFor(item.row.senderLabel, ownSenderLabel) ?? "your message"} · not delivered · {item.row.sentence}
        </div>
        {item.row.text}
      </article>
    {:else if item.row.kind === "prompt_discarded"}
      <article class="chat-system" data-conversation2-row="prompt_discarded">
        <div class="c2-label">
          {promptLabelFor(item.row.senderLabel, ownSenderLabel) ?? "your message"} · discarded without being delivered
        </div>
        {item.row.text}
      </article>
    {:else if item.row.kind === "agent_message"}
      <article class="chat-a" data-conversation2-row="agent_message">
        <MarkdownBlock text={item.row.text} />
      </article>
    {:else if item.row.kind === "streaming_agent_message"}
      <article class="chat-a c2-streaming" data-conversation2-row="streaming">
        <MarkdownBlock text={item.row.text} />
      </article>
    {:else if item.row.kind === "permission_ask"}
      {@const detail = readableDetail(item.row.detail)}
      <div
        class="c2-ask-row"
        class:is-dead={item.row.state === "dead"}
        data-conversation2-row="permission_ask"
        data-conversation2-ask-state={item.row.state}
      >
        <div class="c2-label">permission · {askStateLine(item.row)}</div>
        <div class="c2-ask-title">{item.row.title}</div>
        {#if detail}<pre class="c2-ask-detail" data-conversation2-ask-detail>{detail}</pre>{/if}
      </div>
    {:else if item.row.kind === "model_changed"}
      <div class="acp-compaction" role="separator" data-conversation2-row="model_changed">
        <span>{runValuesLine(item.row.model, item.row.reasoningEffort)}</span>
      </div>
    {:else if item.row.kind === "turn_ended" && item.row.ending === "failed"}
      <div class="acp-turn-end acp-turn-end--error" role="alert" data-conversation2-row="turn_ended">
        {turnEndingSentence(item.row.ending, item.row.errorSummary)}
      </div>
    {:else if item.row.kind === "turn_ended" && item.row.ending === "interrupted"}
      <div class="acp-turn-end" data-conversation2-row="turn_ended">
        {turnEndingSentence(item.row.ending, item.row.errorSummary)}
      </div>
    {:else if item.row.kind === "turn_stopped"}
      <div class="acp-turn-end" data-conversation2-row="turn_stopped">{TURN_STOPPED_SENTENCE}</div>
    {/if}
  {/each}
</div>

<style>
  .c2-transcript { display: contents; }
  .c2-label {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    margin-block-end: var(--space-1);
  }
  .c2-chip {
    margin-inline-start: var(--space-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-pill);
    color: var(--accent-bright);
    padding: 0 var(--space-2);
  }
  .c2-refused .c2-label { color: var(--accent-error); }
  .c2-streaming { color: var(--text-muted); }
  .c2-ask-row {
    align-self: stretch;
    border-inline-start: var(--border-hairline) solid var(--accent-bright);
    padding-inline-start: var(--space-3);
    color: var(--text-default);
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    overflow-wrap: anywhere;
  }
  .c2-ask-row.is-dead {
    border-inline-start-color: var(--text-faintest);
    color: var(--text-faintest);
  }
  .c2-ask-row.is-dead .c2-label { color: var(--text-faintest); }
  .c2-ask-title { color: var(--text-strong); }
  .c2-ask-row.is-dead .c2-ask-title { color: var(--text-faint); }
  /* Capped and scrolling: a transcript row never grows by the size of a payload. */
  .c2-ask-detail {
    margin: var(--space-1) 0 0;
    max-height: calc(var(--type-xs) * 16);
    overflow: auto;
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
