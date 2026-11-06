<script lang="ts">
  import type { ShellUtility } from "../lib/shellDestinations";
  let { name }: { name: ShellUtility } = $props();
</script>
<!-- One icon family: a 24-unit grid, 1.6 strokes, round caps and joins. -->
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  {#if name === "review"}<path d="M3.5 13h4.25l1.5 2.5h5.5l1.5-2.5h4.25" /><path d="M6.2 5h11.6a1 1 0 0 1 .95.68L20.5 13v5a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18v-5l1.75-7.32A1 1 0 0 1 6.2 5Z" />
  {:else if name === "usage"}<path d="M5.5 19.5v-6M12 19.5v-15M18.5 19.5v-9.5" />
  {:else if name === "ideas"}<path d="M9.5 17.5h5M10.5 20.5h3" /><path d="M8.6 14.6A6 6 0 1 1 15.4 14.6c-.55.45-.9 1.05-.9 1.75v1.15h-5v-1.15c0-.7-.35-1.3-.9-1.75Z" />
  {:else if name === "goals"}<circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="0.75" fill="currentColor" />
  {:else if name === "vps"}<rect x="3.5" y="4" width="17" height="7" rx="2" /><rect x="3.5" y="13" width="17" height="7" rx="2" /><path d="M7.5 7.5h.01M7.5 16.5h.01" stroke-width="2" />
  {:else}<path d="M4 7h9M17 7h3M4 17h3M11 17h9M4 12h4.5M12.5 12H20" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /><circle cx="10.5" cy="12" r="2" />{/if}
</svg>
