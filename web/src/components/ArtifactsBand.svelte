<script lang="ts">
  import ArtifactStrip from "./ArtifactStrip.svelte";
  import type { ArtifactStripItem } from "../lib/artifactStrip";
  let { items }: { items: ArtifactStripItem[] } = $props();
</script>
<details class="artifacts-band" data-artifacts-band>
  <summary>Artifacts<span class="count">{items.length}</span></summary>
  <div class="body">
    {#if items.length}<ArtifactStrip {items} />{:else}<p class="empty">No artifacts yet.</p>{/if}
  </div>
</details>
<style>
  .artifacts-band { line-height:1.6; border-top:1px solid var(--border-color); border-bottom:1px solid var(--border-color); }
  summary { display:flex; align-items:center; gap:var(--space-3); padding:14px var(--space-2); margin-inline:calc(-1 * var(--space-2)); border-radius:var(--radius-sm); font-size:13px; font-weight:600; color:var(--text-faint); cursor:pointer; list-style:none; }
  summary::-webkit-details-marker { display:none; }
  summary:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .count { margin-left:auto; font-weight:400; color:var(--text-faintest); }
  .body { padding-bottom:var(--space-4); }
  .empty { margin:var(--space-2) 0; font-size:13px; color:var(--text-faintest); }
  .body :global(.artifact-strip) { display:block; padding:0; margin:0; border:0; }
  .body :global(.artifact-strip-label) { display:none; }
  .body :global(.artifact-strip-items) { display:flex; flex-direction:column; gap:0; }
  .body :global(.artifact-chip) { width:calc(100% + 2 * var(--space-2)); margin-inline:calc(-1 * var(--space-2)); display:flex; justify-content:space-between; gap:var(--space-3); padding:9px var(--space-2); border:0; border-radius:var(--radius-sm); background:transparent; font-size:15px; color:var(--text-muted); }
  .body :global(.artifact-chip:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .body :global(.artifact-chip small) { font-size:13px; text-transform:none; letter-spacing:normal; color:var(--text-faintest); }
  .body :global(.artifact-chip--overflow) { display:flex; }
  .body :global(.artifact-strip-more) { display:none; }
  .body :global(.artifact-chip--nested) { padding-left:var(--space-5); }
</style>
