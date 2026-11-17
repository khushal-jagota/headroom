<script lang="ts">
  import { cursorChoices, questionAt } from "../../lib/asks/preview";
  import AskCommit from "./AskCommit.svelte";
  import AskFrame from "./AskFrame.svelte";
  import AskTranscript from "./AskTranscript.svelte";
  import IdleComposer from "./IdleComposer.svelte";
  import type { AskSurface } from "./askSurface";

  let { lines, asks, drafts, cursors, active, handlers }: AskSurface = $props();

  let ask = $derived(asks[active]);
  let draft = $derived(drafts[active]);
  let current = $derived(ask ? questionAt(ask, draft) : null);
  let choices = $derived(ask && draft ? cursorChoices(ask, draft) : []);

  function lead(index: number): string {
    const item = asks[index];
    if (item.kind === "approval") return item.questions[0]?.prompt ?? item.stage ?? "Approval";
    const step = drafts[index];
    return questionAt(item, step)?.prompt ?? "Question";
  }
</script>

<AskFrame>
  {#snippet thread()}
    <AskTranscript {lines} />
  {/snippet}
  {#snippet foot()}
    <IdleComposer />
  {/snippet}
  {#snippet aside()}
    <aside class="sheet" aria-label={ask ? (ask.kind === "approval" ? "Approval" : "Question") : "Nothing waiting"}>
      {#if asks.length > 1}
        <div class="index" role="tablist" aria-label="Waiting">
          {#each asks as item, index (item.id)}
            <button
              type="button"
              role="tab"
              aria-selected={index === active}
              onclick={() => handlers.onActivate(index)}
            >
              <span>{item.kind === "approval" ? item.stage : "Question"}</span>
              {lead(index)}
            </button>
          {/each}
        </div>
      {/if}
      {#if ask && draft}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <div
          class="reading"
          role="group"
          data-ask-index={active}
          data-ask-live="true"
          tabindex="-1"
          onkeydown={(event) => handlers.onKey(event, active)}
        >
        <div class="body">
          <p class="kicker">
            {#if ask.kind === "approval"}{ask.stage}{:else}{current?.header || "Question"}{/if}
            {#if ask.kind === "question" && ask.questions.length > 1}
              <span>{draft.step + 1} of {ask.questions.length}</span>
            {/if}
          </p>
          <h2>{ask.kind === "approval" ? ask.questions[0]?.prompt : current?.prompt}</h2>
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
              rows="4"
              placeholder="Write the answer"
              aria-label={current.prompt}
              value={draft.text[current.id] ?? ""}
              oninput={(event) => handlers.onText(active, event.currentTarget.value)}
            ></textarea>
          {/if}
        </div>
        <div class="foot">
          <AskCommit {ask} {draft} index={active} cursor={cursors[active] ?? -1} {handlers} />
        </div>
        </div>
      {:else}
        <p class="empty">Nothing waiting.</p>
      {/if}
    </aside>
  {/snippet}
</AskFrame>

<style>
  .sheet {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-height: 0;
    background: var(--surface-2);
    color: var(--tone-primary);
    animation: arrive var(--motion-enter) var(--motion-ease);
  }
  .index { display: grid; gap: 2px; padding: var(--space-4) var(--space-4) 0; }
  .index button {
    display: grid;
    gap: 2px;
    width: 100%;
    padding: var(--space-2) var(--space-3);
    border: 0;
    border-radius: var(--radius-md);
    background: transparent;
    color: var(--tone-secondary);
    font: inherit;
    font-size: var(--type-small);
    text-align: start;
    cursor: pointer;
  }
  .index button span {
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
  }
  .index button[aria-selected="true"] { background: var(--surface-recessed); color: var(--tone-strong); }
  .reading {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-height: 0;
  }
  .reading:focus { outline: none; }
  .body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    display: grid;
    align-content: start;
    gap: var(--space-4);
    padding: var(--space-6) var(--space-5);
  }
  .kicker {
    display: flex;
    justify-content: space-between;
    margin: 0;
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
  }
  h2 {
    margin: 0;
    color: var(--tone-strong);
    font-size: var(--type-page-section);
    font-weight: var(--weight-title);
    line-height: 1.35;
  }
  .detail, .hint {
    margin: 0;
    color: var(--tone-secondary);
    font-size: var(--type-body);
    line-height: var(--leading-body);
    white-space: pre-wrap;
  }
  .hint { font-size: var(--type-small); font-weight: var(--weight-small); }
  .choices { display: grid; gap: var(--space-2); }
  .choice {
    display: grid;
    gap: var(--space-1);
    width: 100%;
    padding: var(--space-4);
    border: 0;
    border-radius: var(--radius-lg);
    background: transparent;
    color: var(--tone-primary);
    font: inherit;
    font-size: var(--type-body);
    text-align: start;
    cursor: pointer;
    transition: background-color var(--motion-fast) var(--motion-ease);
  }
  .choice small {
    color: var(--tone-secondary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
    line-height: 1.4;
  }
  .choice:hover { background: var(--wash-hover); }
  .choice[aria-checked="true"],
  .choice[data-ask-cursor="true"] { background: var(--surface-recessed); color: var(--tone-strong); }
  .writing {
    width: 100%;
    box-sizing: border-box;
    padding: var(--space-3) var(--space-4);
    border: 0;
    border-radius: var(--radius-lg);
    background: var(--surface-recessed);
    color: var(--tone-primary);
    font: inherit;
    font-size: var(--type-body);
    resize: vertical;
  }
  .writing::placeholder { color: var(--tone-faint); }
  .foot {
    flex: none;
    padding: var(--space-4) var(--space-5) var(--space-5);
  }
  .foot :global(.commit),
  .foot :global(.actions) { flex-wrap: nowrap; }
  .empty {
    margin: auto;
    color: var(--tone-tertiary);
    font-size: var(--type-small);
  }
  @keyframes arrive {
    from { opacity: 0; transform: translateX(16px); }
    to { opacity: 1; transform: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .sheet { animation: none; }
  }
</style>
