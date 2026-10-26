<script lang="ts">
  import type { ShellUtility } from "../lib/shellDestinations";
  let { name }: { name: ShellUtility } = $props();
</script>
<svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
  {#if name === "review"}<path d="M3 11h3.5l1.2 2h4.6l1.2-2H17M4.6 4h10.8l1.6 7v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-4l1.6-7Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
  {:else if name === "usage"}<path d="M4 15V9m6 6V4m6 11v-8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
  {:else if name === "ideas"}<path d="M7 14h6m-5 3h4M6 10a5 5 0 1 1 8 0l-1 2H7Z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
  {:else if name === "feedback"}<path d="M3 4h14v10H8l-5 3Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
  {:else if name === "vps"}<rect x="3" y="4" width="14" height="5" rx="1.2" stroke="currentColor" stroke-width="1.4" /><rect x="3" y="11" width="14" height="5" rx="1.2" stroke="currentColor" stroke-width="1.4" /><path d="M6 6.5h.01M6 13.5h.01" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
  {:else}<path d="m8 3-.5 2-1.4.8-2-.5-2 3.4L3.5 10l.1 1.6-1.4 1.4 2 3.4 2-.5 1.4.8.5 2h4l.5-2 1.4-.8 2 .5 2-3.4-1.4-1.4.1-1.6 1.4-1.3-2-3.4-2 .5-1.4-.8-.5-2Z" transform="translate(0 -1)" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" /><circle cx="10" cy="10" r="2.5" stroke="currentColor" stroke-width="1.2" />{/if}
</svg>
