<script lang="ts">
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import { tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchJson } from "../lib/api";
  import { lifecycleFor, type WorkerTypesResponse } from "../lib/lifecycle";
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
  {#if worker}
    {#if currentWorker?.recap_preview}<div class="recap">{currentWorker.recap_preview}</div>{/if}
    {#if stageSentence}<div class="doing">{stageSentence}</div>{/if}
  {:else if recap}
    <div class="recap">{recap}</div>
  {/if}
</div>
<style>
  .rail-hover-panel { font-size:var(--type-sm); line-height:normal; position:fixed; z-index:40; width:min(340px,calc(100vw - 24px)); max-height:74vh; overflow:hidden; padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-lg); background:var(--surface-2); box-shadow:var(--shadow-float); pointer-events:none; opacity:0; transform:translateX(-4px); transition:opacity var(--motion-base) var(--motion-ease),transform var(--motion-base) var(--motion-ease); }
  .visible { opacity:1; transform:none; }
  .instant { transition:none; }
  .kicker { font-size:12px; color:var(--text-faintest); margin-bottom:5px; }
  .title { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); }
  .title { font-size:var(--type-md); font-weight:600; line-height:1.4; color:var(--text-strong); overflow-wrap:anywhere; }
  .recap { margin-top:var(--space-3); padding:var(--space-3); background:var(--surface-recessed); border-radius:var(--radius-md); font-size:12.5px; line-height:1.5; color:var(--text-default); }
  .doing { margin-top:var(--space-3); font-size:12.5px; color:var(--text-faint); }
  @media (prefers-reduced-motion:reduce) { .rail-hover-panel { transition:none; transform:none; } }
</style>
