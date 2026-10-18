<script lang="ts">
  import type { BackendUsageWindow } from "../../lib/conversation/wire";
  import { remainingPercent } from "../../lib/conversation/usagePresentation";
  import { timestampLabel } from "./usagePresentation";
  let { windows, observedAt, now }: {
    windows: readonly BackendUsageWindow[]; observedAt: string | null; now: number;
  } = $props();
</script>
<div class="allowances" data-allowance-windows>
  {#if !windows.length}<div class="absent"><span>—</span> No allowance reported</div>{/if}
  {#each windows as window (`${window.kind}:${window.model_id ?? ""}`)}
    {@const expired = Date.parse(window.resets_at) <= now}
    {@const remaining = remainingPercent(window.used_percent)}
    <article class="window-card" class:expired data-allowance-window={window.kind}>
      <div class="window-copy">
        <h3>{window.kind === "five_hour" ? "5-hour limit" : "7-day limit"}{window.model_id ? ` · ${window.model_id}` : ""}</h3>
        <div class="window-total"><strong>{remaining}%</strong><span>{expired ? "cached" : "left¹"}</span></div>
      </div>
      <details class="window-details">
        <summary aria-label={`${remaining}% left at observation. ${expired ? "Reset time passed. " : ""}Allowance details`}>
          <span class="bar-fill" style:width={`${remaining}%`} aria-hidden="true"></span>
          <span class="bar-value">{remaining}%</span><span class="reset">{expired ? "Reset passed" : "↻"} · {timestampLabel(window.resets_at)}</span>
        </summary>
        <dl>
          <div><dt>Left at observation</dt><dd>{remaining}%</dd></div>
          <div><dt>Used</dt><dd>{window.used_percent}%</dd></div>
          <div><dt>Observed</dt><dd>{timestampLabel(observedAt)}</dd></div>
          <div><dt>Reset</dt><dd>{timestampLabel(window.resets_at)}{expired ? " · passed" : ""}</dd></div>
        </dl>
      </details>
      <div class="window-foot"><span>{expired ? "Current allowance unavailable" : "¹ At observation"}</span><span>Observed {timestampLabel(observedAt)}</span></div>
    </article>
  {/each}
</div>
<style>
  .allowances { display:grid; gap:var(--space-3); }
  .window-card { display:grid; grid-template-columns:11rem minmax(0,1fr); align-items:center; gap:var(--space-2) var(--space-5); padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); }
  .window-copy { grid-row:span 2; min-width:0; }
  h3 { margin:0 0 var(--space-1); font:500 var(--type-sm) var(--font-ui); color:var(--text-default); overflow-wrap:anywhere; }
  .window-total { display:flex; align-items:baseline; gap:var(--space-2); }
  strong { font-size:var(--type-serif-display); font-weight:600; line-height:1.2; color:var(--text-strong); font-variant-numeric:tabular-nums; }
  .window-total>span { font-size:var(--type-sm); color:var(--text-faint); }
  .window-details { min-width:0; }
  summary { position:relative; display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); min-height:var(--space-6); padding:var(--space-1) var(--space-2); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); background:var(--surface-recessed); list-style:none; cursor:pointer; font-size:var(--type-xs); font-variant-numeric:tabular-nums; }
  summary::-webkit-details-marker { display:none; }
  summary:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:var(--space-1); }
  .bar-fill { position:absolute; inset:0 auto 0 0; background:var(--text-faintest); opacity:.3; border-radius:var(--radius-sm); }
  .bar-value,.reset { position:relative; z-index:1; }.bar-value { font-weight:600; }.reset { background:var(--surface-1); padding:0 var(--space-1); border-radius:var(--radius-sm); color:var(--text-muted); text-align:right; }
  .expired summary { border-style:dashed; }.expired strong { color:var(--text-muted); }.expired .bar-fill { opacity:.14; }
  .window-foot { display:flex; justify-content:space-between; gap:var(--space-2); font-size:var(--type-xs); color:var(--text-faintest); }.window-foot span:last-child { text-align:right; }
  dl { padding:var(--space-3); margin:var(--space-2) 0 0; background:var(--surface-recessed); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); font-size:var(--type-xs); }
  dl>div { display:flex; justify-content:space-between; gap:var(--space-3); padding:var(--space-1) 0; }dt { color:var(--text-faint); }dd { margin:0; text-align:right; }
  .absent { display:flex; align-items:center; gap:var(--space-3); padding:var(--space-4); color:var(--text-faintest); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-md); font-size:var(--type-xs); }.absent span { font-size:var(--type-lg); }
  @media(max-width:580px) { .window-card { grid-template-columns:minmax(0,1fr); gap:var(--space-3); }.window-copy { grid-row:auto; display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); }h3 { margin:0; }.window-total>span { font-size:var(--type-xs); }.window-total { flex:none; }.window-foot { font-size:var(--type-xs); } }
</style>
