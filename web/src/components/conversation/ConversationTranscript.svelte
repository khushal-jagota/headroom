<script lang="ts">
  /** The thread: what was said, and — folded down to its size — what was done between.
   *
   * The prompt is the only bubble, the agent is bubble-less prose, and the work in
   * between is a work log rather than a run of lines. Text still arriving is drawn at the
   * end and disappears the moment its finished row lands.
   */
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import ConversationCredentialRequestCard from './ConversationCredentialRequestCard.svelte';
  import { MessageExits } from "../../lib/conversation/messageExits";
  import ViewItem from "./view/ViewItem.svelte";
  import UserMessage from "./UserMessage.svelte";
  import type { OutgoingMessage } from "../../lib/conversation/outgoing";
  import MessagePieces from "./MessagePieces.svelte";
  import MessageActions from "./MessageActions.svelte";
  import TurnAnchor from "./TurnAnchor.svelte";
  import WorkGroup from "./WorkGroup.svelte";
  import TranscriptStatusRow from "./TranscriptStatusRow.svelte";
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
    promptLabelFor,
    turnEndingStatus,
    PROMPT_DISCARDED_SENTENCE,
    TURN_STOPPED_STATUS,
    ASK_CARD_CONTEXT,
    askShownByItsCard,
    type AskCardReading
  } from "../../lib/conversation/transcript";
  import { getContext } from "svelte";
  import { modelDisplayName } from "../../lib/conversation/composer";
  import type { BackendModel } from "../../lib/conversation/wire";
  import { SvelteMap, SvelteSet } from "svelte/reactivity";

  let {
    activityUncertain = false,
    rows,
    visibleRows,
    lens,
    conversationId,
    models = [],
    ownSenderLabel = null,
    livenessPulse = 0,
    projectedItems = null,
    outgoingMessages = [], onStopDrawingMessage, onSendAgain, acknowledgedMessageIds = new Set<string>()
  }: {
    outgoingMessages?: readonly OutgoingMessage[];
    onStopDrawingMessage?: (id: string) => Promise<void> | void;
    onSendAgain?: (id: string) => Promise<void> | void;
    acknowledgedMessageIds?: ReadonlySet<string>;
    projectedItems?: readonly ThreadItem[] | null;
    activityUncertain?: boolean;
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

  const askCard = getContext<AskCardReading | undefined>(ASK_CARD_CONTEXT);
  let items = $derived<readonly ThreadItem[]>(projectedItems ?? conversationThreadItemsForLens(rows, visibleRows, lens));
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

  type Display = { key: string; item: ThreadItem | null; local: OutgoingMessage | null; afterKey: string | null };
  const exits = new MessageExits<Display>();
  let exitRevision = $state(0);
  let previousDisplay: Display[] = [];
  let localSlots: Display[] = [];
  let slotsConversation: string | null = null;
  const display = $derived.by(() => {
    exitRevision;
    if (slotsConversation !== conversationId) { localSlots = []; previousDisplay = []; slotsConversation = conversationId; }
    const locals = new Map(outgoingMessages.map(message => [message.displayMessageId ?? message.messageId, message]));
    const records = new Map(shown.flatMap(item => item.kind === 'row' && 'senderMessageId' in item.row && item.row.senderMessageId ? [[item.row.senderMessageId, item] as const] : []));
    const claimed = new Set<string>();
    const kept: Display[] = [];
    for (const old of localSlots) {
      const local = locals.get(old.key) ?? old.local!;
      const item = records.get(local.messageId) ?? null;
      if (!locals.has(old.key) && item === null && (old.item !== null || !acknowledgedMessageIds.has(local.messageId))) continue;
      if (item) claimed.add(item.key);
      kept.push({...old, local, item});
      locals.delete(old.key);
    }
    for (const [key, local] of locals) {
      const item = records.get(local.messageId) ?? null;
      if (item) claimed.add(item.key);
      kept.push({key,local,item,afterKey:shown.at(-1)?.key ?? null});
    }
    localSlots = kept;
    const result: Display[] = shown.filter(item=>!claimed.has(item.key)).map(item=>({key:`record:${item.key}`,item,local:null,afterKey:null}));
    for (const slot of [...kept].reverse()) {
      const position = slot.afterKey === null ? -1 : result.findIndex(entry=>entry.item?.key===slot.afterKey);
      result.splice(position + 1,0,slot);
    }
    const retained = exits.retain(result, previousDisplay, () => { exitRevision += 1; });
    localSlots = retained.filter(slot=>slot.local!==null);
    previousDisplay = retained;
    return retained;
  });

  function runValuesLine(model: string | null, reasoningEffort: string | null): string {
    const parts = [modelDisplayName(models, model) ?? "the backend's own model"];
    if (reasoningEffort) parts.push(reasoningEffort);
    return `now running on ${parts.join(" · ")}`;
  }

  function askStateLine(row: Extract<TranscriptRow, { kind: "permission_ask" }>): string {
    if (activityUncertain && row.state === "live") return "pending · availability on this machine unknown";
    if (row.answerFate === "saved") return "Answer saved at home; delivery to the machine is pending";
    if (row.answerFate === "unconfirmed") return "Answer was saved; application on the machine is unconfirmed";
    if (row.state === "answered") return `answered · ${row.answeredOptionLabel ?? ""}`;
    if (row.state === "dead") return askDeadSentence(row.deadReason);
    return "waiting for you";
  }

  function userInputStateLine(row: Extract<TranscriptRow, { kind: "user_input" }>): string {
    if (activityUncertain && row.state === "live") return "pending · availability on this machine unknown";
    if (row.answerFate === "saved") return "Answer saved at home; delivery to the machine is pending";
    if (row.answerFate === "unconfirmed") return "Answer was saved; application on the machine is unconfirmed";
    if (row.state === "answered") return "answered";
    if (row.state === "failed") return `failed · ${row.failureDetail ?? "the answer could not be delivered"}`;
    if (row.state === "dead") return askDeadSentence(row.deadReason);
    return "waiting for you";
  }
</script>

<div class="c2-transcript" data-conversation-transcript>
  {#each display as slot (slot.key)}
    {@const item = slot.item}
    {#if slot.local !== null}
      <ViewItem itemKey={item?.key ?? slot.key} displayKey={slot.key} registerDisplayNode={exits.register}>
      <UserMessage presentationRowKey={item?.key ?? null} row={item?.kind === 'row' && (item.row.kind === 'prompt' || item.row.kind === 'prompt_refused' || item.row.kind === 'prompt_uncertain' || item.row.kind === 'prompt_discarded') ? item.row : null} local={slot.local} {conversationId} {ownSenderLabel} {onStopDrawingMessage} {onSendAgain}/>
      </ViewItem>
    {:else if item !== null}
    {#if item.kind === "turn"}
      <TurnAnchor {activityUncertain}
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
      />
    {:else if item.kind === "work_group"}
      {#if lens === "full"}
      <WorkGroup {activityUncertain}
        openRows={openToolCallRows}
        wholeDetails={wholeToolCallDetails}
        entries={item.entries}
        hidden={lens !== "full" && item.settled && expandedTurns[item.turnKey] !== true}
        showAll={lens === "full"}
        {conversationId}
      />
      {/if}
    {:else if item.row.kind === "prompt" || item.row.kind === "prompt_refused" || item.row.kind === "prompt_uncertain" || item.row.kind === "prompt_discarded"}
      <UserMessage presentationRowKey={item.key} row={item.row} {conversationId} {ownSenderLabel}/>
    {:else if item.row.kind === "agent_message"}
      {@const toYou = lens === "full" && item.row.toOwner}
      <!-- In Full, where the agent's own narration sits beside them, the messages it
           addressed to the owner are marked as theirs. Focus shows little else. -->
      <article class="chat-a" class:chat-a--to-you={toYou} data-conversation-row="agent_message" data-conversation-to-owner={item.row.toOwner ? "" : undefined} data-conversation-source-key={item.row.key}>
        {#if toYou}<div class="c2-to-you">To you</div>{/if}
        <MessagePieces presentationRowKey={item.key} content={item.row.content} {conversationId} />
        <MessageActions content={item.row.content} createdAt={item.row.createdAt} />
      </article>
    {:else if item.row.kind === "streaming_agent_message"}
      <article class="chat-a c2-streaming" data-conversation-row="streaming">
        <MarkdownBlock text={item.row.text} />
      </article>
    {:else if item.row.kind === 'credential_request'}
      <ConversationCredentialRequestCard request={item.row.request} />
    {:else if (item.row.kind === "permission_ask" || item.row.kind === "user_input") && askShownByItsCard(item.row, activityUncertain,
      askCard?.saysAvailabilityUnknown(item.row.kind === "permission_ask" ? "permission" : "user_input", item.row.kind === "permission_ask" ? item.row.askId : item.row.requestId) ?? false)}
      <!-- Waiting: the card above the composer asks it, and the turn head says it waits. -->
    {:else if item.row.kind === "permission_ask"}
      {@const detail = readableConversationDetail(item.row.detail)}
      <div
        class="c2-ask-row"
        class:is-dead={item.row.state === "dead"}
        data-conversation-row="permission_ask"
        data-conversation-ask-state={item.row.state}
      >
        <div class="c2-ask-label">Permission · {askStateLine(item.row)}</div>
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
        <div class="c2-ask-label">Question · {userInputStateLine(item.row)}</div>
        {#if item.row.questions.length > 0}<div class="c2-ask-title">
          {item.row.questions.length === 1
            ? item.row.questions[0]?.question
            : `${item.row.questions.length} questions`}
        </div>{/if}
        {#if item.row.state === "answered" && item.row.answers}
          <div class="c2-ask-answers" data-conversation-user-input-answers>
            {#each item.row.questions as question (question.question_id)}
              <div>
                <span class="c2-ask-question">{question.header || question.question}</span>
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
    {:else if item.row.kind === "command_reply"}
      <!-- The backend answered a slash command itself. Its words are kept whole, quiet and
           in the tool voice, because they are the backend's output rather than the agent's. -->
      <p class="acp-command-reply" data-conversation-row="command_reply">{item.row.replyText}</p>
    {:else if item.row.kind === "turn_ended" && item.row.automaticCompactionResult === "not_compacted"}
      <div class="acp-compaction" role="status" data-conversation-row="turn_ended">
        {AUTOMATIC_COMPACTION_NOT_CONFIRMED_SENTENCE}
      </div>
    {:else if item.row.kind === "turn_ended" && turnEndingStatus(item.row.ending, item.row.errorSummary) !== null}
      <TranscriptStatusRow status={turnEndingStatus(item.row.ending, item.row.errorSummary)!} role={item.row.ending === "failed" ? "alert" : "status"} data-conversation-row="turn_ended"/>
    {:else if item.row.kind === "turn_stopped"}
      <TranscriptStatusRow status={TURN_STOPPED_STATUS} role="status" data-conversation-row="turn_stopped"/>
    {/if}
    {/if}
  {/each}
</div>

<style>
  .c2-transcript { display: contents; }
  .c2-streaming { color: var(--text-muted); }
  .chat-a { position: relative; }
  .chat-a--to-you {
    padding-inline-start: var(--space-3);
    border-inline-start: var(--border-strong) solid var(--accent-bright);
    color: var(--text-strong);
  }
  .c2-to-you {
    margin-block-end: var(--space-1);
    color: var(--accent-bright);
    font-family: var(--font-ui);
    font-size: var(--type-small);
    font-weight: var(--weight-title);
    line-height: 1.4;
  }
  /* An ask's record reads like the turn head beside it: the thread's own small voice,
     flush with the thread, no box. Only the command it asked about stays in mono. */
  .c2-ask-row {
    align-self: stretch;
    display: grid;
    gap: var(--space-1);
    min-width: 0;
    color: var(--text-default);
    font-family: var(--font-ui);
    font-size: var(--type-small);
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .c2-ask-label { color: var(--text-faintest); }
  .c2-ask-title { color: var(--text-muted); }
  .c2-ask-question { color: var(--text-muted); }
  .c2-ask-answers { color: var(--text-faintest); }
  .c2-ask-row.is-dead .c2-ask-title { color: var(--text-faintest); }
  /* Capped and scrolling: a transcript row never grows by the size of a payload. */
  .c2-ask-detail {
    margin: 0;
    max-height: calc(var(--type-xs) * 16);
    overflow: auto;
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    background: var(--surface-code);
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
