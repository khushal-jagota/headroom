<script lang="ts">
 import type {Snippet} from 'svelte';
 import {fly} from 'svelte/transition';
 let {checked,total,title,doneTitle='All items done',reduced=false,headingId,presentation='normal',actions}:{checked:number;total:number;title:string;doneTitle?:string;reduced?:boolean;headingId?:string;presentation?:'normal'|'small';actions?:Snippet}=$props();
 let allDone=$derived(total>0&&checked===total);
 let heading=$derived(allDone?doneTitle:title);
</script>

<div class="header" class:small={presentation==='small'} class:reduced>
 <div class="heading">
   <h3 id={headingId}>
    <span class="sr">{heading}</span>
    <span class="swap" aria-hidden="true">
     <span class="swap-sizer">{title}</span><span class="swap-sizer">{doneTitle}</span>
     {#key heading}<span class="swap-text" in:fly={{y:reduced?0:6,duration:reduced?150:220}} out:fly={{y:reduced?0:-6,duration:reduced?100:150}}>{heading}</span>{/key}
    </span>
   </h3>
  <p class="count">
   <span class="sr">{checked} of {total} done</span>
   <span class="count-visual" aria-hidden="true"><span class="roll">{#key checked}<span in:fly={{y:reduced?0:8,duration:reduced?150:220}} out:fly={{y:reduced?0:-8,duration:reduced?100:140}}>{checked}</span>{/key}</span>&nbsp;of {total} done</span>
  </p>
 </div>
 {@render actions?.()}
 <!-- How far the checklist has come, one segment per step: a measure, not a spinner. -->
 {#if total > 0}<span class="progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={checked} aria-valuetext="{checked} of {total} done">
  {#each Array.from({length: total}, (_, index) => index) as index (index)}<span class="segment" class:done={index < checked}></span>{/each}
 </span>{/if}
</div>

<style>
 /* The checklist's line: its name in body text and its count in small print, the fold's
    chevron at the right, and under them the segmented progress bar across the line. */
 .header{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;column-gap:var(--space-3);padding:var(--page-card-pad)}
 .header.small{padding:var(--space-4)}
 .heading{display:grid;min-width:0;gap:0}
 h3{margin:0;color:var(--tone-primary);font-size:var(--type-body);font-weight:var(--weight-body);line-height:20px}
 .count{margin:0;color:var(--tone-tertiary);font-size:var(--type-small);font-weight:var(--weight-small);line-height:18px;font-variant-numeric:tabular-nums}
 .count-visual{display:inline-flex;align-items:baseline}
 .roll{position:relative;display:inline-grid;vertical-align:top}
 .roll>span{grid-area:1/1}
 .swap{position:relative;display:inline-grid;max-width:100%;vertical-align:top}
 .swap-sizer{grid-area:1/1;visibility:hidden}
 .swap-text{grid-area:1/1}
 .progress{grid-column:1 / -1;display:flex;gap:var(--page-progress-gap);height:var(--page-progress-height);margin-top:var(--space-2)}
 .segment{flex:1;min-width:0;border-radius:var(--page-progress-height);background:var(--page-progress-track);transition:background-color var(--motion-slow) var(--motion-ease)}
 .segment.done{background:var(--accent-done)}
 .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);border:0}
 .reduced .segment{transition:none}
 @media(prefers-reduced-motion:reduce){.segment{transition:none}}
</style>
