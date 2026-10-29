<script lang="ts">
 import type {Snippet} from 'svelte';
 import {fly} from 'svelte/transition';
 let {checked,total,title,doneTitle='All items done',reduced=false,headingId,compact=false,actions}:{checked:number;total:number;title:string;doneTitle?:string;reduced?:boolean;headingId?:string;compact?:boolean;actions?:Snippet}=$props();
 let allDone=$derived(total>0&&checked===total);
 let heading=$derived(allDone?doneTitle:title);
</script>

<div class="header" class:compact class:reduced>
 <span class="ring" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={checked} aria-valuetext="{checked} of {total} done">
  <svg class="ring-arc" viewBox="0 0 40 40" aria-hidden="true">
   <circle class="ring-track" cx="20" cy="20" r="16"/>
   <g transform="rotate(-90 20 20)"><circle class="ring-fill" cx="20" cy="20" r="16" pathLength="1" stroke-dasharray="1" stroke-dashoffset={total?1-checked/total:1}/></g>
  </svg>
  {#if allDone}<svg class="ring-tick" viewBox="0 0 40 40" aria-hidden="true"><path d="M13.5 20.5 18 25l8.5-9.5" pathLength="1"/></svg>{/if}
 </span>
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
</div>

<style>
 .header{display:grid;grid-template-columns:40px minmax(0,1fr) auto;align-items:center;column-gap:var(--space-3);padding:var(--space-5)}
 .header.compact{padding:0}
 .heading{display:grid;min-width:0;gap:1px}
 h3{margin:0;color:var(--text-strong);font-size:var(--type-md);font-weight:500;line-height:1.4}
 .count{margin:0;color:var(--text-faint);font-size:var(--type-sm);line-height:1.4}
 .count-visual{display:inline-flex;align-items:baseline}
 .roll{position:relative;display:inline-grid;vertical-align:top}
 .roll>span{grid-area:1/1}
 .swap{position:relative;display:inline-grid;max-width:100%;vertical-align:top}
 .swap-sizer{grid-area:1/1;visibility:hidden}
 .swap-text{grid-area:1/1}
 .ring{position:relative;display:grid;width:40px;height:40px;color:var(--text-strong)}
 .ring-arc,.ring-tick{grid-area:1/1;width:40px;height:40px}
 .ring-track{fill:none;stroke:var(--border-color);stroke-width:3}
 .ring-fill{fill:none;stroke:var(--text-strong);stroke-width:3;stroke-linecap:round;transition:stroke-dashoffset var(--motion-slow) var(--motion-ease)}
 .ring-tick path{fill:none;stroke:currentColor;stroke-width:2.25;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1;stroke-dashoffset:0;animation:draw 240ms var(--motion-ease) both}
 .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);border:0}
 @keyframes draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
 .reduced .ring-fill,.reduced .ring-tick path{transition:none;animation:none}
 @media(prefers-reduced-motion:reduce){.ring-fill,.ring-tick path{transition:none;animation:none}}
</style>
