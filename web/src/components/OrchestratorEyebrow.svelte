<script lang="ts">
  let { project, title, settled = false, rail = false, busy = false, onSettle }: {
    project: string | null; title: string; settled?: boolean; rail?: boolean; busy?: boolean;
    onSettle: () => void;
  } = $props();
</script>
<div class="orchestrator-eyebrow" class:rail>
  <span>{project ?? ""}</span>
  <button type="button" class="settle" disabled={busy} aria-label={`${settled ? "Reopen" : "Settle"} ${title}`} onclick={(event) => { event.stopPropagation(); onSettle(); }}>
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
    {settled ? "Reopen" : "Settle"}
  </button>
</div>
<style>
  .orchestrator-eyebrow { display:flex; align-items:center; gap:var(--space-2); min-height:17px; font-size:12px; font-weight:500; color:var(--text-faintest); }
  .settle { margin-left:auto; display:inline-flex; align-items:center; gap:5px; color:var(--text-faint); white-space:nowrap; background:none; border:0; font:inherit; cursor:pointer; padding:0; transition:opacity var(--motion-fast) var(--motion-ease); }
  .settle svg { width:12px; height:12px; flex:none; }
  .rail .settle { opacity:0; }
  :global(.orchestrator-card:hover) .settle, :global(.orchestrator-card:focus-within) .settle, .settle:focus-visible { opacity:1; }
  .settle:hover { color:var(--text-strong); }
  .settle:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:3px; }
</style>
