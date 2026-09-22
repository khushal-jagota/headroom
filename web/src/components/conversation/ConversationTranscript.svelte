<script lang="ts">
  /** The thread: what was said, and — folded down to its size — what was done between.
   *
   * The prompt is the only bubble, the agent is bubble-less prose, and the work in
   * between is a work log rather than a run of lines. Text still arriving is drawn at the
   * end and disappears the moment its finished row lands.
   */
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import MessagePieces from "./MessagePieces.svelte";
  import TurnAnchor from "./TurnAnchor.svelte";
  import WorkGroup from "./WorkGroup.svelte";
  import type { TranscriptRow } from "../../lib/conversation/transcript";
  import { readableConversationDetail } from "../../lib/conversation/conversationDetail";
  import {
    type ThreadItem
  } from "../../lib/conversation/threadLayout";
  import {
    conversationThreadItemsForLens,
    type ConversationLens
  } from "../../lib/conversation/lens";
  import {
    AUTOMATIC_COMPACTION_NOT_CONFIRMED_SENTENCE,
    askDeadSentence,
    CONTEXT_COMPACTED_SENTENCE,
    explicitReplyMissingSentence,
    promptLabelFor,
    turnEndingSentence,
    PROMPT_DISCARDED_SENTENCE,
    TURN_STOPPED_SENTENCE
  } from "../../lib/conversation/transcript";
  import { modelDisplayName } from "../../lib/conversation/composer";
  import type { BackendModel } from "../../lib/conversation/wire";
  import { SvelteMap, SvelteSet } from "svelte/reactivity";

  let {
    rows,
    visibleRows,
    lens,
    conversationId,
    models = [],
    ownSenderLabel = null,
    livenessPulse = 0
  }: {
    rows: readonly TranscriptRow[];
    visibleRows: readonly TranscriptRow[];
    lens: ConversationLens;
    /** Which conversation these rows belong to, so a piece naming a file it kept has
     *  somewhere to fetch it from. */
    conversationId: string;
    models?: readonly BackendModel[];
    /** Moves whenever a live frame arrives, so a running turn can say it is alive. */
    livenessPulse?: number;
    /** The label this pane sends under. Messages carrying it are yours, and yours are
     *  not labelled — you know who wrote them. Everyone else's still are. */
    ownSenderLabel?: string | null;
  } = $props();

  let items = $derived<ThreadItem[]>(conversationThreadItemsForLens(rows, visibleRows, lens));
  // Opening a turn opens every run inside it, so there is one place to open a turn
  // rather than one per batch of tool calls in it.
  let expandedTurns = $state<Record<string, boolean>>({});
  // Which tool calls the reader opened, and the whole output each of them fetched. A
  // settling turn folds its work away, which destroys those rows, so the answer is kept
  // here — by the thread, which outlives every fold in it — and keyed by tool call id
  // rather than by where the row sat on the page.
  let openToolCallRows = new SvelteSet<string>();
  let wholeToolCallDetails = new SvelteMap<string, string>();
  // A settled turn reads as one paragraph you can open. Everything it said on the way to
  // that paragraph is dropped from the thread until its fold is opened, and comes back in
  // the place it happened rather than gathered up at the end.
  let shown = $derived(
    lens === "full" ? items : items.filter(
      (item) =>
        item.kind !== "row" ||
        item.behindTheFoldOf === null ||
        expandedTurns[item.behindTheFoldOf] === true
    )
  );

  function toggleTurn(turnKey: string): void {
    expandedTurns = { ...expandedTurns, [turnKey]: expandedTurns[turnKey] !== true };
  }

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

  function userInputStateLine(row: Extract<TranscriptRow, { kind: "user_input" }>): string {
    if (row.state === "answered") return "answered";
    if (row.state === "failed") return `failed · ${row.failureDetail ?? "the answer could not be delivered"}`;
    if (row.state === "dead") return askDeadSentence(row.deadReason);
    return "waiting for you";
  }
</script>

