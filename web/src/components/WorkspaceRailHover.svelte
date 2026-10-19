<script lang="ts">
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import { tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchJson } from "../lib/api";
  import { lifecycleFor, type WorkerTypesResponse } from "../lib/lifecycle";
  import { RAIL_HOVER_RECAP_MAX_LINES } from "../lib/workspaceRailInteraction";
  import { workspaceWorkerStageSentence } from "../lib/workspaceRail";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  let { visible, instant, target, title, kicker, recap = "", worker = null, rail, signal = undefined, conversationId = null, element = $bindable<HTMLDivElement | null>(null) }: {
    visible: boolean; instant: boolean; target: HTMLElement | null; title: string; kicker: string;
    recap?: string; worker?: WorkItemTicketFacts | null;
    rail: HTMLElement | null; cards?: readonly WorkItemTicketFacts[];
    signal?: Partial<ConversationSignal>; conversationId?: string | null; element?: HTMLDivElement | null;
  } = $props();
  const manifests = createQuery(() => ({ queryKey: ["worker-types"], queryFn: () => fetchJson<WorkerTypesResponse>("/api/worker-types") }));
  let currentWorker = $derived(worker);
  let stageSentence = $derived(currentWorker ? workspaceWorkerStageSentence(currentWorker, lifecycleFor(manifests.data, currentWorker.worker_type)) : "");
  let recapText = $derived(currentWorker ? currentWorker.recap ?? "" : recap);
  let recapElement = $state<HTMLDivElement | null>(null);
  let recapLines = $state(1);
  // Measure the fixed content, never the clamped recap. This keeps the budget
  // independent of the height that the budget itself produces.
  $effect(() => {
    recapText; stageSentence; title; kicker;
    if (!visible || !element || !recapElement) return;
    const panel = element;
    const recapBox = recapElement;
    const budget = () => {
      const css = getComputedStyle(panel);
      const recapCss = getComputedStyle(recapBox);
      const px = (value: string) => Number.parseFloat(value) || 0;
      const edges = (style: CSSStyleDeclaration) => px(style.paddingTop) + px(style.paddingBottom) + px(style.borderTopWidth) + px(style.borderBottomWidth);
      const fixedHeight = [...panel.children].filter(child => child !== recapBox).reduce((height, child) => {
        const style = getComputedStyle(child);
        return height + child.getBoundingClientRect().height + px(style.marginTop) + px(style.marginBottom);
      }, 0);
      const available = px(css.maxHeight) - edges(css) - fixedHeight - edges(recapCss) - px(recapCss.marginTop) - px(recapCss.marginBottom);
      recapLines = Math.min(RAIL_HOVER_RECAP_MAX_LINES, Math.max(0, Math.floor(available / px(recapCss.lineHeight))));
    };
    const observer = new ResizeObserver(budget);
    for (const child of panel.children) if (child !== recapBox) observer.observe(child);
    void tick().then(budget);
    window.addEventListener("resize", budget);
    return () => { observer.disconnect(); window.removeEventListener("resize", budget); };
  });
  let left = $state(0);
  let top = $state(0);
  $effect(() => {
    if (!target || !rail || !visible || !element) return;
    const currentTarget = target;
    const currentRail = rail;
    const currentPanel = element;
    const place = () => {
      const rect = currentTarget.getBoundingClientRect();
      left = Math.max(12, Math.min(currentRail.getBoundingClientRect().right + 14, window.innerWidth - currentPanel.offsetWidth - 12));
      top = Math.max(12, Math.min(rect.top - 8, window.innerHeight - currentPanel.offsetHeight - 12));
    };
    // Manifest arrival can change the height after the panel is already visible.
    const observer = new ResizeObserver(place);
    observer.observe(currentPanel);
    void tick().then(place);
    window.addEventListener("resize", place);
    return () => { observer.disconnect(); window.removeEventListener("resize", place); };
  });
</script>
<div bind:this={element} class="rail-hover-panel" class:visible class:instant style:left={`${left}px`} style:top={`${top}px`} data-rail-hover-panel aria-hidden={!visible}>
  <div class="kicker">{kicker}</div>
  <div class="title"><span>{currentWorker?.title ?? title}</span><ConversationSignalMark signal={currentWorker?.conversation_signal ?? signal} conversationId={currentWorker?.conversation_id ?? conversationId} /></div>
  {#if recapText}<div bind:this={recapElement} class="recap" class:no-space={recapLines === 0}><div class="recap-text" style:--recap-lines={recapLines}>{recapText}</div></div>{/if}
  {#if worker && stageSentence}<div class="doing">{stageSentence}</div>{/if}
</div>
<style>
  .rail-hover-panel { font-size:var(--type-sm); line-height:normal; position:fixed; z-index:40; width:min(340px,calc(100vw - 24px)); max-height:74vh; overflow:hidden; padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-lg); background:var(--surface-2); box-shadow:var(--shadow-float); pointer-events:none; opacity:0; transform:translateX(-4px); transition:opacity var(--motion-base) var(--motion-ease),transform var(--motion-base) var(--motion-ease); }
  .visible { opacity:1; transform:none; }
  .instant { transition:none; }
  .kicker { font-size:12px; color:var(--text-faintest); margin-bottom:5px; }
  .title { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); }
  .title { font-size:var(--type-md); font-weight:600; line-height:1.4; color:var(--text-strong); overflow-wrap:anywhere; }
  .recap { margin-top:var(--space-3); padding:var(--space-3); background:var(--surface-recessed); border-radius:var(--radius-md); font-size:12.5px; line-height:1.5; color:var(--text-default); }
  .recap-text { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:var(--recap-lines); line-clamp:var(--recap-lines); overflow:hidden; overflow-wrap:anywhere; }
  .recap.no-space { display:none; }
  .doing { margin-top:var(--space-3); font-size:12.5px; color:var(--text-faint); }
  @media (prefers-reduced-motion:reduce) { .rail-hover-panel { transition:none; transform:none; } }
</style>
