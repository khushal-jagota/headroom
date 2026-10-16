<script lang="ts">
  import { tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchJson } from "../lib/api";
  import { lifecycleFor, type WorkerTypesResponse } from "../lib/lifecycle";
  import { workspaceWorkerStages, type WorkspaceTicketGroup } from "../lib/workspaceRail";
  import type { BoardCard } from "../lib/types";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  let { visible, instant, target, title, kicker, groups = null, worker = null, cards, rail }: {
    visible: boolean; instant: boolean; target: HTMLElement | null; title: string; kicker: string;
    groups?: WorkspaceTicketGroup[] | null; worker?: WorkItemTicketFacts | null;
    cards: BoardCard[]; rail: HTMLElement | null;
  } = $props();
  const manifests = createQuery(() => ({ queryKey: ["worker-types"], queryFn: () => fetchJson<WorkerTypesResponse>("/api/worker-types") }));
  let card = $derived(cards.find((entry) => entry.id === worker?.id));
  let stages = $derived(worker ? workspaceWorkerStages(worker, lifecycleFor(manifests.data, card?.worker_type ?? worker?.worker_type)) : []);
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
  <div class="title">{title}</div>
  {#if worker}
    <div class="label">Stages</div>
    {#each stages as stage (stage.id)}
      <div class="stage {stage.state}"><i class="pip"></i><span>{stage.label}</span>{#if stage.state === "now"}<span class="here">Here</span>{/if}</div>
    {/each}
  {:else if groups}
    {#each groups as group (group.key)}
      <div class="label">{group.label}</div>
      {#each group.cards as ticket (ticket.id)}<div class="worker">{ticket.title}</div>{/each}
    {/each}
  {/if}
</div>
<style>
  .rail-hover-panel { position:fixed; z-index:40; width:min(340px,calc(100vw - 24px)); max-height:74vh; overflow:hidden; padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-lg); background:var(--surface-2); box-shadow:var(--shadow-float); pointer-events:none; opacity:0; transform:translateX(-4px); transition:opacity var(--motion-base) var(--motion-ease),transform var(--motion-base) var(--motion-ease); }
  .visible { opacity:1; transform:none; }
  .instant { transition:none; }
  .kicker { font-size:12px; color:var(--text-faintest); margin-bottom:5px; }
  .title { font-size:var(--type-md); font-weight:600; line-height:1.4; color:var(--text-strong); overflow-wrap:anywhere; }
  .label { margin:var(--space-4) 0 7px; font-size:10.5px; font-weight:600; letter-spacing:0.08em; text-transform:uppercase; color:var(--text-faint); }
  .worker { padding:var(--space-1) 0; font-size:12.5px; line-height:1.4; color:var(--text-default); }
  .stage { display:flex; align-items:center; gap:9px; padding:3px 0; font-size:12.5px; }
  .pip { flex:none; width:7px; height:7px; border-radius:var(--radius-pill); border:1.5px solid var(--text-faintest); box-sizing:border-box; }
  .done .pip { background:var(--accent-done); border-color:var(--accent-done); }
  .done,.todo { color:var(--text-faintest); }
  .now .pip { background:var(--accent-bright); border-color:var(--accent-bright); }
  .now { color:var(--text-strong); font-weight:600; }
  .todo { opacity:0.75; }
  .here { margin-left:auto; font-size:10.5px; font-weight:600; letter-spacing:0.08em; text-transform:uppercase; color:var(--accent-bright); }
</style>
