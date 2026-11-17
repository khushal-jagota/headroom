<script lang="ts">
  import {
    choicesFitARow,
    cursorChoices,
    questionAt
  } from "../../lib/asks/preview";
  import AskCommit from "./AskCommit.svelte";
  import AskFrame from "./AskFrame.svelte";
  import AskSwitcher from "./AskSwitcher.svelte";
  import AskTranscript from "./AskTranscript.svelte";
  import IdleComposer from "./IdleComposer.svelte";
  import type { AskSurface } from "./askSurface";

  let { lines, asks, drafts, cursors, active, handlers }: AskSurface = $props();

  let ask = $derived(asks[active]);
  let draft = $derived(drafts[active]);
  let current = $derived(ask ? questionAt(ask, draft) : null);
  let choices = $derived(ask && draft ? cursorChoices(ask, draft) : []);
  let asRow = $derived(choicesFitARow(choices));
</script>

<AskFrame>
  {#snippet thread()}
    <AskTranscript {lines} />
  {/snippet}
  {#snippet foot()}
    {#if ask && draft}
      {#key ask.id + ":" + draft.step}
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <section
        class="dock"
        role="group"
        data-ask-index={active}
        data-ask-live="true"
        tabindex="-1"
        aria-label={ask.kind === "approval" ? "Approval" : "Question"}
        onkeydown={(event) => handlers.onKey(event, active)}
      >
        <AskSwitcher {asks} {active} onActivate={handlers.onActivate} />
        <div class="head">
          <p class="kicker">
            {#if ask.kind === "approval"}{ask.stage}{:else}{current?.header || "Question"}{/if}
            {#if ask.kind === "question" && ask.questions.length > 1}
              <span>{draft.step + 1} of {ask.questions.length}</span>
            {/if}
          </p>
          <p class="prompt">{ask.kind === "approval" ? ask.questions[0]?.prompt : current?.prompt}</p>
        </div>
        {#if ask.detail}<div class="detail">{ask.detail}</div>{/if}
        {#if current?.multi}<p class="hint">Choose any that apply.</p>{/if}

        {#if choices.length > 0 && ask.kind === "question"}
          <div class="choices" class:row={asRow} role={current?.multi ? "group" : "radiogroup"} aria-label="Answers">
            {#each choices as choice, position (choice.id)}
              {@const selected = (draft.selected[current?.id ?? ""] ?? []).includes(choice.id)}
              <button
                type="button"
                class:choice={!asRow}
                class:segmented-option={asRow}
                role={current?.multi ? "checkbox" : "radio"}
                aria-checked={selected}
                aria-pressed={asRow ? selected : undefined}
                data-ask-choice={choice.id}
                data-ask-position={position}
                data-ask-cursor={cursors[active] === position ? "true" : undefined}
                onclick={() => current?.multi ? handlers.onToggle(active, choice.id) : handlers.onCommitChoice(active, choice.id)}
              >
                <span>{choice.label}</span>
                {#if choice.description}<small>{choice.description}</small>{/if}
              </button>
            {/each}
          </div>
        {/if}

        {#if current?.allowOther}
          <input
            class="writing"
            placeholder="Other"
            aria-label="Other"
            value={draft.other[current.id] ?? ""}
            oninput={(event) => handlers.onOther(active, event.currentTarget.value)}
          />
        {/if}
        {#if current?.freeText}
          <textarea
            class="writing"
            rows="2"
            placeholder="Write the answer"
            aria-label={current.prompt}
            value={draft.text[current.id] ?? ""}
            oninput={(event) => handlers.onText(active, event.currentTarget.value)}
          ></textarea>
        {/if}
        <AskCommit {ask} {draft} index={active} cursor={cursors[active] ?? -1} {handlers} />
      </section>
      {/key}
    {/if}
    <IdleComposer />
  {/snippet}
</AskFrame>

<style>
  .dock {
    display: grid;
    gap: var(--space-4);
    padding: var(--space-4) var(--space-1) var(--space-5);
    animation: lift var(--motion-slow) var(--motion-ease);
  }
  .dock:focus { outline: none; }
  .head { display: grid; gap: var(--space-1); }
  .kicker {
    display: flex;
    justify-content: space-between;
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
    line-height: 1.4;
  }
  .detail, .hint {
    margin: 0;
    color: var(--tone-secondary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
    line-height: 1.45;
  }
  .detail {
    max-height: 7.5rem;
    overflow: auto;
    white-space: pre-wrap;
    color: var(--tone-secondary);
    font-size: var(--type-body);
    font-weight: var(--weight-body);
    line-height: var(--leading-body);
  }
  .choices { display: grid; gap: var(--space-1); }
  .choices.row { display: flex; flex-wrap: wrap; gap: 2px; }
  .choice {
    display: grid;
    gap: 2px;
    width: 100%;
    padding: var(--space-2) var(--space-3);
    border: 0;
    border-radius: var(--radius-md);
    background: transparent;
    color: var(--tone-primary);
    font: inherit;
    font-size: var(--type-body);
    text-align: start;
    cursor: pointer;
    transition: background-color var(--motion-fast) var(--motion-ease);
  }
  .choice small { color: var(--tone-secondary); font-size: var(--type-small); font-weight: var(--weight-small); }
  .choice:hover { background: var(--surface-hover); }
  .choice[aria-checked="true"],
  .choice[data-ask-cursor="true"],
  .choices.row button[data-ask-cursor="true"] { background: var(--surface-selected); color: var(--tone-strong); }
  .writing {
    width: 100%;
    box-sizing: border-box;
    padding: var(--space-3) var(--space-4);
    border: 0;
    border-radius: var(--radius-lg);
    background: var(--surface-input);
    color: var(--tone-primary);
    font: inherit;
    font-size: var(--type-body);
    resize: vertical;
  }
  .writing::placeholder { color: var(--tone-faint); }
  @keyframes lift {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .dock { animation: none; }
  }
</style>
