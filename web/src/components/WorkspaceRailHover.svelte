<script lang="ts">
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import { tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchJson } from "../lib/api";
  import { lifecycleFor, type WorkerTypesResponse } from "../lib/lifecycle";
  import { workspaceWorkerStageSentence, type WorkspaceTicketGroup } from "../lib/workspaceRail";
  import type { BoardCard } from "../lib/types";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  let { visible, instant, target, title, kicker, groups = null, worker = null, cards, rail, signal = undefined, conversationId = null }: {
    visible: boolean; instant: boolean; target: HTMLElement | null; title: string; kicker: string;
    groups?: WorkspaceTicketGroup[] | null; worker?: WorkItemTicketFacts | null;
    cards: BoardCard[]; rail: HTMLElement | null;
    signal?: ConversationSignal; conversationId?: string | null;
  } = $props();
  const manifests = createQuery(() => ({ queryKey: ["worker-types"], queryFn: () => fetchJson<WorkerTypesResponse>("/api/worker-types") }));
  let card = $derived(cards.find((entry) => entry.id === worker?.id));
  let currentWorker = $derived(card ?? worker);
  let stageSentence = $derived(currentWorker ? workspaceWorkerStageSentence(currentWorker, lifecycleFor(manifests.data, currentWorker.worker_type)) : "");
  let panel = $state<HTMLDivElement | null>(null);
  let left = $state(0);
  let top = $state(0);
  $effect(() => {
    if (!target || !rail || !visible || !panel) return;
    const currentTarget = target;
    const currentRail = rail;
    const currentPanel = panel;
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
<div bind:this={panel} class="rail-hover-panel" class:visible class:instant style:left={`${left}px`} style:top={`${top}px`} data-rail-hover-panel aria-hidden={!visible}>
  <div class="kicker">{kicker}</div>
  <div class="title"><span>{currentWorker?.title ?? title}</span><ConversationSignalMark signal={currentWorker?.conversation_signal ?? signal} conversationId={currentWorker?.conversation_id ?? conversationId} /></div>
  {#if worker}
    {#if currentWorker?.recap_preview}<div class="recap">{currentWorker.recap_preview}</div>{/if}
    {#if stageSentence}<div class="doing">{stageSentence}</div>{/if}
  {:else if groups}
    {#each groups as group (group.key)}
      <div class="label">{group.label}</div>
      {#each group.cards as ticket (ticket.id)}<div class="worker"><span>{ticket.title}</span><ConversationSignalMark signal={ticket.conversation_signal} conversationId={ticket.conversation_id} /></div>{/each}
    {/each}
  {/if}
</div>
<style>
  .rail-hover-panel { font-size:var(--type-sm); line-height:normal; position:fixed; z-index:40; width:min(340px,calc(100vw - 24px)); max-height:74vh; overflow:hidden; padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-lg); background:var(--surface-2); box-shadow:var(--shadow-float); pointer-events:none; opacity:0; transform:translateX(-4px); transition:opacity var(--motion-base) var(--motion-ease),transform var(--motion-base) var(--motion-ease); }
  .visible { opacity:1; transform:none; }
  .instant { transition:none; }
  .kicker { font-size:12px; color:var(--text-faintest); margin-bottom:5px; }
  .title, .worker { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); }
  .title { font-size:var(--type-md); font-weight:600; line-height:1.4; color:var(--text-strong); overflow-wrap:anywhere; }
  .recap { margin-top:var(--space-3); padding:var(--space-3); background:var(--surface-recessed); border-radius:var(--radius-md); font-size:12.5px; line-height:1.5; color:var(--text-default); }
  .doing { margin-top:var(--space-3); font-size:12.5px; color:var(--text-faint); }
  @media (prefers-reduced-motion:reduce) { .rail-hover-panel { transition:none; transform:none; } }
  .label { margin:var(--space-4) 0 7px; font-size:10.5px; font-weight:600; letter-spacing:0.08em; text-transform:uppercase; color:var(--text-faint); }
  .worker { padding:var(--space-1) 0; font-size:12.5px; line-height:1.4; color:var(--text-default); }
</style>
