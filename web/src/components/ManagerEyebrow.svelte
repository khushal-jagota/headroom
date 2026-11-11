<script lang="ts">
  import type {GoalResponse} from "../lib/goals/contracts";
  import {goalTagLabel} from "../lib/goals/railPresentation";
  let { project, title, settled = false, rail = false, busy = false, onSettle, goal = null, chief = false, onGoalFocus, onGoalBlur, onGoal }: {
    project: string | null; title: string; settled?: boolean; rail?: boolean; busy?: boolean;
    onSettle: () => void; goal?: GoalResponse | null; chief?: boolean; onGoalFocus?: (event: FocusEvent) => void; onGoalBlur?: (event: FocusEvent) => void; onGoal?: () => void;
  } = $props();
</script>
<div class="manager-eyebrow" class:rail class:chief class:has-goal={Boolean(goal)}>
  <span>{chief ? "All projects" : project ?? ""}</span>
  {#if goal}<button type="button" class="goal-tag" data-goal-status={goal.status} aria-label={`Goals: ${goalTagLabel(goal.status)}`} onclick={(event)=>{event.stopPropagation();onGoal?.();}} onfocus={onGoalFocus} onblur={onGoalBlur}>{goalTagLabel(goal.status)}</button>{/if}
  {#if !chief}
  <button type="button" class="settle" disabled={busy} aria-label={`${settled ? "Reopen" : "Settle"} ${title}`} onclick={(event) => { event.stopPropagation(); onSettle(); }}>
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
    {settled ? "Reopen" : "Settle"}
  </button>{/if}
</div>
<style>
  /* The project, in the rail's label voice, over the name it belongs to. The Settle action (its
     check and its word, as on staging) shares its line at the right, shown while the pointer or
     focus is on the header. */
  .manager-eyebrow { position:relative; display:flex; align-items:center; gap:var(--space-2); min-height:16px; font-size:var(--rail-label-size); font-weight:var(--rail-label-weight); line-height:16px; color:var(--rail-tone-label); font-variant-numeric:tabular-nums; }
  .settle { margin-left:auto; display:inline-flex; align-items:center; gap:5px; color:var(--rail-tone-label); white-space:nowrap; background:none; border:0; font:inherit; cursor:pointer; padding:0; transition:opacity var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .settle svg { width:12px; height:12px; flex:none; }
  .rail { line-height:16px; }
  .rail > span { min-width:0; overflow-wrap:anywhere; }
  .rail.has-goal > span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .rail.has-goal:not(.chief) { column-gap:calc(60px + var(--space-2)); }
  .rail.has-goal.chief > span { flex:1; }
  .rail .settle { position:absolute; right:0; top:0; flex-shrink:0; opacity:0; }
  .goal-tag { margin-left:auto; flex:none; white-space:nowrap; border:0; padding:0; background:none; font:inherit; font-variant-numeric:tabular-nums; cursor:pointer; transition:transform var(--motion-fast) var(--motion-ease); }
  .goal-tag[data-goal-status=bad]{color:var(--accent-error)}.goal-tag[data-goal-status=attention]{color:var(--accent-attention)}.goal-tag[data-goal-status=good]{color:var(--accent-done)}
  :global(.manager-card .identity:hover) .rail:not(.chief) .goal-tag, :global(.manager-card .identity:focus-within) .rail:not(.chief) .goal-tag { transform:translateX(-60px); }
  .goal-tag:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--focus-offset); border-radius:var(--radius-sm); }
  @media(prefers-reduced-motion:reduce){.goal-tag,.settle{transition:none}}
  :global(.manager-card .identity:hover) .settle, :global(.manager-card .identity:focus-within) .settle, .settle:focus-visible { opacity:1; }
  .settle:hover { color:var(--rail-tone-bright); }
  .settle:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; border-radius:var(--radius-sm); }
</style>
