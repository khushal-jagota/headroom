<script lang="ts">
  import { tick, untrack, getContext } from "svelte";
  import { cubicOut } from "svelte/easing";
  import { fly } from "svelte/transition";
  import type { ChecklistBlock } from "../../lib/pageKit";
  import ChecklistSummary from "./ChecklistSummary.svelte";
  import ChecklistDeadline from "../goals/ChecklistDeadline.svelte";

  import { PAGE_CONTEXT, type PageContext } from "../../lib/pageKit/context";
  const context = getContext<PageContext | undefined>(PAGE_CONTEXT);
  let pending = $state<Record<string, { state: "done" | "open"; request: number }>>({});
  let failed = $state<Record<string, { state: "done" | "open"; error: unknown }>>({});
  let demoStates = $state<Record<string, "done" | "open">>({});
  let requestSequence = 0;
  const localRequests = new Map<string, number>();
  let disposed = false;
  let saveError = $derived(Object.values(failed)[0]?.error);
  async function persist(id: string, state: "done" | "open") {
    const request = ++requestSequence;
    const blockId = block.id;
    const session = context?.checklistSession?.(blockId, id);
    localRequests.set(id, request);
    pending = { ...pending, [id]: { state, request } };
    const remaining = { ...failed };
    delete remaining[id];
    failed = remaining;
    message = "";
    try {
      if (context?.updateBlock) {
        const write = () => context.updateBlock!(blockId, old => {
          if (old.type !== "checklist" || !old.items.some(item => item.id === id)) throw new Error("This checklist item no longer exists.");
          return { ...old, items: old.items.map(item => item.id === id ? { ...item, state } : item) };
        });
        await (session ? session.run({ state }, write) : write());
      } else {
        // The component gallery has no writer. Its interactions accept local state.
        demoStates = { ...demoStates, [id]: state };
      }
      if (disposed || pending[id]?.request !== request) return;
      if (state === "done") {
        timers.set(id, window.setTimeout(() => {
          timers.delete(id);
          void settle(id);
        }, reduced ? 0 : settleMs));
      }
      const next = { ...pending };
      delete next[id];
      pending = next;
      await tick();
      if (disposed || pending[id] || failed[id]) return;
      const step = steps.find((item) => item.id === id);
      message = state === "done"
        ? allDone ? `All ${total} items done.` : `${step?.title ?? "Item"} done. ${doneCount} of ${total} complete.`
        : `${step?.title ?? "Item"} marked as not done. ${doneCount} of ${total} complete.`;
    } catch (error) {
      if (disposed || pending[id]?.request !== request) return;
      const next = { ...pending };
      delete next[id];
      pending = next;
      failed = { ...failed, [id]: { state, error } };
    } finally { if (localRequests.get(id) === request) localRequests.delete(id); }
  }
  let { block, presentation = "normal", supplementary = false }: {
    block: ChecklistBlock;
    presentation?: "normal" | "small";
    supplementary?: boolean;
  } = $props();

  type Step = {
    id: string;
    title: string;
    detail?: string;
    action?: string;
    blocked: boolean;
    filedDone: boolean;
    item: ChecklistBlock["items"][number];
  };

  const settleMs = 720;
  const uid = `cl-${Math.random().toString(36).slice(2, 8)}`;
  let reduced = $state(false);
  let hidden = $state(false);
  let mobileOpen = $state(false);
  let foldable = $derived(supplementary && (context?.narrow ?? false));
  let bodyOpen = $derived(foldable ? mobileOpen : !hidden);
  let size = $derived(supplementary ? "small" : presentation);
  let headerToggle = $state<HTMLButtonElement | null>(null);
  let body = $state<HTMLDivElement | null>(null);

  $effect.pre(() => {
    if (!bodyOpen && body?.contains(document.activeElement)) headerToggle?.focus();
  });

  function toggleChecklist(): void {
    if (foldable) mobileOpen = !mobileOpen;
    else hidden = !hidden;
  }
  let groupOpen = $state(false);
  let message = $state("");
  let root = $state<HTMLElement | null>(null);
  const timers = new Map<string, number>();

  function readSteps(source: ChecklistBlock): Step[] {
    return source.items.map((item, index) => ({
      id: item.id ?? `item-${index}`,
      title: item.label,
      detail: item.detail,
      action: item.action,
      blocked: item.state === "blocked",
      filedDone: item.state === "done",
      item
    }));
  }

  // Reattach native feedback to commands submitted by an earlier mounted control.
  $effect(() => {
    if (!context?.checklistSession) return;
    const subscriptions = block.items.map(item => {
      const session = context.checklistSession!(block.id, item.id);
      const sync = () => {
        if (localRequests.has(item.id)) return;
        const receipt = session.current;
        if (receipt?.status === "pending") {
          pending = { ...pending, [item.id]: { state: receipt.command.state, request: ++requestSequence } };
        } else {
          if (!receipt && pending[item.id]?.state === "done" && !timers.has(item.id)) {
            timers.set(item.id, window.setTimeout(() => { timers.delete(item.id); void settle(item.id); }, reduced ? 0 : settleMs));
          }
          const next = { ...pending }; delete next[item.id]; pending = next;
        }
        const next = { ...failed };
        if (receipt?.status === "failed") next[item.id] = { state: receipt.command.state, error: receipt.error };
        else delete next[item.id];
        failed = next;
      };
      untrack(sync);
      return session.subscribe(sync);
    });
    return () => subscriptions.forEach(unsubscribe => unsubscribe());
  });

  let done = $state<string[]>(untrack(() => block.items.filter(item => item.state === "done").map(item => item.id)));
  let settled = $state<string[]>(untrack(() => block.items.filter(item => item.state === "done").map(item => item.id)));
  let acceptedDone = $derived(block.items.filter(item => (demoStates[item.id] ?? item.state) === "done").map(item => item.id));
  $effect(() => {
    const ids = block.items.filter(item => (pending[item.id]?.state ?? demoStates[item.id] ?? item.state) === "done").map(item => item.id);
    const accepted = acceptedDone.filter(id => !pending[id]);
    untrack(() => { done = ids; settled = accepted.filter(id => !timers.has(id)); });
  });
  let expanded = $state(untrack(() => readSteps(block).find((step) => !step.filedDone)?.id ?? null));
  let steps = $derived(readSteps(block));
  let title = $derived(block.title ?? "Checklist");
  let doneTitle = $derived(block.doneTitle ?? "All items done");
  let total = $derived(steps.length);
  let doneCount = $derived(acceptedDone.length);
  let allDone = $derived(total > 0 && doneCount === total);
  let openSteps = $derived(steps.filter((step) => !settled.includes(step.id)));
  let completedSteps = $derived(steps.filter((step) => settled.includes(step.id)));

  $effect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      reduced = media.matches;
    };
    apply();
    media.addEventListener("change", apply);
    return () => {
      disposed = true;
      media.removeEventListener("change", apply);
      timers.forEach((timer) => window.clearTimeout(timer));
      timers.clear();
    };
  });

  function order(ids: string[]): string[] {
    return steps.map((step) => step.id).filter((id) => ids.includes(id));
  }

  function grow(node: HTMLElement, { duration = 320 }: { duration?: number } = {}) {
    const height = node.scrollHeight;
    const quiet = reduced;
    return {
      duration: quiet ? 150 : duration,
      easing: cubicOut,
      css: (t: number) => (quiet ? `opacity:${t}` : `height:${t * height}px;opacity:${t};overflow:hidden`)
    };
  }

  function check(id: string): void {
    if (done.includes(id)) return;
    const next = order([...done, id]);
    done = next;
    void persist(id, "done");
  }

  async function settle(id: string): Promise<void> {
    if (!acceptedDone.includes(id) || pending[id] || !done.includes(id) || settled.includes(id)) return;
    const leavingFocused = root?.querySelector(`[data-step="${id}"]`)?.contains(document.activeElement) ?? false;
    let nextExpanded = expanded;
    if (nextExpanded === id) {
      const orderIds = steps.map((step) => step.id);
      const start = orderIds.indexOf(id);
      nextExpanded = orderIds.map((_, offset) => orderIds[(start + offset + 1) % orderIds.length]!).find((item) => !done.includes(item)) ?? null;
    }
    settled = order([...settled, id]);
    expanded = nextExpanded;
    if (!leavingFocused) return;
    await tick();
    const next = nextExpanded ? root?.querySelector<HTMLElement>(`[data-step="${nextExpanded}"]`) : null;
    (next?.querySelector<HTMLElement>("[data-action]") ?? next?.querySelector<HTMLElement>("[data-title]") ?? root?.querySelector<HTMLElement>("[data-group-toggle]"))?.focus();
  }

  async function uncheck(id: string, fromGroup: boolean): Promise<void> {
    void persist(id, "open");
    const timer = timers.get(id);
    if (timer) window.clearTimeout(timer);
    timers.delete(id);
    if (!done.includes(id)) return;
    done = done.filter((item) => item !== id);
    settled = settled.filter((item) => item !== id);
    expanded = expanded ?? id;
    if (!fromGroup) return;
    await tick();
    root?.querySelector<HTMLElement>(`[data-step="${id}"] [data-check]`)?.focus();
  }

  function toggleStep(id: string): void {
    expanded = expanded === id ? null : id;
  }

  function keys(node: HTMLElement): { destroy: () => void } {
    const onKey = (event: KeyboardEvent) => navigate(event, node);
    node.addEventListener("keydown", onKey);
    return { destroy: () => node.removeEventListener("keydown", onKey) };
  }

  function navigate(event: KeyboardEvent, node: HTMLElement): void {
    const target = event.target as HTMLElement;
    if (!target.matches("[data-title]") || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const titles = Array.from(node.querySelectorAll<HTMLElement>("li:not([data-leaving]) [data-title]"));
    const index = titles.indexOf(target);
    if (index < 0) return;
    const next = event.key === "Home" ? 0 : event.key === "End" ? titles.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + titles.length) % titles.length;
    event.preventDefault();
    titles[next]?.focus();
  }
