<script lang="ts">
  let { project, title, settled = false, rail = false, busy = false, onSettle }: {
    project: string | null; title: string; settled?: boolean; rail?: boolean; busy?: boolean;
    onSettle: () => void;
  } = $props();
</script>
<div class="manager-eyebrow" class:rail>
  <span>{project ?? ""}</span>
  <button type="button" class="settle" disabled={busy} aria-label={`${settled ? "Reopen" : "Settle"} ${title}`} onclick={(event) => { event.stopPropagation(); onSettle(); }}>
    {settled ? "Reopen" : "Settle"}
  </button>
</div>
<style>
  /* The project, in the rail's label voice, over the name it belongs to. The Settle action shares its
     line at the right, shown while the pointer or focus is on the header. */
  .manager-eyebrow { display:flex; align-items:center; gap:var(--space-2); min-height:16px; font-size:var(--rail-label-size); font-weight:var(--weight-small); line-height:16px; color:var(--rail-tone-label); font-variant-numeric:tabular-nums; }
  .settle { margin-left:auto; display:inline-flex; align-items:center; gap:4px; color:var(--rail-tone-label); white-space:nowrap; background:none; border:0; font:inherit; cursor:pointer; padding:0; transition:opacity var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .rail { line-height:16px; }
  .rail > span { min-width:0; overflow:hidden; white-space:nowrap; text-overflow:clip; }
  .rail .settle { flex-shrink:0; opacity:0; }
  :global(.manager-card .identity:hover) .settle, :global(.manager-card .identity:focus-within) .settle, .settle:focus-visible { opacity:1; }
  .settle:hover { color:var(--tone-strong); }
  .settle:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; border-radius:var(--radius-sm); }
</style>