<div class="c2-transcript" data-conversation-transcript>
  {#each shown as item (item.key)}
    {#if item.kind === "turn"}
      <TurnAnchor
        settled={item.settled}
        stopped={item.stopped}
        startedAtUnixMilliseconds={item.startedAtUnixMilliseconds}
        ending={item.ending}
        isLatest={item.isLatest}
        durationSeconds={item.durationSeconds}
        toolCallCount={item.toolCallCount}
        foldedMessageCount={item.foldedMessageCount}
        expanded={lens === "full" || expandedTurns[item.turnKey] === true}
        {livenessPulse}
        onToggle={lens === "full" ? undefined : () => toggleTurn(item.turnKey)}
      />
    {:else if item.kind === "work_group"}
      <WorkGroup
        openRows={openToolCallRows}
        wholeDetails={wholeToolCallDetails}
        entries={item.entries}
        hidden={lens !== "full" && item.settled && expandedTurns[item.turnKey] !== true}
        showAll={lens === "full"}
        {conversationId}
      />
    {:else if item.row.kind === "prompt"}
      {@const label = promptLabelFor(item.row.senderLabel, ownSenderLabel)}
      {@const chip = modeChip(item.row.mode)}
      <!-- A message somebody else sent is a different kind of thing on the page, not the
           reader's own with a note attached. A thread is scanned by shape before it is
           read by label, so drawing the loop's prompts in the reader's own voice says
           "you said this" and only then quietly corrects itself. -->
      <article
        class={label === null ? "chat-u" : "chat-system"}
        data-conversation-row="prompt"
      >
        {#if label || chip}
          <div class="c2-label" data-conversation-prompt-label>
            {label ?? ""}{#if chip}<span class="c2-chip">{chip}</span>{/if}
          </div>
        {/if}
        <MessagePieces content={item.row.content} {conversationId} />
      </article>
    {:else if item.row.kind === "prompt_refused"}
      <article class="chat-system c2-refused" data-conversation-row="prompt_refused">
        <div class="c2-label">
          {promptLabelFor(item.row.senderLabel, ownSenderLabel) ?? "your message"} · not delivered · {item.row.sentence}
        </div>
        <MessagePieces content={item.row.content} {conversationId} />
      </article>
    {:else if item.row.kind === "prompt_uncertain"}
      <article class="chat-system c2-refused" data-conversation-row="prompt_uncertain">
        <div class="c2-label">
          {promptLabelFor(item.row.senderLabel, ownSenderLabel) ?? "your message"} · delivery uncertain · do not resend
        </div>
        <MessagePieces content={item.row.content} {conversationId} />
      </article>
    {:else if item.row.kind === "prompt_discarded"}
      <article class="chat-system" data-conversation-row="prompt_discarded">
        <div class="c2-label">
          {promptLabelFor(item.row.senderLabel, ownSenderLabel) ?? "your message"} · {PROMPT_DISCARDED_SENTENCE}
        </div>
        <MessagePieces content={item.row.content} {conversationId} />
      </article>
    {:else if item.row.kind === "agent_message"}
      <article class="chat-a" data-conversation-row="agent_message">
        <MessagePieces content={item.row.content} {conversationId} />
      </article>
    {:else if item.row.kind === "explicit_reply_missing"}
      <div class="acp-turn-end" data-conversation-row="explicit_reply_missing">
        {explicitReplyMissingSentence(item.row.promptSender)}
      </div>
    {:else if item.row.kind === "streaming_agent_message"}
      <article class="chat-a c2-streaming" data-conversation-row="streaming">
        <MarkdownBlock text={item.row.text} />
      </article>
    {:else if item.row.kind === "permission_ask"}
      {@const detail = readableConversationDetail(item.row.detail)}
      <div
        class="c2-ask-row"
        class:is-dead={item.row.state === "dead"}
        data-conversation-row="permission_ask"
        data-conversation-ask-state={item.row.state}
      >
        <div class="c2-label">permission · {askStateLine(item.row)}</div>
        <div class="c2-ask-title">{item.row.title}</div>
        {#if detail}<pre class="c2-ask-detail" data-conversation-ask-detail>{detail}</pre>{/if}
      </div>
    {:else if item.row.kind === "user_input"}
      <div
        class="c2-ask-row"
        class:is-dead={item.row.state === "dead" || item.row.state === "failed"}
        data-conversation-row="user_input"
        data-conversation-user-input-state={item.row.state}
      >
        <div class="c2-label">question · {userInputStateLine(item.row)}</div>
        {#if item.row.questions.length > 0}<div class="c2-ask-title">
          {item.row.questions.length === 1
            ? item.row.questions[0]?.question
            : `${item.row.questions.length} questions`}
        </div>{/if}
        {#if item.row.state === "answered" && item.row.answers}
          <div class="c2-ask-detail" data-conversation-user-input-answers>
            {#each item.row.questions as question (question.question_id)}
              <div>
                <strong>{question.header || question.question}</strong>
                <span> · {item.row.answers[question.question_id]?.answers.join(", ") ?? ""}</span>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {:else if item.row.kind === "model_changed"}
      <div class="acp-compaction" role="separator" data-conversation-row="model_changed">
        <span>{runValuesLine(item.row.model, item.row.reasoningEffort)}</span>
      </div>
    {:else if item.row.kind === "context_compacted"}
      <!-- The seam the old pane drew for the same thing, in the same stylesheet. What was
           before it has gone from the agent's memory, and a thread that just stopped
           mentioning it would read as an agent that forgot. -->
      <div class="acp-compaction" role="separator" data-conversation-row="context_compacted">
        <span>{CONTEXT_COMPACTED_SENTENCE}</span>
      </div>
    {:else if item.row.kind === "turn_ended" && item.row.automaticCompactionResult === "not_compacted"}
      <div class="acp-compaction" role="status" data-conversation-row="turn_ended">
        {AUTOMATIC_COMPACTION_NOT_CONFIRMED_SENTENCE}
      </div>
    {:else if item.row.kind === "turn_ended" && item.row.ending === "failed"}
      <div class="acp-turn-end acp-turn-end--error" role="alert" data-conversation-row="turn_ended">
        {turnEndingSentence(item.row.ending, item.row.errorSummary)}
      </div>
    {:else if item.row.kind === "turn_ended" && item.row.ending === "interrupted"}
      <div class="acp-turn-end" data-conversation-row="turn_ended">
        {turnEndingSentence(item.row.ending, item.row.errorSummary)}
      </div>
    {:else if item.row.kind === "turn_stopped"}
      <div class="acp-turn-end" data-conversation-row="turn_stopped">{TURN_STOPPED_SENTENCE}</div>
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
