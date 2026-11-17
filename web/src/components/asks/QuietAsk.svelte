<script lang="ts">
  import {
    cursorChoices,
    questionAt,
    type AskDraft,
    type PreviewAsk
  } from "../../lib/asks/preview";
  import AskCommit from "./AskCommit.svelte";
  import AskFrame from "./AskFrame.svelte";
  import AskTranscript from "./AskTranscript.svelte";
  import IdleComposer from "./IdleComposer.svelte";
  import type { AskSurface } from "./askSurface";

  let { lines, asks, drafts, cursors, active, handlers }: AskSurface = $props();

  function question(ask: PreviewAsk, draft: AskDraft) {
    return questionAt(ask, draft);
  }
</script>

<AskFrame>
  {#snippet thread()}
    <AskTranscript {lines} />
    {#each asks as ask, index (ask.id)}
      {@const draft = drafts[index]}
      {@const current = question(ask, draft)}
      {@const choices = cursorChoices(ask, draft)}
      <!-- The group catches arrows and digits that are not already a button's own click. -->
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <section
        class="quiet"
        role="group"
        data-ask-index={index}
        data-ask-live={index === active ? "true" : undefined}
        tabindex="-1"
        aria-label={ask.kind === "approval" ? "Approval" : "Question"}
        onfocusin={() => handlers.onActivate(index)}
        onkeydown={(event) => handlers.onKey(event, index)}
      >
        <p class="kicker">
          {#if ask.kind === "approval"}{ask.stage}{:else}{current?.header || "Question"}{/if}
          {#if ask.kind === "question" && ask.questions.length > 1}
            <span>{draft.step + 1} of {ask.questions.length}</span>
          {/if}
        </p>
        <p class="prompt">{ask.kind === "approval" ? ask.questions[0]?.prompt : current?.prompt}</p>
        {#if ask.detail}<div class="detail">{ask.detail}</div>{/if}
        {#if current?.multi}<p class="hint">Choose any that apply.</p>{/if}

        {#if choices.length > 0 && ask.kind === "question"}
          <div class="choices" role={current?.multi ? "group" : "radiogroup"} aria-label="Answers">
            {#each choices as choice, position (choice.id)}
              {@const selected = (draft.selected[current?.id ?? ""] ?? []).includes(choice.id)}
              <button
                type="button"
                class="choice"
                role={current?.multi ? "checkbox" : "radio"}
                aria-checked={selected}
                data-ask-choice={choice.id}
                data-ask-position={position}
                data-ask-cursor={cursors[index] === position ? "true" : undefined}
                onclick={() => current?.multi ? handlers.onToggle(index, choice.id) : handlers.onCommitChoice(index, choice.id)}
              >
                <span>{choice.label}</span>
                {#if choice.description}<small>{choice.description}</small>{/if}
              </button>
            {/each}
          </div>
        {/if}

        {#if current?.allowOther}
          <label class="field">
            <span>Other</span>
            <input
              value={draft.other[current.id] ?? ""}
              placeholder="Type another answer"
              oninput={(event) => handlers.onOther(index, event.currentTarget.value)}
            />
          </label>
        {/if}
        {#if current?.freeText}
          <textarea
            class="writing"
            rows="3"
            placeholder="Write the answer"
            aria-label={current.prompt}
            value={draft.text[current.id] ?? ""}
            oninput={(event) => handlers.onText(index, event.currentTarget.value)}
          ></textarea>
        {/if}

        <AskCommit {ask} {draft} {index} cursor={cursors[index] ?? -1} {handlers} />
      </section>
    {/each}
    {#if asks.length === 0}
      <p class="chat-system">Nothing waiting.</p>
    {/if}
  {/snippet}
  {#snippet foot()}
    <IdleComposer />
  {/snippet}
</AskFrame>

<style>
  .quiet {
    display: grid;
    gap: var(--space-3);
    margin-block-start: var(--space-2);
    animation: rise var(--motion-slow) var(--motion-ease);
  }
  .quiet:focus { outline: none; }
  .kicker {
    display: flex;
    justify-content: space-between;
    gap: var(--space-3);
    margin: 0;
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
  }
  .prompt {
    margin: 0;
    color: var(--tone-strong);
    font-size: var(--type-body);
    font-weight: var(--weight-title);
    line-height: 1.45;
  }
  .detail, .writing {
    margin: 0;
    color: var(--tone-secondary);
    font: inherit;
    font-size: var(--type-body);
    font-weight: var(--weight-body);
    line-height: var(--leading-body);
    white-space: pre-wrap;
  }
  .detail { max-height: 16rem; overflow: auto; }
  .hint {
    margin: 0;
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
  }
  .choices { display: grid; gap: var(--space-1); }
  .choice {
    display: grid;
    gap: 2px;
    width: 100%;
    margin: 0;
    padding: var(--space-3) var(--space-4);
    border: 0;
    border-radius: var(--radius-lg);
    background: transparent;
    color: var(--tone-primary);
    font: inherit;
    font-size: var(--type-body);
    line-height: 1.4;
    text-align: start;
    cursor: pointer;
    transition: background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease);
  }
  .choice small {
    color: var(--tone-secondary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
    line-height: 1.4;
  }
  .choice:hover { background: var(--surface-hover); }
  .choice[aria-checked="true"],
  .choice[data-ask-cursor="true"] { background: var(--surface-selected); color: var(--tone-strong); }
  .field { display: grid; gap: var(--space-2); color: var(--tone-tertiary); font-size: var(--type-small); }
  .field input, .writing {
    width: 100%;
    box-sizing: border-box;
    padding: var(--space-3) var(--space-4);
    border: 0;
    border-radius: var(--radius-lg);
    background: var(--surface-input);
    color: var(--tone-primary);
    font-size: var(--type-body);
  }
  .field input::placeholder, .writing::placeholder { color: var(--tone-faint); }
  .writing { min-height: 5.5rem; resize: vertical; }
  @keyframes rise {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .quiet { animation: none; }
  }
</style>
