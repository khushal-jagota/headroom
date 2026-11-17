<script lang="ts">
  import ConversationComposer from "../conversation/ConversationComposer.svelte";
  import { readableConversationDetail } from "../../lib/conversation/conversationDetail";
  import {
    sentenceFromSubmitted,
    toPermissionAsk,
    toUserInput,
    type PreviewAsk
  } from "../../lib/asks/preview";
  import AskFrame from "./AskFrame.svelte";
  import AskTranscript from "./AskTranscript.svelte";
  import type { AskSurface } from "./askSurface";

  let { lines, asks, handlers }: AskSurface = $props();

  let live = $derived(asks[0] ?? null);
  let userInput = $derived(live ? toUserInput(live) : null);
  let permission = $derived(live ? toPermissionAsk(live) : null);

  function chipTitle(ask: PreviewAsk): string {
    if (ask.kind === "approval") return ask.questions[0]?.prompt ?? "Approval";
    if (ask.questions.length > 1) return `${ask.questions.length} questions`;
    return ask.questions[0]?.prompt ?? "Question";
  }

</script>

<AskFrame>
  {#snippet thread()}
    <AskTranscript {lines} />
    {#each asks as ask (ask.id)}
      {@const detail = ask.production === "permission" ? readableConversationDetail(ask.detail) : null}
      <div class="c2-ask-row" data-conversation-row={ask.production === "permission" ? "permission_ask" : "user_input"}>
        <div class="c2-label">{ask.kind === "approval" || ask.production === "permission" ? "permission" : "question"} · waiting for you</div>
        <div class="c2-ask-title">{chipTitle(ask)}</div>
        {#if detail}<pre class="c2-ask-detail">{detail}</pre>{/if}
      </div>
    {/each}
  {/snippet}
  {#snippet foot()}
    <ConversationComposer
      draftKey={live ? `ask-preview-${live.id}` : "ask-preview-idle"}
      showRunPicker={false}
      ask={permission}
      {userInput}
      placeholder="Message the agent..."
      onSend={async () => true}
      onAnswer={(optionId) => handlers.onCommitChoice(0, optionId)}
      onSubmitUserInput={(answers) => {
        if (live === null) return;
        handlers.onFinish(0, sentenceFromSubmitted(live, answers));
      }}
      onCancelTurn={() => handlers.onCancel(0)}
    />
  {/snippet}
</AskFrame>

<style>
  /* The transcript's record of an ask. The rules match ConversationTranscript;
     that component scopes them, so the preview repeats the values. */
  .c2-ask-row {
    align-self: stretch;
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-md);
    background: var(--accent-surface);
    color: var(--text-default);
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    overflow-wrap: anywhere;
  }
  .c2-label {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    margin-block-end: var(--space-1);
  }
  .c2-ask-title { color: var(--text-strong); }
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
