<script lang="ts">
  import type { ShellUtility } from "../lib/shellDestinations";
  let { name }: { name: ShellUtility } = $props();
</script>
<!-- One icon family: a 24-unit grid, 1.6 strokes, round caps and joins, and the same optical
     weight for every glyph (two strokes of detail at most, no nested rings). -->
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  {#if name === "review"}<path d="M3.75 13h4.5l1.5 2.5h4.5l1.5-2.5h4.5" /><path d="M6.4 5.25h11.2a1 1 0 0 1 .96.72L20.25 12.5v5a1.75 1.75 0 0 1-1.75 1.75h-13a1.75 1.75 0 0 1-1.75-1.75v-5L5.44 5.97a1 1 0 0 1 .96-.72Z" />
  {:else if name === "usage"}<path d="M4.5 19.25h15" /><path d="M7.5 15.5v-3.5M12 15.5V6.5M16.5 15.5V9.75" />
  {:else if name === "ideas"}<path d="M9.75 20.25h4.5" /><path d="M8.6 14.6A6 6 0 1 1 15.4 14.6c-.55.45-.9 1.05-.9 1.75v.9h-5v-.9c0-.7-.35-1.3-.9-1.75Z" />
  {:else if name === "goals"}<circle cx="12" cy="12" r="8.25" /><circle cx="12" cy="12" r="3.25" />
  {:else if name === "vps"}<rect x="4" y="4.75" width="16" height="14.5" rx="2.75" /><path d="M4 12h16M7.75 8.4h.01M7.75 15.6h.01" />
  {:else}<path d="M4 8h9.5M18.5 8H20M4 16h1.5M10.5 16H20" /><circle cx="16" cy="8" r="2.25" /><circle cx="8" cy="16" r="2.25" />{/if}
</svg>
