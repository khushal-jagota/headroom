<script lang="ts">
  import type {ViewWindow} from '../lib/navigation/contracts';
  let {window,label='Rows',onPage}:{window:ViewWindow|undefined;label?:string;onPage:(anchor:string,direction:'before'|'after')=>void}=$props();
</script>
{#if window && (window.previous || window.next)}
  <nav class="child-pages" aria-label={`${label} pages`}>
    <button type="button" disabled={!window.previous} onclick={()=>window?.previous && onPage(window.previous,'before')}>Previous</button>
    <span>{window.members.length} of {window.total}</span>
    <button type="button" disabled={!window.next} onclick={()=>window?.next && onPage(window.next,'after')}>Next</button>
  </nav>
{/if}
<style>
.child-pages{display:flex;align-items:center;gap:var(--space-2);margin-block:var(--space-2);font-size:var(--type-xs);color:var(--text-faint)}
button{font:inherit;color:inherit;background:var(--surface-2);border:var(--border-hairline) solid var(--border-color);border-radius:var(--radius-sm);padding:var(--space-1) var(--space-2)}
</style>
