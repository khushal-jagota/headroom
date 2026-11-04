<script lang="ts">
 import type {GoalOutcomeSummary} from '../../lib/goals/contracts';
 let {summary}:{summary:GoalOutcomeSummary}=$props();
</script>

<div class="hit-rate" aria-label="Overall goal hit rate">
 <span>Hit rate <strong>{summary.rate===null?'—':new Intl.NumberFormat('en-US',{style:'percent',maximumFractionDigits:1}).format(summary.rate)}</strong></span>
 <span>{summary.hits} {summary.hits===1?'hit':'hits'} · {summary.misses} {summary.misses===1?'miss':'misses'}</span>
</div>

<style>
 .hit-rate{display:flex;align-items:baseline;flex-wrap:wrap;gap:var(--space-2) var(--space-5);margin-bottom:var(--space-5);font-size:12px;line-height:1.5;color:var(--text-faint)}
 strong{margin-left:var(--space-2);font-size:18px;font-weight:500;font-variant-numeric:tabular-nums;color:var(--text-strong)}
</style>
