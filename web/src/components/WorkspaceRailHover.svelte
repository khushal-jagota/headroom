<script lang="ts">
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import { tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchJson } from "../lib/api";
  import { lifecycleFor, type WorkerTypesResponse } from "../lib/lifecycle";
  import { workspaceWorkerStageSentence } from "../lib/workspaceRail";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import RequestRecapAction from "./RequestRecapAction.svelte";
  import RecapPending from "./RecapPending.svelte";
  import { recapPending } from "../lib/recapPending";
  /* ``recapTarget`` is the agent that can be asked for a fresh recap, and its own canonical
   * recap. The panel's line is not always that recap — for an orchestrator it is a composed
   * list — and the ask has to watch the real thing it asked for, not a line that moves for
   * its own reasons.
   * ``onKeepOpen`` and ``onLeave`` hand the panel the same hover intent the rows use, so
   * the pointer can cross the gap and press what is inside without the panel closing. */
  let { visible, instant, target, title, kicker, recap = "", worker = null, rail, signal = undefined, conversationId = null, recapTarget = null, onKeepOpen, onLeave, element = $bindable<HTMLDivElement | null>(null) }: {
    visible: boolean; instant: boolean; target: HTMLElement | null; title: string; kicker: string;
    recap?: string; worker?: WorkItemTicketFacts | null;
    rail: HTMLElement | null; cards?: readonly WorkItemTicketFacts[];
    signal?: Partial<ConversationSignal>; conversationId?: string | null;
    recapTarget?: { kind: "ticket" | "sprint_item" | "chief"; id: string; recap?: string } | null;
    onKeepOpen?: () => void; onLeave?: () => void;
    element?: HTMLDivElement | null;
  } = $props();
  const manifests = createQuery(() => ({ queryKey: ["worker-types"], queryFn: () => fetchJson<WorkerTypesResponse>("/api/worker-types") }));
  let currentWorker = $derived(worker);
  let stageSentence = $derived(currentWorker ? workspaceWorkerStageSentence(currentWorker, lifecycleFor(manifests.data, currentWorker.worker_type)) : "");
  let recapText = $derived(worker ? (currentWorker?.recap_preview ?? "") : recap);
  let panelSignal = $derived(currentWorker?.conversation_signal ?? signal);
  let pendingRecap = $derived(recapTarget !== null && recapPending(recapTarget.recap, panelSignal));
  let left = $state(0);
  let top = $state(0);
  /* The panel can only take the pointer where it is clear of the rail. A narrow window
   * clamps it back over the rows, and a panel that took the pointer there would cover
   * the row the pointer is travelling to. Hovering the rail is the common act and wins. */
  let clearOfRail = $state(false);
  $effect(() => {
    if (!target || !rail || !visible || !element) return;
    const currentTarget = target;
    const currentRail = rail;
    const currentPanel = element;
    const place = () => {
      const rect = currentTarget.getBoundingClientRect();
      const railRight = currentRail.getBoundingClientRect().right;
      left = Math.max(12, Math.min(railRight + 14, window.innerWidth - currentPanel.offsetWidth - 12));
      top = Math.max(12, Math.min(rect.top - 8, window.innerHeight - currentPanel.offsetHeight - 12));
      clearOfRail = left >= railRight;
    };
    // Manifest arrival can change the height after the panel is already visible.
    const observer = new ResizeObserver(place);
    observer.observe(currentPanel);
    void tick().then(place);
    window.addEventListener("resize", place);
    return () => { observer.disconnect(); window.removeEventListener("resize", place); };
  });
</script>
<div bind:this={element} class="rail-hover-panel" class:visible class:instant class:interactive={visible && clearOfRail} style:left={`${left}px`} style:top={`${top}px`} data-rail-hover-panel aria-hidden={!visible}
  onpointerenter={() => onKeepOpen?.()} onpointerleave={() => onLeave?.()}
  onfocusin={() => onKeepOpen?.()}
  onfocusout={(event) => {
    // Focus landing somewhere outside is someone leaving. Focus landing nowhere is the
    // control they just pressed being replaced by what it did, and they are still here.
    const moved = event.relatedTarget as Node | null;
    if (moved !== null && !element?.contains(moved)) onLeave?.();
  }}>
  <div class="kicker">{kicker}</div>
  <div class="title"><span>{currentWorker?.title ?? title}</span><ConversationSignalMark signal={currentWorker?.conversation_signal ?? signal} conversationId={currentWorker?.conversation_id ?? conversationId} /></div>
  {#if recapText || recapTarget}
    <div class="recap">
      {#if pendingRecap}<RecapPending />{:else if recapText}<span data-recap-text>{recapText}</span>{/if}
      {#if recapTarget}<RequestRecapAction kind={recapTarget.kind} id={recapTarget.id} pending={pendingRecap} class="recap-ask" />{/if}
    </div>
  {/if}
  {#if worker && stageSentence}<div class="doing">{stageSentence}</div>{/if}
</div>
<style>
  .rail-hover-panel { font-size:var(--type-sm); line-height:normal; position:fixed; z-index:40; width:min(340px,calc(100vw - 24px)); max-height:74vh; overflow:hidden; padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-lg); background:var(--surface-2); box-shadow:var(--shadow-float); pointer-events:none; opacity:0; transform:translateX(-4px); transition:opacity var(--motion-base) var(--motion-ease),transform var(--motion-base) var(--motion-ease); }
  .visible { opacity:1; transform:none; }
  .interactive { pointer-events:auto; }
  .instant { transition:none; }
  .kicker { font-size:12px; color:var(--text-faintest); margin-bottom:5px; }
  .title { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); }
  .title { font-size:var(--type-md); font-weight:600; line-height:1.4; color:var(--text-strong); overflow-wrap:anywhere; }
  .recap { margin-top:var(--space-3); padding:var(--space-3); background:var(--surface-recessed); border-radius:var(--radius-md); font-size:12.5px; line-height:1.5; color:var(--text-default); }
  /* The ask lives inside the recessed block, under the line it replaces. */
  .recap :global(.recap-ask) { display:flex; margin-top:var(--space-2); font-size:12px; }
  .doing { margin-top:var(--space-3); font-size:12.5px; color:var(--text-faint); }
  @media (prefers-reduced-motion:reduce) { .rail-hover-panel { transition:none; transform:none; } }
</style>
