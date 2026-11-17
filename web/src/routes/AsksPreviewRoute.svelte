<script lang="ts">
  import { onMount, tick } from "svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";
  import ComposerAsk from "../components/asks/ComposerAsk.svelte";
  import CurrentAsk from "../components/asks/CurrentAsk.svelte";
  import DockAsk from "../components/asks/DockAsk.svelte";
  import QuietAsk from "../components/asks/QuietAsk.svelte";
  import SheetAsk from "../components/asks/SheetAsk.svelte";
  import type { AskHandlers } from "../components/asks/askSurface";
  import {
    ASK_SCENES,
    ASK_VARIANTS,
    answerReady,
    answerSentence,
    askKeyFromEvent,
    commandForAskKey,
    draftWithOther,
    draftWithText,
    draftWithToggle,
    emptyDraft,
    questionAt,
    sceneById,
    selectionKey,
    variantById,
    type AskDraft,
    type AskVariantId,
    type PreviewAsk,
    type TranscriptLine
  } from "../lib/asks/preview";

  function readQuery(): { variant: AskVariantId; scene: string; shot: boolean } {
    const hash = window.location.hash;
    const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
    const params = new URLSearchParams(query);
    return {
      variant: variantById(params.get("variant") ?? ""),
      scene: sceneById(params.get("scene") ?? "").id,
      shot: params.get("shot") === "1"
    };
  }

  const opening = readQuery();
  let variant = $state<AskVariantId>(opening.variant);
  let sceneId = $state(opening.scene);
  let shot = $state(opening.shot);
  let lines = $state<TranscriptLine[]>([]);
  let asks = $state<PreviewAsk[]>([]);
  let drafts = $state<AskDraft[]>([]);
  let cursors = $state<number[]>([]);
  let active = $state(0);

  function loadScene(id: string): void {
    const scene = sceneById(id);
    sceneId = scene.id;
    lines = scene.lines.map((line) => ({ ...line }));
    asks = [...scene.asks];
    drafts = scene.asks.map(() => emptyDraft());
    cursors = scene.asks.map(() => -1);
    active = 0;
  }

  loadScene(opening.scene);

  onMount(() => {
    const onHash = () => {
      const next = readQuery();
      shot = next.shot;
      if (next.variant !== variant) variant = next.variant;
      if (next.scene !== sceneId) loadScene(next.scene);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });

  $effect(() => {
    const params = new URLSearchParams();
    params.set("variant", variant);
    params.set("scene", sceneId);
    if (shot) params.set("shot", "1");
    const hash = `#/dev/asks?${params}`;
    if (window.location.hash !== hash) history.replaceState(null, "", hash);
  });

  function patchDraft(index: number, draft: AskDraft): void {
    drafts = drafts.map((item, i) => (i === index ? draft : item));
  }

  function patchCursor(index: number, cursor: number): void {
    cursors = cursors.map((item, i) => (i === index ? cursor : item));
  }

  function drop(index: number): void {
    asks = asks.filter((_, i) => i !== index);
    drafts = drafts.filter((_, i) => i !== index);
    cursors = cursors.filter((_, i) => i !== index);
    active = Math.min(active, Math.max(0, asks.length - 1));
  }

  function finish(index: number, sentence: string): void {
    const text = sentence.trim();
    if (text === "") return;
    lines = [...lines, { role: "owner", text }];
    drop(index);
  }

  function advance(index: number, draft: AskDraft): void {
    patchDraft(index, { ...draft, step: draft.step + 1 });
    patchCursor(index, -1);
  }

  const handlers: AskHandlers = {
    onCommitChoice: (index, choiceId) => {
      const ask = asks[index];
      const draft = drafts[index];
      if (ask === undefined || draft === undefined || choiceId === "") return;
      if (ask.kind === "question") {
        const question = questionAt(ask, draft);
        if (question === null) return;
        const next = draftWithToggle(draft, question.id, choiceId, false);
        if (draft.step < ask.questions.length - 1) {
          advance(index, next);
          return;
        }
        finish(index, answerSentence(ask, next, choiceId));
        return;
      }
      finish(index, answerSentence(ask, draft, choiceId));
    },
    onFinish: finish,
    onToggle: (index, choiceId) => {
      const ask = asks[index];
      const draft = drafts[index];
      if (ask === undefined || draft === undefined) return;
      const question = questionAt(ask, draft);
      patchDraft(index, draftWithToggle(draft, selectionKey(ask, draft), choiceId, question?.multi === true));
    },
    onOther: (index, value) => {
      const ask = asks[index];
      const draft = drafts[index];
      const question = ask === undefined || draft === undefined ? null : questionAt(ask, draft);
      if (question === null || draft === undefined) return;
      patchDraft(index, draftWithOther(draft, question.id, value, question.multi !== true));
    },
    onText: (index, value) => {
      const ask = asks[index];
      const draft = drafts[index];
      const question = ask === undefined || draft === undefined ? null : questionAt(ask, draft);
      if (question === null || draft === undefined) return;
      patchDraft(index, draftWithText(draft, question.id, value));
    },
    onCommit: (index) => {
      const ask = asks[index];
      const draft = drafts[index];
      if (ask === undefined || draft === undefined || !answerReady(ask, draft)) return;
      if (ask.kind === "question" && draft.step < ask.questions.length - 1) {
        advance(index, draft);
        return;
      }
      finish(index, answerSentence(ask, draft));
    },
    onCancel: (index) => {
      lines = [...lines, { role: "note", text: "Turn cancelled." }];
      drop(index);
    },
    onStep: (index, delta) => {
      const draft = drafts[index];
      const ask = asks[index];
      if (draft === undefined || ask === undefined || ask.kind !== "question") return;
      const step = Math.min(ask.questions.length - 1, Math.max(0, draft.step + delta));
      patchDraft(index, { ...draft, step });
      patchCursor(index, -1);
    },
    onActivate: (index) => {
      if (index < 0 || index >= asks.length) return;
      active = index;
    },
    onKey: (event, index) => {
      const ask = asks[index];
      const draft = drafts[index];
      if (ask === undefined || draft === undefined) return;
      const typing = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement;
      const onButton = event.target instanceof HTMLButtonElement;
      const moves = event.key === "ArrowUp" || event.key === "ArrowDown" || event.key === "Escape" || /^[1-9]$/.test(event.key);
      if (onButton && !moves) return;
      const key = askKeyFromEvent(event, typing);
      if (key === null) return;
      const command = commandForAskKey(ask, draft, cursors[index] ?? -1, key);
      if (command.type === "none") return;
      event.preventDefault();
      if (command.type === "cancel") handlers.onCancel(index);
      else if (command.type === "cursor") {
        patchCursor(index, command.cursor);
        void focusChoice(index, command.cursor);
      } else if (command.type === "toggle") {
        patchCursor(index, command.cursor);
        handlers.onToggle(index, command.choiceId);
        void focusChoice(index, command.cursor);
      } else if (command.type === "commit-choice") handlers.onCommitChoice(index, command.choiceId);
      else if (command.type === "commit") handlers.onCommit(index);
    }
  };

  async function focusChoice(index: number, cursor: number): Promise<void> {
    await tick();
    document.querySelector<HTMLElement>(
      `[data-ask-index="${index}"] [data-ask-position="${cursor}"]`
    )?.focus({ preventScroll: true });
  }

  let surface = $derived({ lines, asks, drafts, cursors, active, handlers });
  let idea = $derived(ASK_VARIANTS.find((item) => item.id === variant)?.idea ?? "");
</script>

<div class="asks" class:shot data-ask-preview data-ask-variant={variant} data-ask-scene={sceneId}>
  {#if !shot}
    <div class="bar">
      <p class="name">Asks</p>
      <SegmentedControl
        name="ask-variant"
        label="Treatment"
        options={ASK_VARIANTS.map((item) => ({ value: item.id, label: item.label }))}
        bind:value={variant}
      />
      <div class="scenes" role="group" aria-label="Examples">
        {#each ASK_SCENES as scene (scene.id)}
          <button
            type="button"
            class="scene"
            aria-pressed={scene.id === sceneId}
            onclick={() => loadScene(scene.id)}
          >{scene.label}</button>
        {/each}
      </div>
      <button type="button" class="button button--ghost reset" onclick={() => loadScene(sceneId)}>Reset</button>
    </div>
    <p class="idea">{idea}</p>
  {/if}
  <div class="stage" data-ask-stage>
    {#if variant === "current"}
      <CurrentAsk {...surface} />
    {:else if variant === "quiet"}
      <QuietAsk {...surface} />
    {:else if variant === "dock"}
      <DockAsk {...surface} />
    {:else if variant === "composer"}
      <ComposerAsk {...surface} />
    {:else}
      <SheetAsk {...surface} />
    {/if}
  </div>
</div>

<style>
  .asks {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    min-width: 0;
    background: var(--surface-1);
    color: var(--tone-primary);
  }
  .bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-3) var(--space-5);
    padding: var(--space-4) var(--space-5) var(--space-2);
  }
  .name {
    margin: 0;
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
  }
  .scenes { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-4); }
  .scene {
    padding: var(--space-1) 0;
    border: 0;
    background: none;
    color: var(--tone-tertiary);
    font: inherit;
    font-size: var(--type-small);
    font-weight: var(--weight-small);
    cursor: pointer;
  }
  .scene[aria-pressed="true"] { color: var(--tone-strong); }
  .reset { margin-left: auto; }
  .idea {
    margin: 0;
    padding: 0 var(--space-5) var(--space-3);
    color: var(--tone-secondary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
    line-height: 1.45;
  }
  .stage { flex: 1; min-height: 0; min-width: 0; }
  .shot .stage { height: 100%; }
</style>