</script>

<section class="card" class:small={size === "small"} class:reduced bind:this={root} data-block="checklist" data-presentation={foldable && !bodyOpen ? "smallest" : size} data-supplementary={supplementary || undefined} data-complete={allDone ? "true" : undefined} aria-labelledby="{uid}-title">
  <div class="checklist-header">
    <ChecklistSummary checked={doneCount} {total} {title} {doneTitle} {reduced} presentation={size} headingId={`${uid}-title`}>
      {#snippet actions()}
        <span class="header-action" aria-hidden="true">
          <span class="hide-label">{bodyOpen ? "Hide" : "Show"}</span>
          <svg class="chevron" class:turned={!bodyOpen} viewBox="0 0 16 16"><path d="M3.5 10.5 8 6l4.5 4.5" /></svg>
        </span>
      {/snippet}
    </ChecklistSummary>
    <button bind:this={headerToggle} type="button" class="header-toggle" data-checklist-toggle aria-label={bodyOpen ? "Hide checklist" : "Show checklist"} aria-expanded={bodyOpen} aria-controls="{uid}-body" onclick={toggleChecklist}></button>
  </div>
  {#if Object.keys(failed).length}<p class="save-feedback" role="alert">Checklist change was not saved. {String(saveError)} <button type="button" onclick={() => { for (const [id, { state }] of Object.entries(failed)) void persist(id, state); }}>Retry save</button></p>{/if}
  <div id="{uid}-body" data-checklist-body bind:this={body} inert={!bodyOpen} aria-hidden={!bodyOpen}>
  {#if bodyOpen}
    <div class="body" transition:grow>
      <div class="body-inner">
        <ul class="list" aria-label="Open items" use:keys>
          {#each openSteps as step (step.id)}
            <li class="step" class:current={expanded === step.id} class:struck={done.includes(step.id)} data-step={step.id} transition:grow={{ duration: 280 }}>
              <div class="step-inner">
                <button type="button" class="check" data-check role="checkbox" aria-checked={done.includes(step.id)} aria-labelledby="{uid}-{step.id}-title" onclick={() => done.includes(step.id) ? uncheck(step.id, false) : check(step.id)}>
                  <span class="check-visual" aria-hidden="true">
                    <span class="check-fill"></span>
                    <svg class="check-mark" viewBox="0 0 22 22"><path d="M6.75 11.25 9.75 14.25 15.25 8.25" pathLength="1" /></svg>
                  </span>
                </button>
                <div class="step-main">
                  <div class="step-heading">
                    <button type="button" class="step-title" data-title aria-expanded={expanded === step.id} aria-controls="{uid}-{step.id}-panel" onclick={() => toggleStep(step.id)}>
                      <span id="{uid}-{step.id}-title" class="title-text"><span class="strike-mark">{step.title}</span></span>
                    </button>
                    <ChecklistDeadline blockId={block.id} item={step.item}/>
                  </div>
                  {#if expanded === step.id}
                    <div id="{uid}-{step.id}-panel" class="step-panel" role="region" aria-labelledby="{uid}-{step.id}-title" transition:grow={{ duration: 260 }}>
                      {#if step.detail?.trim() || (step.blocked && !done.includes(step.id)) || step.action?.trim()}
                        <div class="step-panel-inner">
                          {#if step.detail?.trim()}<div class="detail">{step.detail}</div>{/if}
                          {#if step.blocked && !done.includes(step.id)}<p class="blocked">Blocked</p>{/if}
                          {#if step.action?.trim()}
                              <button type="button" class="action" data-action onclick={() => check(step.id)}>
                                {step.action}
                                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10M9 4l4 4-4 4" /></svg>
                              </button>
                          {/if}
                        </div>
                      {/if}
                    </div>
                  {/if}
                </div>
              </div>
            </li>
          {/each}
        </ul>
        {#if completedSteps.length}
          <div class="group" transition:grow>
            <button type="button" class="group-toggle" data-group-toggle aria-expanded={groupOpen} aria-controls="{uid}-completed" onclick={() => { groupOpen = !groupOpen; }}>
              <span class="sr">{completedSteps.length} completed</span>
              <span aria-hidden="true"><span class="roll">{#key completedSteps.length}<span in:fly={{ y: reduced ? 0 : 8, duration: reduced ? 150 : 220 }} out:fly={{ y: reduced ? 0 : -8, duration: reduced ? 100 : 140 }}>{completedSteps.length}</span>{/key}</span>&nbsp;completed</span>
              <svg class="chevron" class:turned={!groupOpen} viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 10.5 8 6l4.5 4.5" /></svg>
            </button>
            {#if groupOpen}
              <ul id="{uid}-completed" class="list" aria-label="Completed items" transition:grow={{ duration: 260 }}>
                {#each completedSteps as step (step.id)}
                  <li class="step struck" transition:grow={{ duration: 240 }}>
                    <div class="step-inner">
                      <button type="button" class="check" data-check role="checkbox" aria-checked="true" aria-labelledby="{uid}-{step.id}-done" onclick={() => uncheck(step.id, true)}>
                        <span class="check-visual" aria-hidden="true">
                          <span class="check-fill"></span>
                          <svg class="check-mark" viewBox="0 0 22 22"><path d="M6.75 11.25 9.75 14.25 15.25 8.25" pathLength="1" /></svg>
                        </span>
                      </button>
                      <div class="step-heading">
                        <div class="completed-title"><div id="{uid}-{step.id}-done" class="title-text"><span class="strike-mark">{step.title}</span></div></div>
                        <ChecklistDeadline blockId={block.id} item={step.item}/>
                      </div>
                    </div>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  {/if}
  </div>
  <span class="sr" role="status">{message}</span>
</section>

<style>
  .card {
    container-type: inline-size;
    width: 100%;
    min-width: 0;
    border-radius: var(--space-5);
    background: var(--surface-recessed);
    color: var(--text-default);
  }
  .roll { position: relative; display: inline-grid; vertical-align: top; }
  .roll > span { grid-area: 1 / 1; }
  .save-feedback { margin: 0; padding: 0 var(--space-5) var(--space-3); color: var(--text-faint); font-size: var(--type-sm); }
  .checklist-header { position: relative; border-radius: inherit; transition: background var(--motion-fast) var(--motion-ease); }
  .checklist-header:hover { background: var(--surface-2); }
  .header-action { display: inline-flex; align-items: center; gap: var(--space-1); color: var(--text-faint); font-size: var(--type-sm); font-weight: 500; }
  .header-toggle { position: absolute; inset: 0; z-index: 1; width: 100%; height: 100%; margin: 0; padding: 0; border: 0; border-radius: inherit; background: transparent; cursor: pointer; -webkit-tap-highlight-color: transparent; }
  .header-toggle:focus-visible, .check:focus-visible, .step-title:focus-visible, .action:focus-visible, .group-toggle:focus-visible {
    outline: var(--border-strong) solid var(--accent-bright);
    outline-offset: 2px;
  }
  .small .body-inner { padding: var(--space-1); }
  .small .step-inner { column-gap: var(--space-2); padding-inline: var(--space-2); }
  .small .group-toggle { padding-left: calc(40px + var(--space-2) * 2); }
  .chevron { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; transition: transform var(--motion-base) var(--motion-ease); }
  .chevron.turned { transform: rotate(180deg); }
  .body { overflow: hidden; }
  .body-inner { padding: var(--space-2); border-top: var(--border-hairline) solid var(--border-color); }
  .list { margin: 0; padding: 0; list-style: none; }
  .step { overflow: hidden; }
  .step-inner {
    display: grid;
    grid-template-columns: 40px minmax(0, 1fr);
    column-gap: var(--space-3);
    align-items: start;
    padding: var(--space-1) var(--space-3);
    border-radius: calc(var(--space-5) - var(--space-2));
  }
  .step.current .step-inner { background: var(--surface-2); }
  .check {
    display: grid;
    width: 40px;
    height: 40px;
    place-items: center;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: var(--radius-pill);
    background: transparent;
    cursor: pointer;
  }
  .check-visual {
    position: relative;
    display: block;
    width: 22px;
    height: 22px;
    border: 1.5px solid var(--text-faint);
    border-radius: var(--radius-pill);
    background: var(--surface-recessed);
    transition: transform 110ms var(--motion-ease), border-color var(--motion-fast) var(--motion-ease);
  }
  .check:active .check-visual { transform: scale(0.92); }
  .check[aria-checked="true"] .check-visual { border-color: var(--text-strong); }
  .check-fill {
    position: absolute;
    inset: -1.5px;
    border-radius: inherit;
    background: var(--text-strong);
    opacity: 0;
    transform: scale(0.6);
    transition: transform var(--motion-slow) var(--motion-ease), opacity var(--motion-fast) linear;
  }
  .check[aria-checked="true"] .check-fill { opacity: 1; transform: scale(1); }
  .check-mark { position: absolute; inset: -1.5px; width: 22px; height: 22px; }
  .check-mark path {
    fill: none;
    stroke: var(--surface-recessed);
    stroke-width: 1.75;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 1;
    stroke-dashoffset: 1;
    opacity: 0;
    transition: stroke-dashoffset var(--motion-base) var(--motion-ease) 50ms, opacity var(--motion-fast) linear 50ms;
  }
  .check[aria-checked="true"] .check-mark path { stroke-dashoffset: 0; opacity: 1; }
  .step-main { display: grid; min-width: 0; }
  .step-heading { display: flex; min-width: 0; align-items: start; gap: var(--space-2); }
  .step-inner:has(:global([data-item-deadline])) > .check { margin-top: 2px; }
  .step-heading:has(:global([data-item-deadline])) > .step-title,
  .step-heading:has(:global([data-item-deadline])) > .completed-title { min-height: 44px; }
  .step-title, .completed-title {
    display: flex;
    flex: 1 1 auto;
    min-width: 0;
    width: 100%;
    min-height: 40px;
    align-items: center;
    margin: 0;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--text-strong);
    font: inherit;
    font-size: var(--type-sm);
    font-weight: 500;
    line-height: 1.4;
    text-align: left;
  }
  .step-title { cursor: pointer; border-radius: var(--radius-md); }
  .title-text { transition: color var(--motion-base) var(--motion-ease); }
  .strike-mark {
    background-image: linear-gradient(currentColor, currentColor);
    background-position: 0 60%;
    background-repeat: no-repeat;
    background-size: 0% 1px;
    transition: background-size var(--motion-slow) var(--motion-ease);
    -webkit-box-decoration-break: clone;
    box-decoration-break: clone;
  }
  .step.struck .title-text { color: var(--text-faint); }
  .step.struck .strike-mark { background-size: 100% 1px; }
  /* Named for this disclosure, so the application-wide .panel card cannot style it. */
  .step-panel { overflow: hidden; }
  .step-panel-inner { padding: 0 0 var(--space-3); }
  .detail { white-space: pre-wrap; max-width: 46ch; margin: 0; color: var(--text-faint); font-size: var(--type-sm); font-weight: 400; line-height: 1.45; }
  .blocked { margin: var(--space-2) 0 0; color: var(--accent-error); font-size: var(--type-sm); }
  .action {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    min-height: var(--space-6);
    margin-top: var(--space-3);
    padding: 0 var(--space-3) 0 var(--space-4);
    border: 0;
    border-radius: var(--radius-pill);
    background: var(--text-strong);
    color: var(--surface-recessed);
    font: inherit;
    font-size: var(--type-sm);
    font-weight: 500;
    cursor: pointer;
  }
  .action svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
  .action:active { transform: scale(0.97); }
  .group-toggle {
    display: grid;
    width: 100%;
    min-height: 44px;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: var(--space-3);
    margin-top: 2px;
    padding: 0 var(--space-3) 0 64px;
    border: 0;
    border-radius: var(--radius-lg);
    background: transparent;
    color: var(--text-faint);
    font: inherit;
    font-size: var(--type-sm);
    font-weight: 500;
    text-align: left;
    cursor: pointer;
  }
  .group-toggle:hover { background: var(--surface-2); color: var(--text-strong); }
  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    border: 0;
  }
  @container (max-width: 380px) {
    .hide-label { display: none; }
  }
  .reduced .checklist-header,
  .reduced .check-fill,
  .reduced .check-mark path,
  .reduced .title-text,
  .reduced .strike-mark,
  .reduced .chevron,
  .reduced .check-visual { transition: none; animation: none; }
  .reduced .check:active .check-visual,
  .reduced .action:active { transform: none; }
</style>
